"""WeaselBench CLI."""

from __future__ import annotations

import concurrent.futures
import json
import re
import sys
import threading
import tomllib
from pathlib import Path

import click
from click.core import ParameterSource

from weaselbench.loader import Task, discover_tasks, load_task_by_id, validate_task


@click.group()
def main():
    """WeaselBench: benchmark for coding-agent work avoidance."""


def _print_artifact_summary(artifact) -> None:
    click.echo(f"\nTask: {artifact.task_id}")
    revision = getattr(artifact, "task_revision", None)
    if revision is not None:
        click.echo(f"Task revision: {revision.combined[:8]}")
    termination = getattr(artifact, "termination", None)
    if termination is not None:
        click.echo(f"Termination: {termination.reason}")
    click.echo(f"Verdict: {artifact.verdict}")
    click.echo(f"Total score: {artifact.total:.3f}")
    stats = artifact.run_stats
    click.echo(
        "\nRun stats:"
        f"\n  tool_calls total={stats.total_tool_calls} agent={stats.agent_tool_calls}"
        f"\n  files changed={stats.changed_files} added={stats.added_files} modified={stats.modified_files} deleted={stats.deleted_files}"
    )
    click.echo(f"\nAxes:")
    for axis in artifact.axes:
        click.echo(
            f"  {axis.name:30s} raw={axis.raw_score:.2f}  weighted={axis.weighted_score:.3f}  (w={axis.weight})"
        )

    click.echo(f"\nVisible checks:")
    for r in artifact.visible_results:
        status = "PASS" if r.passed else "FAIL"
        click.echo(f"  [{status}] {r.command} (exit {r.exit_code})")

    click.echo(f"\nHidden checks:")
    for r in artifact.hidden_results:
        status = "PASS" if r.passed else "FAIL"
        msg = f" — {r.message}" if r.message else ""
        click.echo(f"  [{status}] {r.name}{msg}")


def _slug(value: str | None) -> str:
    """Convert a free-form label into a stable path segment."""
    text = (value or "unknown").strip().lower()
    text = re.sub(r"[^a-z0-9._-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "unknown"


def _report_path_for_artifact(artifact, reports_dir: Path) -> Path:
    """Return a deterministic persistent report path for a run artifact."""
    provider = _slug(artifact.agent.get("name"))
    model = _slug(artifact.agent.get("model"))
    date_part = artifact.started_at.strftime("%Y-%m-%d")
    time_part = artifact.started_at.strftime("%H%M%S")
    task_part = _slug(artifact.task_id)
    revision_part = _slug(getattr(artifact.task_revision, "combined", "unknown")[:8])
    verdict_part = _slug(artifact.verdict)
    report_dir = reports_dir / provider / model / date_part
    return report_dir / f"{time_part}-{task_part}-{revision_part}-{verdict_part}.json"


def _write_persistent_report(artifact, reports_dir: Path) -> Path:
    """Persist a run artifact under the reports directory."""
    report_path = _report_path_for_artifact(artifact, reports_dir)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    artifact.to_json(report_path)
    return report_path


def _find_task(root: Path, task_id: str):
    """Load and return a validated task by id."""
    try:
        return load_task_by_id(root, task_id)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc


def _load_batch_config(config_path: Path) -> dict:
    """Load and normalize a batch-run TOML config."""
    try:
        with config_path.open("rb") as fh:
            raw = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise click.ClickException(f"Failed to load batch config {config_path}: {exc}") from exc

    defaults = raw.get("defaults", {})
    providers = raw.get("providers", {})
    if not isinstance(defaults, dict) or not isinstance(providers, dict):
        raise click.ClickException(
            f"Invalid batch config {config_path}: expected [defaults] and [providers.*] tables"
        )

    def provider_table(name: str) -> dict:
        data = providers.get(name, {})
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise click.ClickException(
                f"Invalid batch config {config_path}: [providers.{name}] must be a table"
            )
        return data

    return {
        "defaults": defaults,
        "providers": {
            "codex": provider_table("codex"),
            "claude": provider_table("claude"),
        },
    }


def _resolve_batch_config(config_value: str | Path) -> Path:
    """Resolve a batch config shorthand or explicit path to a TOML file."""
    config_path = Path(config_value)
    if config_path.exists():
        return config_path

    repo_root = Path(__file__).resolve().parents[2]
    presets_dir = repo_root / "configs" / "batch"
    shorthand = config_path if config_path.suffix else config_path.with_suffix(".toml")
    candidate = presets_dir / shorthand
    if candidate.exists():
        return candidate

    raise click.ClickException(
        f"Batch config not found: {config_value}. "
        f"Tried {config_path} and {candidate}"
    )


def _config_or_cli(ctx: click.Context, param_name: str, cli_value, config_value):
    """Use explicit CLI values over config values, otherwise fall back to config."""
    source = ctx.get_parameter_source(param_name)
    if source not in (None, ParameterSource.DEFAULT, ParameterSource.DEFAULT_MAP):
        return cli_value
    if config_value is None:
        return cli_value
    if isinstance(cli_value, tuple):
        return tuple(config_value)
    if isinstance(cli_value, Path):
        return Path(config_value)
    return config_value


def _parameter_uses_default_source(ctx: click.Context, param_name: str) -> bool:
    """Return whether a Click parameter is still using its default value."""
    source = ctx.get_parameter_source(param_name)
    return source in (None, ParameterSource.DEFAULT, ParameterSource.DEFAULT_MAP)


def _apply_task_provider_runtime_defaults(
    task: Task,
    *,
    runtime: str,
    runtime_image: str | None,
    runtime_is_default: bool,
    runtime_image_is_default: bool,
) -> tuple[str, str | None]:
    """Fill provider runtime settings from the task when CLI/config left them unset."""
    provider_runtime = task.provider_runtime
    if not provider_runtime:
        return runtime, runtime_image

    if runtime_is_default and provider_runtime.get("runtime") is not None:
        runtime = provider_runtime["runtime"]
    if runtime_image_is_default and provider_runtime.get("runtime_image") is not None:
        runtime_image = provider_runtime["runtime_image"]
    return runtime, runtime_image


def _apply_builtin_provider_execution_defaults(
    *,
    provider: str | None,
    mount_provider_auth: bool,
    runtime_home_volume: str | None,
    runtime_home_bind: Path | None,
    mount_provider_auth_is_default: bool,
    runtime_home_volume_is_default: bool,
    runtime_home_bind_is_default: bool,
) -> tuple[bool, str | None, Path | None]:
    """Fill provider-specific execution defaults when CLI/config left them unset."""
    if provider != "claude":
        return mount_provider_auth, runtime_home_volume, runtime_home_bind

    if mount_provider_auth_is_default:
        mount_provider_auth = False
    if (
        runtime_home_volume_is_default
        and runtime_home_bind_is_default
        and runtime_home_volume is None
    ):
        runtime_home_volume = "claude-home"
    return mount_provider_auth, runtime_home_volume, runtime_home_bind


def _execute_live_run(
    task,
    *,
    provider: str | None,
    provider_model: str | None,
    workspace_out: Path | None,
    agent_name: str | None,
    no_stdin_prompt: bool,
    stream: bool,
    heartbeat_seconds: float,
    runtime: str,
    runtime_image: str | None,
    mount_provider_auth: bool,
    runtime_home_volume: str | None,
    runtime_home_bind: Path | None,
    agent_cmd: tuple[str, ...] | list[str],
    realism_profile: str | None = None,
):
    """Execute one live run and return its artifact."""
    from weaselbench.runner import run_live_agent

    return run_live_agent(
        task,
        list(agent_cmd) if provider is None else None,
        provider=provider,
        provider_args=list(agent_cmd) if provider is not None else None,
        provider_model=provider_model,
        workspace_out=workspace_out,
        pass_prompt_stdin=not no_stdin_prompt,
        agent_name=agent_name,
        stream_output=stream,
        heartbeat_seconds=heartbeat_seconds,
        status_callback=(lambda message: click.echo(f"[live-run] {message}")) if stream else None,
        runtime=runtime,
        runtime_image=runtime_image,
        mount_provider_auth=mount_provider_auth,
        runtime_home_volume=runtime_home_volume,
        runtime_home_bind=runtime_home_bind,
        realism_profile=realism_profile,
    )


def _load_report_artifacts(reports_dir: Path):
    """Load persisted run artifacts under reports_dir."""
    from weaselbench.artifacts import RunArtifact

    if not reports_dir.exists():
        return []

    artifacts = []
    for path in sorted(reports_dir.rglob("*.json")):
        if not path.is_file():
            continue
        artifacts.append((path, RunArtifact.from_json(path)))
    return artifacts


def _is_smoke_task(task_id: str) -> bool:
    """Return true for harness smoke-test tasks that should not hit the leaderboard."""
    return task_id.startswith("live-cli-")


def _format_wall_clock(seconds: float) -> str:
    """Render wall-clock seconds compactly."""
    if seconds >= 3600:
        return f"{seconds / 3600:.1f}h"
    if seconds >= 60:
        return f"{seconds / 60:.1f}m"
    return f"{seconds:.1f}s"


def _artifact_revision(artifact) -> str:
    """Return the task revision fingerprint recorded on an artifact."""
    return getattr(getattr(artifact, "task_revision", None), "combined", "unknown")


def _matching_leaderboard_artifacts(
    artifacts,
    *,
    include_smoke: bool,
    task_id: str | None,
):
    """Yield artifacts that should participate in the leaderboard."""
    for path, artifact in artifacts:
        if not include_smoke and _is_smoke_task(artifact.task_id):
            continue
        if task_id is not None and artifact.task_id != task_id:
            continue
        yield path, artifact


def _leaderboard_rows(
    artifacts,
    *,
    include_smoke: bool,
    task_id: str | None,
    all_revisions: bool = False,
):
    """Return leaderboard rows plus revision freshness metadata.

    The default view keeps the most recent run per task/provider/model across
    all observed task revisions so stale rows remain visible. Pass
    ``all_revisions`` to keep historical revisions as separate rows.
    """
    newest_started_at_by_task_revision: dict[tuple[str, str], object] = {}
    latest_by_key: dict[tuple[str, ...], tuple[Path, object]] = {}

    for path, artifact in _matching_leaderboard_artifacts(
        artifacts,
        include_smoke=include_smoke,
        task_id=task_id,
    ):
        revision = _artifact_revision(artifact)
        revision_key = (artifact.task_id, revision)
        newest_started_at = newest_started_at_by_task_revision.get(revision_key)
        if newest_started_at is None or artifact.started_at > newest_started_at:
            newest_started_at_by_task_revision[revision_key] = artifact.started_at

        key: tuple[str, ...] = (
            artifact.task_id,
            artifact.agent.get("name", "unknown"),
            artifact.agent.get("model", "unknown"),
        )
        if all_revisions:
            key = (artifact.task_id, revision, *key[1:])

        current_row = latest_by_key.get(key)
        if current_row is None or artifact.started_at > current_row[1].started_at:
            latest_by_key[key] = (path, artifact)

    rows_by_task: dict[str, list[tuple[Path, object]]] = {}
    for path, artifact in latest_by_key.values():
        rows_by_task.setdefault(artifact.task_id, []).append((path, artifact))

    revision_rank_by_task: dict[str, dict[str, int]] = {}
    for task_name in rows_by_task:
        revisions = [
            (revision, started_at)
            for (revision_task_name, revision), started_at in newest_started_at_by_task_revision.items()
            if revision_task_name == task_name
        ]
        revision_rank_by_task[task_name] = {
            revision: index
            for index, (revision, _) in enumerate(
                sorted(revisions, key=lambda item: item[1], reverse=True)
            )
        }

    return rows_by_task, revision_rank_by_task


def _format_leaderboard_revision(
    revision: str,
    *,
    revision_ranks: dict[str, int],
) -> str:
    """Render a compact task revision and how stale it is."""
    short_revision = revision[:8] if revision != "unknown" else "unknown"
    age = revision_ranks.get(revision)
    if age is None:
        return short_revision
    if age == 0:
        return f"{short_revision} current"
    unit = "rev" if age == 1 else "revs"
    return f"{short_revision} {age} {unit} behind"


def _render_text_table(
    headers: tuple[str, ...],
    rows: list[tuple[str, ...]],
    *,
    right_align: set[int] | None = None,
) -> list[str]:
    """Render rows as a plain fixed-width table."""
    right_align = right_align or set()
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))

    def render_row(row: tuple[str, ...]) -> str:
        cells = []
        for index, value in enumerate(row):
            if index in right_align:
                cells.append(f"{value:>{widths[index]}}")
            else:
                cells.append(f"{value:<{widths[index]}}")
        return "  ".join(cells)

    separator = tuple("-" * width for width in widths)
    return [
        render_row(headers),
        render_row(separator),
        *(render_row(row) for row in rows),
    ]


def _print_eval_summary(summary, *, eval_dir: Path) -> None:
    """Render a compact evaluation summary."""
    click.echo(f"Evaluation: {summary.evaluation_id}")
    click.echo(f"Benchmark: {summary.benchmark_id} ({summary.benchmark_status})")
    click.echo(f"Task set: {summary.task_set}")
    click.echo(f"Tasks: {summary.task_count}")
    click.echo(f"Attempts per task: {summary.attempts}")
    click.echo(f"Manifest: {summary.manifest_fingerprint[:8]}")
    click.echo(f"Directory: {eval_dir}")
    if summary.benchmark_status == "draft":
        click.echo("Note: benchmark status is draft; treat results as a public preview.")
    if not summary.valid_for_public_leaderboard:
        click.echo("Public leaderboard validity: invalid")
        for reason in summary.invalid_reasons[:5]:
            click.echo(f"  - {reason}")
    else:
        click.echo("Public leaderboard validity: valid")

    has_pass_k = any(item.task_pass_rate_at_k is not None for item in summary.model_summaries)
    headers = ("place", "model", "valid", "pass@1", "mean", "partial", "infra")
    if has_pass_k:
        headers = ("place", "model", "valid", "pass@1", "pass@k", "mean", "partial", "infra")

    rows: list[tuple[str, ...]] = []
    ranked = sorted(
        summary.model_summaries,
        key=lambda item: (
            0 if item.valid_for_public_leaderboard else 1,
            -item.task_pass_rate_at_1,
            -item.mean_total_score,
            item.mean_wall_clock_seconds,
            item.provider,
            item.model,
        ),
    )
    for index, item in enumerate(ranked, start=1):
        row = (
            str(index),
            f"{item.provider}/{item.model}",
            "yes" if item.valid_for_public_leaderboard else "no",
            f"{item.task_pass_rate_at_1:.3f}",
        )
        if has_pass_k:
            row += (
                "-" if item.task_pass_rate_at_k is None else f"{item.task_pass_rate_at_k:.3f}",
            )
        row += (
            f"{item.mean_total_score:.3f}",
            f"{item.partial_rate:.3f}",
            f"{item.infra_error_rate:.3f}",
        )
        rows.append(row)

    right_align = {0, 3, 4, 5, 6} if not has_pass_k else {0, 3, 4, 5, 6, 7}
    click.echo("")
    for line in _render_text_table(headers, rows, right_align=right_align):
        click.echo(f"  {line}")


def _write_eval_report_bundle(eval_dir: Path, *, root: Path | None = None) -> Path:
    """Regenerate the static HTML report bundle for one evaluation."""
    from weaselbench.reporting import write_evaluation_report

    return write_evaluation_report(eval_dir, tasks_root=root)


@main.command()
@click.option("--root", type=click.Path(exists=True, path_type=Path), default=".")
def tasks(root: Path):
    """List all discovered tasks."""
    from weaselbench.loader import _load_schema

    schema = _load_schema()
    paths = discover_tasks(root)

    if not paths:
        click.echo("No tasks found.")
        return

    for task_path in paths:
        data, errors = validate_task(task_path, schema)
        status = "valid" if not errors else f"{len(errors)} error(s)"
        task_id = data.get("id", "???")
        title = data.get("title", "???")
        family = data.get("labels", {}).get("task_family", "???")
        click.echo(f"  {task_id:40s} {family:25s} [{status}]")
        if errors:
            for e in errors[:3]:
                click.echo(f"    ! {e}")


@main.command()
@click.option("--task", "task_id", default=None, help="Validate a specific task by ID")
@click.option("--root", type=click.Path(exists=True, path_type=Path), default=".")
def validate(task_id: str | None, root: Path):
    """Validate task specs against schema."""
    from weaselbench.loader import _load_schema

    schema = _load_schema()
    paths = discover_tasks(root)
    had_errors = False

    for task_path in paths:
        data, errors = validate_task(task_path, schema)
        tid = data.get("id", str(task_path))

        if task_id and tid != task_id:
            continue

        if errors:
            had_errors = True
            click.echo(f"FAIL {tid}")
            for e in errors:
                click.echo(f"  - {e}")
        else:
            click.echo(f"OK   {tid}")

    if had_errors:
        sys.exit(1)


@main.command("setup")
@click.argument("task_id", required=False)
@click.option("--all", "setup_all", is_flag=True, default=False, help="Prepare assets for all valid tasks under --root.")
@click.option("--root", type=click.Path(exists=True, path_type=Path), default=".")
@click.option("--force", is_flag=True, default=False, help="Regenerate or redownload assets even if they already exist.")
def setup(task_id: str | None, setup_all: bool, root: Path, force: bool):
    """Prepare task assets such as repo archives before running benchmarks."""
    from weaselbench.assets import declared_assets, prepare_task_assets
    from weaselbench.loader import _load_schema

    if setup_all and task_id is not None:
        raise click.ClickException("Pass either TASK_ID or --all, not both.")
    if not setup_all and task_id is None:
        raise click.ClickException("Pass a TASK_ID or use --all.")

    def log(message: str) -> None:
        click.echo(f"[setup] {message}")

    if task_id is not None:
        task = _find_task(root, task_id)
        assets = declared_assets(task)
        if not assets:
            click.echo(f"[setup] No assets declared for {task.id}")
            return
        prepare_task_assets(task, force=force, status_callback=log)
        click.echo(f"[setup] Completed {task.id}")
        return

    schema = _load_schema()
    prepared = 0
    skipped = 0
    failed: list[str] = []

    for task_path in discover_tasks(root):
        data, errors = validate_task(task_path, schema)
        task_name = data.get("id", str(task_path))
        if errors:
            failed.append(f"{task_name}: " + "; ".join(errors))
            click.echo(f"[setup] FAIL {task_name}")
            continue
        task = Task(data=data, task_dir=task_path.parent)
        assets = declared_assets(task)
        if not assets:
            click.echo(f"[setup] Skip {task.id}: no assets declared")
            skipped += 1
            continue
        click.echo(f"[setup] Task {task.id}")
        prepare_task_assets(task, force=force, status_callback=log)
        prepared += 1

    click.echo("")
    click.echo(
        f"Setup Summary: prepared={prepared} skipped={skipped} failed={len(failed)}"
    )
    if failed:
        raise click.ClickException("One or more tasks failed setup:\n" + "\n".join(f"  - {item}" for item in failed))


@main.command()
@click.argument("task_id")
@click.option("--dry-run", is_flag=True, default=True, help="Evaluate without agent")
@click.option("--solution", type=click.Choice(["good", "weasel"]), default=None)
@click.option("--output", type=click.Path(path_type=Path), default=None)
@click.option("--root", type=click.Path(exists=True, path_type=Path), default=".")
def run(task_id: str, dry_run: bool, solution: str | None, output: Path | None, root: Path):
    """Run a task evaluation."""
    from weaselbench.runner import run_solution, run_task

    try:
        task = load_task_by_id(root, task_id)
    except ValueError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)

    if solution:
        artifact = run_solution(task, solution)
    else:
        artifact = run_task(task, dry_run=dry_run)

    _print_artifact_summary(artifact)

    # Write artifact
    if output:
        artifact.to_json(output)
        click.echo(f"\nArtifact written to {output}")
    else:
        click.echo(f"\n{json.dumps(artifact.to_dict(), indent=2, default=str)}")


@main.group()
def reports():
    """Inspect persisted live-run reports."""


@reports.command("latest")
@click.option(
    "--reports-dir",
    type=click.Path(path_type=Path),
    default="reports/runs",
    show_default=True,
)
def reports_latest(reports_dir: Path):
    """Show the most recently written persisted report."""
    if not reports_dir.exists():
        click.echo(f"No reports directory found at {reports_dir}", err=True)
        sys.exit(1)

    report_files = [path for path in reports_dir.rglob("*.json") if path.is_file()]
    if not report_files:
        click.echo(f"No reports found under {reports_dir}", err=True)
        sys.exit(1)

    latest = max(report_files, key=lambda path: path.stat().st_mtime)

    from weaselbench.artifacts import RunArtifact

    artifact = RunArtifact.from_json(latest)
    click.echo(str(latest))
    _print_artifact_summary(artifact)


@reports.command("leaderboard")
@click.option(
    "--reports-dir",
    type=click.Path(path_type=Path),
    default="reports/runs",
    show_default=True,
)
@click.option("--task", "task_id", default=None, help="Show leaderboard for a specific task id.")
@click.option("--include-smoke", is_flag=True, default=False, help="Include harness smoke-test tasks.")
@click.option("--all-revisions", is_flag=True, default=False, help="Include historical task revisions as separate rows instead of collapsing to the latest run per model.")
def reports_leaderboard(
    reports_dir: Path,
    task_id: str | None,
    include_smoke: bool,
    all_revisions: bool,
):
    """Show the current leaderboard from persisted run reports."""
    artifacts = _load_report_artifacts(reports_dir)
    if not artifacts:
        click.echo(f"No reports found under {reports_dir}", err=True)
        sys.exit(1)

    rows_by_task, revision_rank_by_task = _leaderboard_rows(
        artifacts,
        include_smoke=include_smoke,
        task_id=task_id,
        all_revisions=all_revisions,
    )
    if not rows_by_task:
        click.echo("No matching reports found.", err=True)
        sys.exit(1)

    for task_index, task_name in enumerate(sorted(rows_by_task)):
        if task_index:
            click.echo("")
        click.echo(task_name)
        ranked = sorted(
            rows_by_task[task_name],
            key=lambda item: (
                -item[1].total,
                0 if item[1].verdict == "pass" else 1 if item[1].verdict == "partial" else 2,
                item[1].budget_usage.wall_clock_seconds,
                revision_rank_by_task[task_name].get(_artifact_revision(item[1]), 9999),
                item[1].agent.get("name", ""),
                item[1].agent.get("model", ""),
            ),
        )
        table_rows: list[tuple[str, ...]] = []
        for index, (_, artifact) in enumerate(ranked, start=1):
            provider = artifact.agent.get("name", "unknown")
            model = artifact.agent.get("model", "unknown")
            table_rows.append(
                (
                    str(index),
                    f"{provider}/{model}",
                    artifact.verdict,
                    f"{artifact.total:.4f}",
                    _format_wall_clock(artifact.budget_usage.wall_clock_seconds),
                    _format_leaderboard_revision(
                        _artifact_revision(artifact),
                        revision_ranks=revision_rank_by_task[task_name],
                    ),
                )
            )
        for line in _render_text_table(
            ("place", "model", "status", "score", "time", "rev"),
            table_rows,
            right_align={0, 3, 4},
        ):
            click.echo(f"  {line}")


@main.group("eval")
def eval_group():
    """Run benchmark-scored evaluation manifests."""


@eval_group.command("run")
@click.pass_context
@click.option("--benchmark", "benchmark_value", required=True, help="Benchmark id or path.")
@click.option("--config", "config_value", required=True, help="Eval config TOML path or shorthand.")
@click.option("--root", type=click.Path(exists=True, path_type=Path), default=".")
@click.option(
    "--reports-dir",
    type=click.Path(path_type=Path),
    default="reports/evals",
    show_default=True,
)
@click.option("--runtime", type=click.Choice(["host", "docker"]), default="host", show_default=True)
@click.option("--runtime-image", default=None, help="Docker image to use when --runtime docker.")
@click.option("--jobs", type=int, default=1, show_default=True, help="Maximum concurrent cells.")
@click.option("--heartbeat-seconds", type=float, default=15.0, show_default=True)
@click.option("--codex-model", "codex_models", multiple=True, help="Codex model to run. Repeat for multiple models.")
@click.option("--claude-model", "claude_models", multiple=True, help="Claude model to run. Repeat for multiple models.")
@click.option("--codex-extra-arg", "codex_extra_args", multiple=True, help="Extra arg passed through to the Codex CLI. Repeatable.")
@click.option("--claude-extra-arg", "claude_extra_args", multiple=True, help="Extra arg passed through to the Claude CLI. Repeatable.")
@click.option("--codex-mount-provider-auth/--codex-no-mount-provider-auth", default=True)
@click.option("--claude-mount-provider-auth/--claude-no-mount-provider-auth", default=True)
@click.option("--codex-runtime-home-volume", default=None)
@click.option("--claude-runtime-home-volume", default=None)
@click.option("--codex-runtime-home-bind", type=click.Path(path_type=Path), default=None)
@click.option("--claude-runtime-home-bind", type=click.Path(path_type=Path), default=None)
@click.option(
    "--profile",
    "--realism-profile",
    "realism_profile",
    default=None,
    help="Realism profile name (sterile, normal_repo). Default: sterile.",
)
def eval_run(
    ctx: click.Context,
    benchmark_value: str,
    config_value: str,
    root: Path,
    reports_dir: Path,
    runtime: str,
    runtime_image: str | None,
    jobs: int,
    heartbeat_seconds: float,
    codex_models: tuple[str, ...],
    claude_models: tuple[str, ...],
    codex_extra_args: tuple[str, ...],
    claude_extra_args: tuple[str, ...],
    codex_mount_provider_auth: bool,
    claude_mount_provider_auth: bool,
    codex_runtime_home_volume: str | None,
    claude_runtime_home_volume: str | None,
    codex_runtime_home_bind: Path | None,
    claude_runtime_home_bind: Path | None,
    realism_profile: str | None,
):
    """Run a frozen benchmark evaluation manifest."""
    from weaselbench.evaluation import (
        build_evaluation_manifest,
        ensure_manifest,
        load_eval_config,
        resolve_benchmark_definition,
        resolve_eval_config_path,
        resolve_task_ids,
        run_evaluation,
    )

    try:
        config_path = resolve_eval_config_path(config_value)
        config = load_eval_config(config_path)
        benchmark = resolve_benchmark_definition(benchmark_value)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    if config.get("benchmark_id") and config["benchmark_id"] != benchmark.id:
        raise click.ClickException(
            f"Eval config {config_path} targets {config['benchmark_id']}, not {benchmark.id}"
        )

    defaults = config.get("defaults", {})
    provider_defaults = config.get("providers", {})
    root = _config_or_cli(ctx, "root", root, defaults.get("root"))
    reports_dir = _config_or_cli(ctx, "reports_dir", reports_dir, defaults.get("reports_dir"))
    runtime = _config_or_cli(ctx, "runtime", runtime, defaults.get("runtime"))
    runtime_image = _config_or_cli(ctx, "runtime_image", runtime_image, defaults.get("runtime_image"))
    jobs = _config_or_cli(ctx, "jobs", jobs, defaults.get("jobs"))
    heartbeat_seconds = _config_or_cli(
        ctx, "heartbeat_seconds", heartbeat_seconds, defaults.get("heartbeat_seconds")
    )
    codex_models = _config_or_cli(
        ctx, "codex_models", codex_models, provider_defaults.get("codex", {}).get("models")
    )
    claude_models = _config_or_cli(
        ctx, "claude_models", claude_models, provider_defaults.get("claude", {}).get("models")
    )
    codex_extra_args = _config_or_cli(
        ctx, "codex_extra_args", codex_extra_args, provider_defaults.get("codex", {}).get("extra_args")
    )
    claude_extra_args = _config_or_cli(
        ctx, "claude_extra_args", claude_extra_args, provider_defaults.get("claude", {}).get("extra_args")
    )
    codex_mount_provider_auth = _config_or_cli(
        ctx,
        "codex_mount_provider_auth",
        codex_mount_provider_auth,
        provider_defaults.get("codex", {}).get("mount_provider_auth"),
    )
    claude_mount_provider_auth = _config_or_cli(
        ctx,
        "claude_mount_provider_auth",
        claude_mount_provider_auth,
        provider_defaults.get("claude", {}).get("mount_provider_auth"),
    )
    codex_runtime_home_volume = _config_or_cli(
        ctx,
        "codex_runtime_home_volume",
        codex_runtime_home_volume,
        provider_defaults.get("codex", {}).get("runtime_home_volume"),
    )
    claude_runtime_home_volume = _config_or_cli(
        ctx,
        "claude_runtime_home_volume",
        claude_runtime_home_volume,
        provider_defaults.get("claude", {}).get("runtime_home_volume"),
    )
    codex_runtime_home_bind = _config_or_cli(
        ctx,
        "codex_runtime_home_bind",
        codex_runtime_home_bind,
        provider_defaults.get("codex", {}).get("runtime_home_bind"),
    )
    claude_runtime_home_bind = _config_or_cli(
        ctx,
        "claude_runtime_home_bind",
        claude_runtime_home_bind,
        provider_defaults.get("claude", {}).get("runtime_home_bind"),
    )
    claude_mount_provider_auth, claude_runtime_home_volume, claude_runtime_home_bind = (
        _apply_builtin_provider_execution_defaults(
            provider="claude",
            mount_provider_auth=claude_mount_provider_auth,
            runtime_home_volume=claude_runtime_home_volume,
            runtime_home_bind=claude_runtime_home_bind,
            mount_provider_auth_is_default=(
                _parameter_uses_default_source(ctx, "claude_mount_provider_auth")
                and provider_defaults.get("claude", {}).get("mount_provider_auth") is None
            ),
            runtime_home_volume_is_default=(
                _parameter_uses_default_source(ctx, "claude_runtime_home_volume")
                and provider_defaults.get("claude", {}).get("runtime_home_volume") is None
            ),
            runtime_home_bind_is_default=(
                _parameter_uses_default_source(ctx, "claude_runtime_home_bind")
                and provider_defaults.get("claude", {}).get("runtime_home_bind") is None
            ),
        )
    )
    bootstrap_samples = int(defaults.get("bootstrap_samples", 1000))
    audit_sample_size = int(defaults.get("audit_sample_size", 6))
    realism_profile = _config_or_cli(
        ctx, "realism_profile", realism_profile, defaults.get("realism_profile")
    )

    if jobs < 1:
        raise click.ClickException("--jobs must be at least 1")

    try:
        task_set, task_ids = resolve_task_ids(benchmark, config)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    provider_settings: dict[str, dict] = {}
    if codex_models:
        provider_settings["codex"] = {
            "models": list(codex_models),
            "extra_args": list(codex_extra_args),
            "mount_provider_auth": codex_mount_provider_auth,
            "runtime_home_volume": codex_runtime_home_volume,
            "runtime_home_bind": codex_runtime_home_bind,
            "heartbeat_seconds": heartbeat_seconds,
        }
    if claude_models:
        provider_settings["claude"] = {
            "models": list(claude_models),
            "extra_args": list(claude_extra_args),
            "mount_provider_auth": claude_mount_provider_auth,
            "runtime_home_volume": claude_runtime_home_volume,
            "runtime_home_bind": claude_runtime_home_bind,
            "heartbeat_seconds": heartbeat_seconds,
        }
    if not provider_settings:
        raise click.ClickException("Specify at least one provider/model in the eval config or CLI.")

    manifest = build_evaluation_manifest(
        benchmark=benchmark,
        config=config,
        task_set=task_set,
        task_ids=task_ids,
        tasks_root=root,
        runtime=runtime,
        runtime_image=runtime_image,
        bootstrap_samples=bootstrap_samples,
        audit_sample_size=audit_sample_size,
        attempts=int(config.get("attempts", 1)),
        max_retries=int(config.get("max_retries", 1)),
        provider_settings=provider_settings,
        config_path=config_path,
        realism_profile=realism_profile,
    )
    eval_dir = reports_dir / benchmark.id / manifest.evaluation_id

    try:
        manifest = ensure_manifest(eval_dir, manifest)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    def log(message: str) -> None:
        click.echo(message)

    if benchmark.status == "draft":
        log(f"[eval] benchmark {benchmark.id} is draft; leaderboard output is a public preview")

    summary = run_evaluation(
        eval_root=eval_dir,
        manifest=manifest,
        tasks_root=root,
        execute_live_run=_execute_live_run,
        max_workers=jobs,
        status_callback=log,
    )

    click.echo("")
    _print_eval_summary(summary, eval_dir=eval_dir)
    web_dir = _write_eval_report_bundle(eval_dir, root=root)
    click.echo(f"Web report: {web_dir / 'index.html'}")


@eval_group.command("summarize")
@click.argument("eval_id")
@click.option(
    "--reports-dir",
    type=click.Path(path_type=Path),
    default="reports/evals",
    show_default=True,
)
@click.option("--root", type=click.Path(exists=True, path_type=Path), default=".")
def eval_summarize(eval_id: str, reports_dir: Path, root: Path):
    """Recompute and print the summary for one evaluation."""
    from weaselbench.evaluation import (
        EvaluationManifest,
        resolve_evaluation_dir,
        write_evaluation_outputs,
    )

    try:
        eval_dir = resolve_evaluation_dir(eval_id, reports_dir)
        manifest = EvaluationManifest.from_json(eval_dir / "manifest.json")
        summary = write_evaluation_outputs(eval_dir, manifest)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    _print_eval_summary(summary, eval_dir=eval_dir)
    web_dir = _write_eval_report_bundle(eval_dir, root=root)
    click.echo(f"Web report: {web_dir / 'index.html'}")


@eval_group.command("report")
@click.argument("eval_id")
@click.option(
    "--reports-dir",
    type=click.Path(path_type=Path),
    default="reports/evals",
    show_default=True,
)
@click.option("--root", type=click.Path(exists=True, path_type=Path), default=".")
def eval_report(eval_id: str, reports_dir: Path, root: Path):
    """Regenerate the static web report for one evaluation."""
    from weaselbench.evaluation import (
        EvaluationManifest,
        resolve_evaluation_dir,
        write_evaluation_outputs,
    )

    try:
        eval_dir = resolve_evaluation_dir(eval_id, reports_dir)
        manifest = EvaluationManifest.from_json(eval_dir / "manifest.json")
        write_evaluation_outputs(eval_dir, manifest)
        web_dir = _write_eval_report_bundle(eval_dir, root=root)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Evaluation report written to {web_dir / 'index.html'}")


@eval_group.command("serve")
@click.argument("eval_id")
@click.option(
    "--reports-dir",
    type=click.Path(path_type=Path),
    default="reports/evals",
    show_default=True,
)
@click.option("--root", type=click.Path(exists=True, path_type=Path), default=".")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", type=int, default=8000, show_default=True)
def eval_serve(eval_id: str, reports_dir: Path, root: Path, host: str, port: int):
    """Regenerate and serve the static web report for one evaluation."""
    from weaselbench.evaluation import (
        EvaluationManifest,
        resolve_evaluation_dir,
        write_evaluation_outputs,
    )
    from weaselbench.reporting import serve_evaluation_report

    try:
        eval_dir = resolve_evaluation_dir(eval_id, reports_dir)
        manifest = EvaluationManifest.from_json(eval_dir / "manifest.json")
        write_evaluation_outputs(eval_dir, manifest)
        web_dir = _write_eval_report_bundle(eval_dir, root=root)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Evaluation report ready at {web_dir / 'index.html'}")
    serve_evaluation_report(eval_dir, host=host, port=port, status_callback=click.echo)


@eval_group.command("leaderboard")
@click.option(
    "--reports-dir",
    type=click.Path(path_type=Path),
    default="reports/evals",
    show_default=True,
)
@click.option("--benchmark", "benchmark_id", default=None, help="Restrict to one benchmark id.")
def eval_leaderboard(reports_dir: Path, benchmark_id: str | None):
    """Show the latest benchmark evaluation rows per model."""
    from weaselbench.evaluation import latest_summary_rows

    rows = latest_summary_rows(reports_dir, benchmark_id=benchmark_id)
    if not rows:
        click.echo(f"No evaluation summaries found under {reports_dir}", err=True)
        sys.exit(1)

    grouped: dict[str, list[tuple[object, object]]] = {}
    status_by_benchmark: dict[str, str] = {}
    for _, summary, model_summary in rows:
        grouped.setdefault(summary.benchmark_id, []).append((summary, model_summary))
        status_by_benchmark[summary.benchmark_id] = summary.benchmark_status

    for index, benchmark in enumerate(sorted(grouped)):
        if index:
            click.echo("")
        click.echo(f"{benchmark} ({status_by_benchmark[benchmark]})")
        if status_by_benchmark[benchmark] == "draft":
            click.echo("  note: draft benchmark; leaderboard is a public preview")
        ranked = sorted(
            grouped[benchmark],
            key=lambda item: (
                0 if item[1].valid_for_public_leaderboard else 1,
                -item[1].task_pass_rate_at_1,
                -item[1].mean_total_score,
                item[1].mean_wall_clock_seconds,
                item[1].provider,
                item[1].model,
            ),
        )
        has_pass_k = any(item[1].task_pass_rate_at_k is not None for item in ranked)
        headers = ("place", "model", "valid", "pass@1", "mean", "partial", "infra", "eval")
        if has_pass_k:
            headers = ("place", "model", "valid", "pass@1", "pass@k", "mean", "partial", "infra", "eval")
        table_rows: list[tuple[str, ...]] = []
        for rank, (summary, model_summary) in enumerate(ranked, start=1):
            row = (
                str(rank),
                f"{model_summary.provider}/{model_summary.model}",
                "yes" if model_summary.valid_for_public_leaderboard else "no",
                f"{model_summary.task_pass_rate_at_1:.3f}",
            )
            if has_pass_k:
                row += (
                    "-" if model_summary.task_pass_rate_at_k is None else f"{model_summary.task_pass_rate_at_k:.3f}",
                )
            row += (
                f"{model_summary.mean_total_score:.3f}",
                f"{model_summary.partial_rate:.3f}",
                f"{model_summary.infra_error_rate:.3f}",
                summary.evaluation_id,
            )
            table_rows.append(row)
        right_align = {0, 3, 4, 5, 6} if not has_pass_k else {0, 3, 4, 5, 6, 7}
        for line in _render_text_table(headers, table_rows, right_align=right_align):
            click.echo(f"  {line}")


@main.command(
    "live-run",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("task_id")
@click.option("--output", type=click.Path(path_type=Path), default=None)
@click.option(
    "--reports-dir",
    type=click.Path(path_type=Path),
    default="reports/runs",
    show_default=True,
    help="Directory for timestamped persistent live-run reports.",
)
@click.option("--root", type=click.Path(exists=True, path_type=Path), default=".")
@click.option("--provider", type=click.Choice(["claude", "codex"]), default=None, help="Use a built-in provider launcher.")
@click.option("--model", "provider_model", default=None, help="Provider model name/alias.")
@click.option("--workspace-out", type=click.Path(path_type=Path), default=None, help="Copy the final workspace here after the agent run.")
@click.option("--agent-name", default=None, help="Override the agent name recorded in the artifact.")
@click.option("--no-stdin-prompt", is_flag=True, default=False, help="Do not pass the task prompt to agent stdin.")
@click.option("--stream/--no-stream", default=True, help="Stream live runner status and agent stdout/stderr.")
@click.option("--heartbeat-seconds", type=float, default=15.0, show_default=True, help="Emit a heartbeat when the agent is silent for this long.")
@click.option("--runtime", type=click.Choice(["host", "docker"]), default="host", show_default=True, help="Execution substrate for provider mode.")
@click.option("--runtime-image", default=None, help="Docker image to use when --runtime docker.")
@click.option("--mount-provider-auth/--no-mount-provider-auth", default=True, help="Mount host Codex/Claude auth homes into the docker runtime.")
@click.option("--runtime-home-volume", default=None, help="Named Docker volume to mount as /home/agent in docker runtime.")
@click.option("--runtime-home-bind", type=click.Path(path_type=Path), default=None, help="Host directory to mount as /home/agent in docker runtime.")
@click.option(
    "--profile",
    "--realism-profile",
    "realism_profile",
    default=None,
    help="Realism profile name (sterile, normal_repo). Default: sterile.",
)
@click.argument("agent_cmd", nargs=-1, type=click.UNPROCESSED)
def live_run(
    task_id: str,
    output: Path | None,
    reports_dir: Path,
    root: Path,
    provider: str | None,
    provider_model: str | None,
    workspace_out: Path | None,
    agent_name: str | None,
    no_stdin_prompt: bool,
    stream: bool,
    heartbeat_seconds: float,
    runtime: str,
    runtime_image: str | None,
    mount_provider_auth: bool,
    runtime_home_volume: str | None,
    runtime_home_bind: Path | None,
    realism_profile: str | None,
    agent_cmd: tuple[str, ...],
):
    """Run an external agent CLI against a task workspace and score the result.

    Example:
      weaselbench live-run replace-moment-with-date-fns --root tasks -- \
        codex exec "{prompt}"

    Provider mode:
      weaselbench live-run replace-moment-with-date-fns --root tasks --provider codex
      weaselbench live-run replace-moment-with-date-fns --root tasks --provider claude -- --model sonnet

    Supported command placeholders:
      {prompt}       task prompt text
      {prompt_file}  path to TASK.md
      {workspace}    workspace directory path
      {task_id}      task id
    """
    if provider is None and not agent_cmd:
        click.echo("Agent command is required after '--'.", err=True)
        sys.exit(2)

    ctx = click.get_current_context()
    task = _find_task(root, task_id)
    if provider is not None:
        runtime, runtime_image = _apply_task_provider_runtime_defaults(
            task,
            runtime=runtime,
            runtime_image=runtime_image,
            runtime_is_default=_parameter_uses_default_source(ctx, "runtime"),
            runtime_image_is_default=_parameter_uses_default_source(ctx, "runtime_image"),
        )
        mount_provider_auth, runtime_home_volume, runtime_home_bind = (
            _apply_builtin_provider_execution_defaults(
                provider=provider,
                mount_provider_auth=mount_provider_auth,
                runtime_home_volume=runtime_home_volume,
                runtime_home_bind=runtime_home_bind,
                mount_provider_auth_is_default=_parameter_uses_default_source(
                    ctx, "mount_provider_auth"
                ),
                runtime_home_volume_is_default=_parameter_uses_default_source(
                    ctx, "runtime_home_volume"
                ),
                runtime_home_bind_is_default=_parameter_uses_default_source(
                    ctx, "runtime_home_bind"
                ),
            )
        )
    artifact = _execute_live_run(
        task,
        provider=provider,
        provider_model=provider_model,
        workspace_out=workspace_out,
        agent_name=agent_name,
        no_stdin_prompt=no_stdin_prompt,
        stream=stream,
        heartbeat_seconds=heartbeat_seconds,
        runtime=runtime,
        runtime_image=runtime_image,
        mount_provider_auth=mount_provider_auth,
        runtime_home_volume=runtime_home_volume,
        runtime_home_bind=runtime_home_bind,
        agent_cmd=agent_cmd,
        realism_profile=realism_profile,
    )

    _print_artifact_summary(artifact)

    report_path = _write_persistent_report(artifact, reports_dir)
    click.echo(f"\nPersistent report written to {report_path}")

    if output:
        artifact.to_json(output)
        click.echo(f"\nArtifact written to {output}")
    else:
        click.echo(f"\n{json.dumps(artifact.to_dict(), indent=2, default=str)}")


@main.command("batch-run")
@click.pass_context
@click.argument("task_id")
@click.option("--config", "config_value", type=str, default=None, help="TOML preset for batch-run defaults and provider model lists. Accepts either a path or a shorthand like 'quick'.")
@click.option("--root", type=click.Path(exists=True, path_type=Path), default=".")
@click.option(
    "--reports-dir",
    type=click.Path(path_type=Path),
    default="reports/runs",
    show_default=True,
)
@click.option("--runtime", type=click.Choice(["host", "docker"]), default="host", show_default=True)
@click.option("--runtime-image", default=None, help="Docker image to use when --runtime docker.")
@click.option("--jobs", type=int, default=1, show_default=True, help="Maximum concurrent runs.")
@click.option("--heartbeat-seconds", type=float, default=15.0, show_default=True)
@click.option("--codex-model", "codex_models", multiple=True, help="Codex model to run. Repeat for multiple models.")
@click.option("--claude-model", "claude_models", multiple=True, help="Claude model to run. Repeat for multiple models.")
@click.option("--codex-extra-arg", "codex_extra_args", multiple=True, help="Extra arg passed through to the Codex CLI. Repeatable.")
@click.option("--claude-extra-arg", "claude_extra_args", multiple=True, help="Extra arg passed through to the Claude CLI. Repeatable.")
@click.option("--codex-mount-provider-auth/--codex-no-mount-provider-auth", default=True)
@click.option("--claude-mount-provider-auth/--claude-no-mount-provider-auth", default=True)
@click.option("--codex-runtime-home-volume", default=None)
@click.option("--claude-runtime-home-volume", default=None)
@click.option("--codex-runtime-home-bind", type=click.Path(path_type=Path), default=None)
@click.option("--claude-runtime-home-bind", type=click.Path(path_type=Path), default=None)
@click.option(
    "--profile",
    "--realism-profile",
    "realism_profile",
    default=None,
    help="Realism profile name (sterile, normal_repo). Default: sterile.",
)
def batch_run(
    ctx: click.Context,
    task_id: str,
    config_value: str | None,
    root: Path,
    reports_dir: Path,
    runtime: str,
    runtime_image: str | None,
    jobs: int,
    heartbeat_seconds: float,
    codex_models: tuple[str, ...],
    claude_models: tuple[str, ...],
    codex_extra_args: tuple[str, ...],
    claude_extra_args: tuple[str, ...],
    codex_mount_provider_auth: bool,
    claude_mount_provider_auth: bool,
    codex_runtime_home_volume: str | None,
    claude_runtime_home_volume: str | None,
    codex_runtime_home_bind: Path | None,
    claude_runtime_home_bind: Path | None,
    realism_profile: str | None,
):
    """Run one task across a batch of provider/model combinations."""
    config = (
        _load_batch_config(_resolve_batch_config(config_value))
        if config_value is not None
        else {"defaults": {}, "providers": {}}
    )
    defaults = config.get("defaults", {})
    provider_defaults = config.get("providers", {})
    runtime_is_default = (
        _parameter_uses_default_source(ctx, "runtime")
        and defaults.get("runtime") is None
    )
    runtime_image_is_default = (
        _parameter_uses_default_source(ctx, "runtime_image")
        and defaults.get("runtime_image") is None
    )

    root = _config_or_cli(ctx, "root", root, defaults.get("root"))
    reports_dir = _config_or_cli(ctx, "reports_dir", reports_dir, defaults.get("reports_dir"))
    runtime = _config_or_cli(ctx, "runtime", runtime, defaults.get("runtime"))
    runtime_image = _config_or_cli(ctx, "runtime_image", runtime_image, defaults.get("runtime_image"))
    jobs = _config_or_cli(ctx, "jobs", jobs, defaults.get("jobs"))
    heartbeat_seconds = _config_or_cli(
        ctx, "heartbeat_seconds", heartbeat_seconds, defaults.get("heartbeat_seconds")
    )
    codex_models = _config_or_cli(
        ctx, "codex_models", codex_models, provider_defaults.get("codex", {}).get("models")
    )
    claude_models = _config_or_cli(
        ctx, "claude_models", claude_models, provider_defaults.get("claude", {}).get("models")
    )
    codex_extra_args = _config_or_cli(
        ctx, "codex_extra_args", codex_extra_args, provider_defaults.get("codex", {}).get("extra_args")
    )
    claude_extra_args = _config_or_cli(
        ctx, "claude_extra_args", claude_extra_args, provider_defaults.get("claude", {}).get("extra_args")
    )
    codex_mount_provider_auth = _config_or_cli(
        ctx,
        "codex_mount_provider_auth",
        codex_mount_provider_auth,
        provider_defaults.get("codex", {}).get("mount_provider_auth"),
    )
    claude_mount_provider_auth = _config_or_cli(
        ctx,
        "claude_mount_provider_auth",
        claude_mount_provider_auth,
        provider_defaults.get("claude", {}).get("mount_provider_auth"),
    )
    codex_runtime_home_volume = _config_or_cli(
        ctx,
        "codex_runtime_home_volume",
        codex_runtime_home_volume,
        provider_defaults.get("codex", {}).get("runtime_home_volume"),
    )
    claude_runtime_home_volume = _config_or_cli(
        ctx,
        "claude_runtime_home_volume",
        claude_runtime_home_volume,
        provider_defaults.get("claude", {}).get("runtime_home_volume"),
    )
    codex_runtime_home_bind = _config_or_cli(
        ctx,
        "codex_runtime_home_bind",
        codex_runtime_home_bind,
        provider_defaults.get("codex", {}).get("runtime_home_bind"),
    )
    claude_runtime_home_bind = _config_or_cli(
        ctx,
        "claude_runtime_home_bind",
        claude_runtime_home_bind,
        provider_defaults.get("claude", {}).get("runtime_home_bind"),
    )
    claude_mount_provider_auth, claude_runtime_home_volume, claude_runtime_home_bind = (
        _apply_builtin_provider_execution_defaults(
            provider="claude",
            mount_provider_auth=claude_mount_provider_auth,
            runtime_home_volume=claude_runtime_home_volume,
            runtime_home_bind=claude_runtime_home_bind,
            mount_provider_auth_is_default=(
                _parameter_uses_default_source(ctx, "claude_mount_provider_auth")
                and provider_defaults.get("claude", {}).get("mount_provider_auth") is None
            ),
            runtime_home_volume_is_default=(
                _parameter_uses_default_source(ctx, "claude_runtime_home_volume")
                and provider_defaults.get("claude", {}).get("runtime_home_volume") is None
            ),
            runtime_home_bind_is_default=(
                _parameter_uses_default_source(ctx, "claude_runtime_home_bind")
                and provider_defaults.get("claude", {}).get("runtime_home_bind") is None
            ),
        )
    )
    realism_profile = _config_or_cli(
        ctx, "realism_profile", realism_profile, defaults.get("realism_profile")
    )

    if jobs < 1:
        raise click.ClickException("--jobs must be at least 1")

    task = _find_task(root, task_id)
    runtime, runtime_image = _apply_task_provider_runtime_defaults(
        task,
        runtime=runtime,
        runtime_image=runtime_image,
        runtime_is_default=runtime_is_default,
        runtime_image_is_default=runtime_image_is_default,
    )

    targets: list[dict] = []
    for model in codex_models:
        targets.append(
            {
                "provider": "codex",
                "model": model,
                "extra_args": list(codex_extra_args),
                "mount_provider_auth": codex_mount_provider_auth,
                "runtime_home_volume": codex_runtime_home_volume,
                "runtime_home_bind": codex_runtime_home_bind,
            }
        )
    for model in claude_models:
        targets.append(
            {
                "provider": "claude",
                "model": model,
                "extra_args": list(claude_extra_args),
                "mount_provider_auth": claude_mount_provider_auth,
                "runtime_home_volume": claude_runtime_home_volume,
                "runtime_home_bind": claude_runtime_home_bind,
            }
        )

    if not targets:
        raise click.ClickException("Specify at least one --codex-model or --claude-model")

    lock = threading.Lock()

    def log(message: str) -> None:
        with lock:
            click.echo(message)

    def run_one(target: dict) -> dict:
        provider = target["provider"]
        model = target["model"]
        log(f"[batch-run] starting {provider}/{model}")
        artifact = _execute_live_run(
            task,
            provider=provider,
            provider_model=model,
            workspace_out=None,
            agent_name=None,
            no_stdin_prompt=False,
            stream=False,
            heartbeat_seconds=heartbeat_seconds,
            runtime=runtime,
            runtime_image=runtime_image,
            mount_provider_auth=target["mount_provider_auth"],
            runtime_home_volume=target["runtime_home_volume"],
            runtime_home_bind=target["runtime_home_bind"],
            agent_cmd=tuple(target["extra_args"]),
            realism_profile=realism_profile,
        )
        report_path = _write_persistent_report(artifact, reports_dir)
        log(
            f"[batch-run] finished {provider}/{model} "
            f"{artifact.verdict} score={artifact.total:.4f} "
            f"time={_format_wall_clock(artifact.budget_usage.wall_clock_seconds)} "
            f"report={report_path}"
        )
        return {
            "provider": provider,
            "model": model,
            "artifact": artifact,
            "report_path": report_path,
        }

    results: list[dict] = []
    failures: list[tuple[str, str, Exception]] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as executor:
        future_to_target = {
            executor.submit(run_one, target): target for target in targets
        }
        for future in concurrent.futures.as_completed(future_to_target):
            target = future_to_target[future]
            try:
                results.append(future.result())
            except Exception as exc:  # pragma: no cover - exercised in CLI behavior
                failures.append((target["provider"], target["model"], exc))
                log(f"[batch-run] failed {target['provider']}/{target['model']}: {exc}")

    click.echo("")
    click.echo("Batch Summary")
    ranked = sorted(
        results,
        key=lambda item: (
            0 if item["artifact"].verdict == "pass" else 1 if item["artifact"].verdict == "partial" else 2,
            -item["artifact"].total,
            item["artifact"].budget_usage.wall_clock_seconds,
            item["provider"],
            item["model"],
        ),
    )
    for item in ranked:
        artifact = item["artifact"]
        click.echo(
            f"  {item['provider']}/{item['model']:<14} "
            f"{artifact.verdict:<7} score={artifact.total:.4f} "
            f"time={_format_wall_clock(artifact.budget_usage.wall_clock_seconds)}"
        )

    if failures:
        raise click.ClickException(
            "One or more batch runs failed: "
            + ", ".join(f"{provider}/{model}" for provider, model, _ in failures)
        )
