"""Static evaluation report generation and serving."""

from __future__ import annotations

import json
import shutil
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from weaselbench.artifacts import RunArtifact
from weaselbench.evaluation import EvaluationManifest, EvaluationSummary
from weaselbench.loader import load_task_by_id

_WEB_ASSET_DIR = Path(__file__).with_name("web_assets")


def write_evaluation_report(
    eval_root: Path,
    *,
    tasks_root: Path | None = None,
) -> Path:
    """Write a static HTML report bundle for one evaluation."""
    manifest = EvaluationManifest.from_json(eval_root / "manifest.json")
    summary = EvaluationSummary.from_json(eval_root / "summary.json")
    web_dir = eval_root / "web"
    web_dir.mkdir(parents=True, exist_ok=True)

    index = build_evaluation_report_index(
        eval_root,
        manifest=manifest,
        summary=summary,
        tasks_root=tasks_root,
    )
    (web_dir / "report-index.json").write_text(json.dumps(index, indent=2))

    for name in ("index.html", "app.js", "styles.css"):
        shutil.copy2(_WEB_ASSET_DIR / name, web_dir / name)

    return web_dir


def build_evaluation_report_index(
    eval_root: Path,
    *,
    manifest: EvaluationManifest,
    summary: EvaluationSummary,
    tasks_root: Path | None = None,
) -> dict[str, Any]:
    """Build the lightweight JSON index consumed by the static report UI."""
    task_specs = _resolve_task_specs(manifest, tasks_root=tasks_root)
    runs_by_task: dict[str, list[dict[str, Any]]] = {task_id: [] for task_id in manifest.task_ids}

    for cell in manifest.cells:
        if not cell.artifact_paths:
            continue
        for retry_index, artifact_relpath in enumerate(cell.artifact_paths):
            artifact_path = eval_root / artifact_relpath
            if not artifact_path.exists():
                continue
            artifact = RunArtifact.from_json(artifact_path)
            runs_by_task.setdefault(cell.task_id, []).append(
                _artifact_report_row(
                    artifact,
                    cell=cell,
                    retry_index=retry_index,
                    artifact_relpath=artifact_relpath,
                )
            )

    tasks: list[dict[str, Any]] = []
    for task_id in manifest.task_ids:
        spec = task_specs.get(task_id, {})
        task_runs = sorted(
            runs_by_task.get(task_id, []),
            key=lambda item: (
                item["provider"],
                item["model"],
                item["attempt_index"],
                item["retry_index"],
            ),
        )
        tasks.append(
            {
                "task_id": task_id,
                "title": spec.get("title", task_id),
                "summary": spec.get("summary", ""),
                "workflow": spec.get("workflow", ""),
                "prompt": spec.get("prompt", ""),
                "acceptance_criteria": list(spec.get("acceptance_criteria", [])),
                "labels": dict(spec.get("labels", {})),
                "scoring": dict(spec.get("scoring", {})),
                "has_task_spec": bool(spec),
                "runs": task_runs,
            }
        )

    return {
        "report_version": 1,
        "generated_at": summary.generated_at,
        "evaluation": {
            "evaluation_id": summary.evaluation_id,
            "benchmark_id": summary.benchmark_id,
            "benchmark_name": summary.benchmark_name,
            "benchmark_status": summary.benchmark_status,
            "task_set": summary.task_set,
            "task_count": summary.task_count,
            "attempts": summary.attempts,
            "bootstrap_samples": summary.bootstrap_samples,
            "manifest_fingerprint": summary.manifest_fingerprint,
            "runtime": manifest.runtime,
            "runtime_image": manifest.runtime_image,
            "valid_for_public_leaderboard": summary.valid_for_public_leaderboard,
            "invalid_reasons": summary.invalid_reasons,
            "audit_pack_path": summary.audit_pack_path,
        },
        "models": [
            {
                "provider": item.provider,
                "model": item.model,
                "model_key": f"{item.provider}/{item.model}",
                "task_count": item.task_count,
                "attempts": item.attempts,
                "completed_cells": item.completed_cells,
                "valid_for_public_leaderboard": item.valid_for_public_leaderboard,
                "invalid_reasons": item.invalid_reasons,
                "task_pass_rate_at_1": item.task_pass_rate_at_1,
                "task_pass_rate_at_k": item.task_pass_rate_at_k,
                "best_of_k_mean_total": item.best_of_k_mean_total,
                "attempt_score_variance": item.attempt_score_variance,
                "mean_total_score": item.mean_total_score,
                "partial_rate": item.partial_rate,
                "mean_axis_scores": item.mean_axis_scores,
                "mean_wall_clock_seconds": item.mean_wall_clock_seconds,
                "mean_model_calls": item.mean_model_calls,
                "mean_dollar_cost": item.mean_dollar_cost,
                "infra_error_rate": item.infra_error_rate,
                "unresolved_infra_cells": item.unresolved_infra_cells,
                "bootstrap_ci95": item.bootstrap_ci95,
            }
            for item in sorted(
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
        ],
        "tasks": tasks,
    }


def serve_evaluation_report(
    eval_root: Path,
    *,
    host: str,
    port: int,
    status_callback: Callable[[str], None] | None = None,
) -> None:
    """Serve an evaluation report directory until interrupted."""

    class EvalReportHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, directory: str, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)

        def do_GET(self) -> None:  # pragma: no cover - exercised manually
            if self.path in {"", "/"}:
                self.send_response(302)
                self.send_header("Location", "/web/")
                self.end_headers()
                return
            super().do_GET()

        def log_message(self, format: str, *args) -> None:  # pragma: no cover
            if status_callback is not None:
                status_callback(format % args)

    server = ThreadingHTTPServer(
        (host, port),
        partial(EvalReportHandler, directory=str(eval_root)),
    )
    report_url = f"http://{host}:{server.server_address[1]}/web/"
    if status_callback is not None:
        status_callback(f"Serving evaluation report at {report_url}")
    try:  # pragma: no branch - manual flow
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - manual flow
        if status_callback is not None:
            status_callback("Stopping evaluation report server")
    finally:
        server.server_close()


def _resolve_task_specs(
    manifest: EvaluationManifest,
    *,
    tasks_root: Path | None,
) -> dict[str, dict[str, Any]]:
    """Return frozen task specs, backfilling legacy manifests when possible."""
    task_specs = {task_id: dict(spec) for task_id, spec in manifest.task_specs.items()}
    if tasks_root is None:
        return task_specs

    for task_id in manifest.task_ids:
        if task_id in task_specs:
            continue
        try:
            task_specs[task_id] = load_task_by_id(tasks_root, task_id).data
        except ValueError:
            continue
    return task_specs


def _artifact_report_row(
    artifact: RunArtifact,
    *,
    cell,
    retry_index: int,
    artifact_relpath: str,
) -> dict[str, Any]:
    """Build the lightweight row used by the task matrix and compare view."""
    final_state = artifact.final_state
    changed_paths = (
        [item.path for item in final_state.changed_files]
        if final_state is not None
        else [item["path"] for item in artifact.edits if "path" in item]
    )
    artifact_relative_path = Path(artifact_relpath)
    artifact_url = Path(
        "..",
        artifact_relative_path.as_posix(),
    ).as_posix()
    return {
        "cell_id": cell.cell_id,
        "task_id": artifact.task_id,
        "provider": artifact.agent.get("name", "unknown"),
        "model": artifact.agent.get("model", "unknown"),
        "model_key": (
            f"{artifact.agent.get('name', 'unknown')}/{artifact.agent.get('model', 'unknown')}"
        ),
        "attempt_index": cell.attempt_index,
        "retry_index": retry_index,
        "canonical_for_model": bool(
            cell.attempt_index == 0 and retry_index == len(cell.artifact_paths) - 1
        ),
        "artifact_path": artifact_relpath,
        "artifact_url": artifact_url,
        "started_at": artifact.started_at.isoformat(),
        "ended_at": artifact.ended_at.isoformat(),
        "task_revision": getattr(artifact.task_revision, "combined", "unknown"),
        "verdict": artifact.verdict,
        "total": artifact.total,
        "axis_scores": {axis.name: axis.raw_score for axis in artifact.axes},
        "weighted_axis_scores": {axis.name: axis.weighted_score for axis in artifact.axes},
        "wall_clock_seconds": artifact.budget_usage.wall_clock_seconds,
        "model_calls": artifact.budget_usage.model_calls,
        "dollar_cost": artifact.budget_usage.dollar_cost,
        "changed_files": artifact.run_stats.changed_files,
        "changed_paths": changed_paths,
        "transcript_entries": len(artifact.transcript),
        "tool_usage_entries": len(artifact.tool_usage),
        "final_state_available": final_state is not None,
        "termination_reason": (
            artifact.termination.reason if artifact.termination is not None else None
        ),
        "infra_failure": (
            artifact.evaluation.infra_failure
            if artifact.evaluation is not None
            else None
        ),
    }
