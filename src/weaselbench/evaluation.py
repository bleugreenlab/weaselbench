"""Benchmark evaluation manifests, execution, and summaries."""

from __future__ import annotations

import concurrent.futures
import csv
import json
import random
import re
import statistics
import tempfile
import threading
import tomllib
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from weaselbench.artifacts import (
    BudgetUsage,
    EvaluationMetadata,
    RunArtifact,
    RunStats,
    TaskRevision,
)
from weaselbench.checks.filesystem import snapshot_workspace
from weaselbench.loader import Task, load_task_by_id
from weaselbench.runner import _compute_task_revision, _hash_jsonable, materialize_workspace


@dataclass
class BenchmarkDefinition:
    """Resolved benchmark metadata plus split membership."""

    id: str
    name: str
    status: str
    splits: dict[str, dict[str, Any]]
    data: dict[str, Any]


@dataclass
class EvaluationCell:
    """One scored cell in an evaluation matrix."""

    cell_id: str
    task_id: str
    task_revision: str
    provider: str
    model: str
    attempt_index: int
    status: str = "pending"
    retries: int = 0
    artifact_paths: list[str] = field(default_factory=list)
    infra_failure: str | None = None
    last_error: str | None = None


@dataclass
class EvaluationManifest:
    """Frozen execution plan for a benchmark evaluation."""

    evaluation_id: str
    benchmark_id: str
    benchmark_name: str
    benchmark_status: str
    task_set: str
    task_ids: list[str]
    task_revisions: dict[str, dict[str, str]]
    task_specs: dict[str, dict[str, Any]]
    manifest_fingerprint: str
    created_at: str
    updated_at: str
    harness_revision: str
    attempts: int
    max_retries: int
    bootstrap_samples: int
    audit_sample_size: int
    runtime: str
    runtime_image: str | None = None
    provider_settings: dict[str, dict[str, Any]] = field(default_factory=dict)
    config_path: str | None = None
    realism_profile: str | None = None
    cells: list[EvaluationCell] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation_id": self.evaluation_id,
            "benchmark_id": self.benchmark_id,
            "benchmark_name": self.benchmark_name,
            "benchmark_status": self.benchmark_status,
            "task_set": self.task_set,
            "task_ids": self.task_ids,
            "task_revisions": self.task_revisions,
            "task_specs": self.task_specs,
            "manifest_fingerprint": self.manifest_fingerprint,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "harness_revision": self.harness_revision,
            "attempts": self.attempts,
            "max_retries": self.max_retries,
            "bootstrap_samples": self.bootstrap_samples,
            "audit_sample_size": self.audit_sample_size,
            "runtime": self.runtime,
            "runtime_image": self.runtime_image,
            "provider_settings": self.provider_settings,
            "config_path": self.config_path,
            "realism_profile": self.realism_profile,
            "cells": [asdict(cell) for cell in self.cells],
        }

    def to_json(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def from_json(cls, path: Path) -> EvaluationManifest:
        raw = json.loads(path.read_text())
        return cls(
            evaluation_id=raw["evaluation_id"],
            benchmark_id=raw["benchmark_id"],
            benchmark_name=raw.get("benchmark_name", raw["benchmark_id"]),
            benchmark_status=raw.get("benchmark_status", "unknown"),
            task_set=raw["task_set"],
            task_ids=list(raw.get("task_ids", [])),
            task_revisions=dict(raw.get("task_revisions", {})),
            task_specs=dict(raw.get("task_specs", {})),
            manifest_fingerprint=raw["manifest_fingerprint"],
            created_at=raw["created_at"],
            updated_at=raw["updated_at"],
            harness_revision=raw.get("harness_revision", "unknown"),
            attempts=int(raw.get("attempts", 1)),
            max_retries=int(raw.get("max_retries", 0)),
            bootstrap_samples=int(raw.get("bootstrap_samples", 1000)),
            audit_sample_size=int(raw.get("audit_sample_size", 6)),
            runtime=raw.get("runtime", "host"),
            runtime_image=raw.get("runtime_image"),
            provider_settings=dict(raw.get("provider_settings", {})),
            config_path=raw.get("config_path"),
            realism_profile=raw.get("realism_profile"),
            cells=[EvaluationCell(**cell) for cell in raw.get("cells", [])],
        )


@dataclass
class ModelEvaluationSummary:
    """Aggregate metrics for one provider/model pair."""

    provider: str
    model: str
    task_count: int
    attempts: int
    completed_cells: int
    valid_for_public_leaderboard: bool
    invalid_reasons: list[str] = field(default_factory=list)
    task_pass_rate_at_1: float = 0.0
    task_pass_rate_at_k: float | None = None
    best_of_k_mean_total: float | None = None
    attempt_score_variance: float | None = None
    mean_total_score: float = 0.0
    partial_rate: float = 0.0
    mean_axis_scores: dict[str, float] = field(default_factory=dict)
    mean_wall_clock_seconds: float = 0.0
    mean_model_calls: float = 0.0
    mean_dollar_cost: float = 0.0
    infra_error_rate: float = 0.0
    unresolved_infra_cells: int = 0
    bootstrap_ci95: dict[str, list[float]] = field(default_factory=dict)


@dataclass
class EvaluationSummary:
    """Persisted summary for one evaluation manifest."""

    evaluation_id: str
    benchmark_id: str
    benchmark_name: str
    benchmark_status: str
    manifest_fingerprint: str
    generated_at: str
    task_set: str
    task_count: int
    attempts: int
    bootstrap_samples: int
    valid_for_public_leaderboard: bool
    invalid_reasons: list[str] = field(default_factory=list)
    model_summaries: list[ModelEvaluationSummary] = field(default_factory=list)
    audit_pack_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation_id": self.evaluation_id,
            "benchmark_id": self.benchmark_id,
            "benchmark_name": self.benchmark_name,
            "benchmark_status": self.benchmark_status,
            "manifest_fingerprint": self.manifest_fingerprint,
            "generated_at": self.generated_at,
            "task_set": self.task_set,
            "task_count": self.task_count,
            "attempts": self.attempts,
            "bootstrap_samples": self.bootstrap_samples,
            "valid_for_public_leaderboard": self.valid_for_public_leaderboard,
            "invalid_reasons": self.invalid_reasons,
            "model_summaries": [asdict(item) for item in self.model_summaries],
            "audit_pack_path": self.audit_pack_path,
        }

    def to_json(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def from_json(cls, path: Path) -> EvaluationSummary:
        raw = json.loads(path.read_text())
        return cls(
            evaluation_id=raw["evaluation_id"],
            benchmark_id=raw["benchmark_id"],
            benchmark_name=raw.get("benchmark_name", raw["benchmark_id"]),
            benchmark_status=raw.get("benchmark_status", "unknown"),
            manifest_fingerprint=raw["manifest_fingerprint"],
            generated_at=raw["generated_at"],
            task_set=raw["task_set"],
            task_count=int(raw["task_count"]),
            attempts=int(raw["attempts"]),
            bootstrap_samples=int(raw.get("bootstrap_samples", 1000)),
            valid_for_public_leaderboard=bool(raw.get("valid_for_public_leaderboard", False)),
            invalid_reasons=list(raw.get("invalid_reasons", [])),
            model_summaries=[
                ModelEvaluationSummary(**item)
                for item in raw.get("model_summaries", [])
            ],
            audit_pack_path=raw.get("audit_pack_path"),
        )


def resolve_eval_config_path(config_value: str | Path) -> Path:
    """Resolve an eval config shorthand or explicit path to a TOML file."""
    config_path = Path(config_value)
    if config_path.exists():
        return config_path

    repo_root = Path(__file__).resolve().parents[2]
    presets_dir = repo_root / "configs" / "evals"
    shorthand = config_path if config_path.suffix else config_path.with_suffix(".toml")
    candidate = presets_dir / shorthand
    if candidate.exists():
        return candidate

    raise ValueError(
        f"Eval config not found: {config_value}. Tried {config_path} and {candidate}"
    )


def load_eval_config(config_path: Path) -> dict[str, Any]:
    """Load and normalize an eval config."""
    try:
        with config_path.open("rb") as fh:
            raw = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"Failed to load eval config {config_path}: {exc}") from exc

    defaults = raw.get("defaults", {})
    providers = raw.get("providers", {})
    if not isinstance(defaults, dict) or not isinstance(providers, dict):
        raise ValueError(
            f"Invalid eval config {config_path}: expected [defaults] and [providers.*] tables"
        )

    def provider_table(name: str) -> dict[str, Any]:
        data = providers.get(name, {})
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ValueError(
                f"Invalid eval config {config_path}: [providers.{name}] must be a table"
            )
        return data

    task_ids = raw.get("task_ids")
    if task_ids is not None and not isinstance(task_ids, list):
        raise ValueError(
            f"Invalid eval config {config_path}: task_ids must be an array when set"
        )

    return {
        "benchmark_id": raw.get("benchmark_id"),
        "task_set": raw.get("task_set"),
        "task_ids": task_ids or [],
        "attempts": int(raw.get("attempts", 1)),
        "max_retries": int(raw.get("max_retries", 1)),
        "defaults": defaults,
        "providers": {
            "codex": provider_table("codex"),
            "claude": provider_table("claude"),
        },
    }


def resolve_benchmark_definition(benchmark_value: str | Path) -> BenchmarkDefinition:
    """Resolve a benchmark id/path to its metadata."""
    benchmark_path = Path(benchmark_value)
    if not benchmark_path.exists():
        repo_root = Path(__file__).resolve().parents[2]
        candidate = repo_root / "benchmarks" / (
            benchmark_path if benchmark_path.suffix else benchmark_path.with_suffix(".yaml")
        )
        if candidate.exists():
            benchmark_path = candidate

    if not benchmark_path.exists():
        raise ValueError(f"Benchmark definition not found: {benchmark_value}")

    raw = yaml.safe_load(benchmark_path.read_text()) or {}
    bench = raw.get("benchmark")
    if not isinstance(bench, dict):
        raise ValueError(f"Invalid benchmark definition {benchmark_path}: missing benchmark mapping")

    normalized_splits: dict[str, dict[str, Any]] = {}
    for split_name, split_value in (bench.get("splits") or {}).items():
        if isinstance(split_value, dict):
            normalized_splits[split_name] = dict(split_value)
        else:
            normalized_splits[split_name] = {"count": split_value}

    return BenchmarkDefinition(
        id=bench["id"],
        name=bench.get("name", bench["id"]),
        status=bench.get("status", "unknown"),
        splits=normalized_splits,
        data=bench,
    )


def compute_harness_revision(repo_root: Path | None = None) -> str:
    """Return a stable fingerprint for the current harness source tree."""
    repo_root = repo_root or Path(__file__).resolve().parents[2]
    include_paths = [
        repo_root / "pyproject.toml",
        *(repo_root / "src" / "weaselbench").rglob("*.py"),
        *(repo_root / "schemas").glob("*.json"),
    ]
    snapshot: dict[str, str] = {}
    for path in sorted({path for path in include_paths if path.is_file()}):
        snapshot[str(path.relative_to(repo_root))] = _hash_jsonable(path.read_text())
    return _hash_jsonable(snapshot)


def compute_task_revision_for_eval(task: Task) -> TaskRevision:
    """Compute the task revision used for evaluation manifests."""
    with tempfile.TemporaryDirectory(prefix="weaselbench-eval-task-") as tmp:
        work_dir = Path(tmp) / "workspace"
        materialize_workspace(task, work_dir)
        snapshot = snapshot_workspace(work_dir)
    return _compute_task_revision(task, snapshot)


def build_evaluation_manifest(
    *,
    benchmark: BenchmarkDefinition,
    config: dict[str, Any],
    task_set: str,
    task_ids: list[str],
    tasks_root: Path,
    runtime: str,
    runtime_image: str | None,
    bootstrap_samples: int,
    audit_sample_size: int,
    attempts: int,
    max_retries: int,
    provider_settings: dict[str, dict[str, Any]],
    config_path: Path | None = None,
    realism_profile: str | None = None,
) -> EvaluationManifest:
    """Build a deterministic evaluation manifest for the requested matrix."""
    task_revisions: dict[str, dict[str, str]] = {}
    task_specs: dict[str, dict[str, Any]] = {}
    for task_id in task_ids:
        task = load_task_by_id(tasks_root, task_id)
        task_revisions[task_id] = asdict(compute_task_revision_for_eval(task))
        task_specs[task_id] = task.data

    from weaselbench.realism import resolve_profile

    profile = resolve_profile(realism_profile)
    harness_revision = compute_harness_revision()
    manifest_input = {
        "benchmark_id": benchmark.id,
        "task_set": task_set,
        "task_ids": task_ids,
        "task_revisions": task_revisions,
        "runtime": runtime,
        "runtime_image": runtime_image,
        "attempts": attempts,
        "max_retries": max_retries,
        "bootstrap_samples": bootstrap_samples,
        "audit_sample_size": audit_sample_size,
        "provider_settings": provider_settings,
        "realism_profile": profile.fingerprint_dict(),
        "harness_revision": harness_revision,
    }
    manifest_fingerprint = _hash_jsonable(manifest_input)
    evaluation_id = f"eval-{manifest_fingerprint[:12]}"
    created_at = datetime.now(timezone.utc).isoformat()

    cells: list[EvaluationCell] = []
    for task_id in task_ids:
        for provider, settings in sorted(provider_settings.items()):
            for model in settings.get("models", []):
                for attempt_index in range(attempts):
                    cell_id = _hash_jsonable(
                        {
                            "task_id": task_id,
                            "task_revision": task_revisions[task_id]["combined"],
                            "provider": provider,
                            "model": model,
                            "attempt_index": attempt_index,
                            "manifest_fingerprint": manifest_fingerprint,
                        }
                    )[:16]
                    cells.append(
                        EvaluationCell(
                            cell_id=cell_id,
                            task_id=task_id,
                            task_revision=task_revisions[task_id]["combined"],
                            provider=provider,
                            model=model,
                            attempt_index=attempt_index,
                        )
                    )

    return EvaluationManifest(
        evaluation_id=evaluation_id,
        benchmark_id=benchmark.id,
        benchmark_name=benchmark.name,
        benchmark_status=benchmark.status,
        task_set=task_set,
        task_ids=task_ids,
        task_revisions=task_revisions,
        task_specs=task_specs,
        manifest_fingerprint=manifest_fingerprint,
        created_at=created_at,
        updated_at=created_at,
        harness_revision=harness_revision,
        attempts=attempts,
        max_retries=max_retries,
        bootstrap_samples=bootstrap_samples,
        audit_sample_size=audit_sample_size,
        runtime=runtime,
        runtime_image=runtime_image,
        provider_settings=provider_settings,
        config_path=str(config_path) if config_path is not None else None,
        realism_profile=realism_profile,
        cells=cells,
    )


def resolve_task_ids(benchmark: BenchmarkDefinition, config: dict[str, Any]) -> tuple[str, list[str]]:
    """Resolve the requested task set into an explicit ordered task id list."""
    explicit_task_ids = list(config.get("task_ids", []))
    if explicit_task_ids:
        return config.get("task_set") or "custom", explicit_task_ids

    task_set = config.get("task_set")
    if not task_set:
        raise ValueError("Eval config must set task_set or task_ids")

    split = benchmark.splits.get(task_set)
    if split is None:
        raise ValueError(
            f"Benchmark {benchmark.id} has no split named {task_set}"
        )
    task_ids = split.get("task_ids")
    if not task_ids:
        raise ValueError(
            f"Benchmark {benchmark.id} split {task_set} does not declare task_ids"
        )
    return task_set, list(task_ids)


def ensure_manifest(
    eval_root: Path,
    manifest: EvaluationManifest,
) -> EvaluationManifest:
    """Load an existing manifest if present, otherwise write the provided one."""
    manifest_path = eval_root / "manifest.json"
    if manifest_path.exists():
        loaded = EvaluationManifest.from_json(manifest_path)
        if loaded.manifest_fingerprint != manifest.manifest_fingerprint:
            raise ValueError(
                f"Existing evaluation at {eval_root} has fingerprint "
                f"{loaded.manifest_fingerprint[:8]}, expected {manifest.manifest_fingerprint[:8]}"
            )
        return loaded

    eval_root.mkdir(parents=True, exist_ok=True)
    manifest.to_json(manifest_path)
    return manifest


def persist_manifest(eval_root: Path, manifest: EvaluationManifest) -> None:
    """Update the manifest on disk."""
    manifest.updated_at = datetime.now(timezone.utc).isoformat()
    manifest.to_json(eval_root / "manifest.json")


def annotate_artifact(
    artifact: RunArtifact,
    *,
    manifest: EvaluationManifest,
    cell: EvaluationCell,
) -> RunArtifact:
    """Attach evaluation metadata to a run artifact."""
    runtime_fingerprint = _hash_jsonable(
        {"runtime": manifest.runtime, "runtime_image": manifest.runtime_image}
    )
    artifact.evaluation = EvaluationMetadata(
        benchmark_id=manifest.benchmark_id,
        evaluation_id=manifest.evaluation_id,
        cell_id=cell.cell_id,
        attempt_index=cell.attempt_index,
        provider_model=cell.model,
        harness_revision=manifest.harness_revision,
        manifest_fingerprint=manifest.manifest_fingerprint,
        runtime=manifest.runtime,
        runtime_fingerprint=runtime_fingerprint,
        runtime_image=manifest.runtime_image,
        runtime_image_fingerprint=_hash_jsonable(manifest.runtime_image or "host"),
        infra_failure=None,
    )
    return artifact


def make_infra_failure_artifact(
    *,
    task_id: str,
    task_revision: TaskRevision,
    provider: str,
    model: str,
    manifest: EvaluationManifest,
    cell: EvaluationCell,
    infra_failure: str,
) -> RunArtifact:
    """Create a synthetic artifact for infrastructure-class failures."""
    started_at = datetime.now(timezone.utc)
    ended_at = started_at
    artifact = RunArtifact(
        run_id=str(uuid.uuid4()),
        task_id=task_id,
        started_at=started_at,
        ended_at=ended_at,
        agent={"name": provider, "version": "external", "model": model},
        budget_usage=BudgetUsage(),
        run_stats=RunStats(),
        task_revision=task_revision,
        total=0.0,
        verdict="fail",
    )
    annotate_artifact(artifact, manifest=manifest, cell=cell)
    assert artifact.evaluation is not None
    artifact.evaluation.infra_failure = infra_failure
    return artifact


def classify_infra_exception(exc: Exception) -> str:
    """Map an execution exception to an infra classification label."""
    if isinstance(exc, FileNotFoundError):
        return "missing_dependency"
    return f"{exc.__class__.__name__}"


def artifact_path_for_cell(
    eval_root: Path,
    *,
    cell: EvaluationCell,
    retry_index: int,
) -> Path:
    """Return the stable artifact path for a cell retry attempt."""
    return (
        eval_root
        / "runs"
        / _path_slug(cell.provider)
        / _path_slug(cell.model)
        / _path_slug(cell.task_id)
        / f"attempt-{cell.attempt_index + 1:02d}-retry-{retry_index}.json"
    )


def run_evaluation(
    *,
    eval_root: Path,
    manifest: EvaluationManifest,
    tasks_root: Path,
    execute_live_run: Callable[..., RunArtifact],
    max_workers: int = 1,
    status_callback: Callable[[str], None] | None = None,
) -> EvaluationSummary:
    """Execute all pending cells in the manifest and write evaluation outputs."""
    tasks_by_id = {
        task_id: load_task_by_id(tasks_root, task_id) for task_id in manifest.task_ids
    }
    lock = threading.Lock()
    pending_cells = [cell for cell in manifest.cells if cell.status != "completed"]

    def update_cell(cell: EvaluationCell, **fields: Any) -> None:
        with lock:
            for key, value in fields.items():
                setattr(cell, key, value)
            persist_manifest(eval_root, manifest)

    def run_cell(cell: EvaluationCell) -> None:
        task = tasks_by_id[cell.task_id]
        settings = manifest.provider_settings[cell.provider]
        task_revision = TaskRevision(**manifest.task_revisions[cell.task_id])
        max_attempts = manifest.max_retries + 1
        retry_index = len(cell.artifact_paths)

        while retry_index < max_attempts:
            update_cell(cell, status="running")
            if status_callback is not None:
                status_callback(
                    f"[eval] running {cell.provider}/{cell.model} "
                    f"{cell.task_id} attempt={cell.attempt_index + 1} retry={retry_index}"
                )

            try:
                artifact = execute_live_run(
                    task,
                    provider=cell.provider,
                    provider_model=cell.model,
                    workspace_out=None,
                    agent_name=None,
                    no_stdin_prompt=False,
                    stream=False,
                    heartbeat_seconds=settings["heartbeat_seconds"],
                    runtime=manifest.runtime,
                    runtime_image=manifest.runtime_image,
                    mount_provider_auth=settings["mount_provider_auth"],
                    runtime_home_volume=settings.get("runtime_home_volume"),
                    runtime_home_bind=settings.get("runtime_home_bind"),
                    realism_profile=manifest.realism_profile,
                    agent_cmd=tuple(settings.get("extra_args", [])),
                )
                annotate_artifact(artifact, manifest=manifest, cell=cell)
                infra_failure = None
                last_error = None
            except Exception as exc:  # pragma: no cover - CLI drives the failure paths
                infra_failure = classify_infra_exception(exc)
                last_error = str(exc)
                artifact = make_infra_failure_artifact(
                    task_id=cell.task_id,
                    task_revision=task_revision,
                    provider=cell.provider,
                    model=cell.model,
                    manifest=manifest,
                    cell=cell,
                    infra_failure=infra_failure,
                )

            artifact_path = artifact_path_for_cell(
                eval_root, cell=cell, retry_index=retry_index
            )
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact.to_json(artifact_path)

            with lock:
                cell.artifact_paths.append(str(artifact_path.relative_to(eval_root)))
                cell.retries = retry_index
                cell.infra_failure = infra_failure
                cell.last_error = last_error
                if infra_failure is not None and retry_index + 1 < max_attempts:
                    cell.status = "pending"
                else:
                    cell.status = (
                        "infra_failed" if infra_failure is not None else "completed"
                    )
                persist_manifest(eval_root, manifest)

            if infra_failure is not None and retry_index + 1 < max_attempts:
                retry_index += 1
                if status_callback is not None:
                    status_callback(
                        f"[eval] retrying infra failure {cell.provider}/{cell.model} "
                        f"{cell.task_id} ({infra_failure})"
                    )
                continue
            break

    max_workers = max(1, max_workers)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(run_cell, cell) for cell in pending_cells]
        for future in concurrent.futures.as_completed(futures):
            future.result()

    return write_evaluation_outputs(eval_root, manifest)


def write_evaluation_outputs(
    eval_root: Path, manifest: EvaluationManifest
) -> EvaluationSummary:
    """Recompute and persist summary, leaderboard, and audit files."""
    summary = summarize_evaluation(eval_root)
    summary.to_json(eval_root / "summary.json")
    _write_leaderboard_csv(eval_root / "leaderboard.csv", summary)
    _write_leaderboard_markdown(eval_root / "leaderboard.md", summary)
    audit_path = _write_audit_pack(eval_root, manifest, summary)
    summary.audit_pack_path = str(audit_path.relative_to(eval_root))
    summary.to_json(eval_root / "summary.json")
    return summary


def summarize_evaluation(eval_root: Path) -> EvaluationSummary:
    """Load a manifest, aggregate its artifacts, and return a summary."""
    manifest = EvaluationManifest.from_json(eval_root / "manifest.json")
    artifact_by_cell: dict[str, RunArtifact] = {}
    missing_cells: list[str] = []

    for cell in manifest.cells:
        if not cell.artifact_paths:
            missing_cells.append(cell.cell_id)
            continue
        artifact_path = eval_root / cell.artifact_paths[-1]
        if not artifact_path.exists():
            missing_cells.append(cell.cell_id)
            continue
        artifact = RunArtifact.from_json(artifact_path)
        _validate_artifact_against_manifest(artifact, manifest=manifest, cell=cell)
        artifact_by_cell[cell.cell_id] = artifact

    model_summaries: list[ModelEvaluationSummary] = []
    global_invalid_reasons: list[str] = []
    if missing_cells:
        global_invalid_reasons.append(
            f"{len(missing_cells)} cells missing final artifacts"
        )

    grouped_cells: dict[tuple[str, str], list[EvaluationCell]] = {}
    for cell in manifest.cells:
        grouped_cells.setdefault((cell.provider, cell.model), []).append(cell)

    for (provider, model), cells in sorted(grouped_cells.items()):
        per_task_cells: dict[str, list[EvaluationCell]] = {}
        for cell in cells:
            per_task_cells.setdefault(cell.task_id, []).append(cell)

        invalid_reasons: list[str] = []
        canonical_passes: list[float] = []
        canonical_totals: list[float] = []
        canonical_partials: list[float] = []
        canonical_wall_clock: list[float] = []
        canonical_model_calls: list[float] = []
        canonical_dollar_cost: list[float] = []
        canonical_infra: list[float] = []
        axis_totals: dict[str, list[float]] = {}
        task_passes_at_k: list[float] = []
        best_of_k_totals: list[float] = []
        all_attempt_totals: list[float] = []
        completed_cells = 0
        unresolved_infra = 0

        for task_id in manifest.task_ids:
            task_cells = sorted(
                per_task_cells.get(task_id, []),
                key=lambda item: item.attempt_index,
            )
            artifacts_for_task: list[RunArtifact] = []
            for cell in task_cells:
                artifact = artifact_by_cell.get(cell.cell_id)
                if artifact is None:
                    continue
                completed_cells += 1
                artifacts_for_task.append(artifact)

            canonical = next(
                (artifact_by_cell.get(cell.cell_id) for cell in task_cells if cell.attempt_index == 0),
                None,
            )
            if canonical is None:
                invalid_reasons.append(f"{task_id} missing canonical attempt")
                canonical_passes.append(0.0)
                canonical_totals.append(0.0)
                canonical_partials.append(0.0)
                canonical_wall_clock.append(0.0)
                canonical_model_calls.append(0.0)
                canonical_dollar_cost.append(0.0)
                canonical_infra.append(1.0)
                unresolved_infra += 1
            else:
                is_infra = float(
                    bool(
                        canonical.evaluation
                        and canonical.evaluation.infra_failure is not None
                    )
                )
                if is_infra:
                    unresolved_infra += 1
                    invalid_reasons.append(
                        f"{task_id} unresolved infra failure "
                        f"({canonical.evaluation.infra_failure})"
                    )
                canonical_passes.append(float(canonical.verdict == "pass"))
                canonical_totals.append(canonical.total)
                canonical_partials.append(float(canonical.verdict == "partial"))
                canonical_wall_clock.append(canonical.budget_usage.wall_clock_seconds)
                canonical_model_calls.append(float(canonical.budget_usage.model_calls))
                canonical_dollar_cost.append(float(canonical.budget_usage.dollar_cost))
                canonical_infra.append(is_infra)
                for axis in canonical.axes:
                    axis_totals.setdefault(axis.name, []).append(axis.raw_score)

            for artifact in artifacts_for_task:
                all_attempt_totals.append(artifact.total)

            if manifest.attempts > 1:
                task_passes_at_k.append(
                    float(any(artifact.verdict == "pass" for artifact in artifacts_for_task))
                )
                if artifacts_for_task:
                    best_of_k_totals.append(max(artifact.total for artifact in artifacts_for_task))
                else:
                    best_of_k_totals.append(0.0)

        if missing_cells:
            invalid_reasons.extend(global_invalid_reasons)

        valid_for_public = not invalid_reasons
        ci95 = {
            "task_pass_rate_at_1": list(
                _bootstrap_interval(canonical_passes, manifest.bootstrap_samples)
            ),
            "mean_total_score": list(
                _bootstrap_interval(canonical_totals, manifest.bootstrap_samples)
            ),
        }
        model_summaries.append(
            ModelEvaluationSummary(
                provider=provider,
                model=model,
                task_count=len(manifest.task_ids),
                attempts=manifest.attempts,
                completed_cells=completed_cells,
                valid_for_public_leaderboard=valid_for_public,
                invalid_reasons=sorted(set(invalid_reasons)),
                task_pass_rate_at_1=_mean(canonical_passes),
                task_pass_rate_at_k=_mean(task_passes_at_k) if manifest.attempts > 1 else None,
                best_of_k_mean_total=_mean(best_of_k_totals) if manifest.attempts > 1 else None,
                attempt_score_variance=(
                    statistics.pvariance(all_attempt_totals)
                    if len(all_attempt_totals) > 1
                    else 0.0 if all_attempt_totals else None
                ),
                mean_total_score=_mean(canonical_totals),
                partial_rate=_mean(canonical_partials),
                mean_axis_scores={
                    axis_name: _mean(values)
                    for axis_name, values in sorted(axis_totals.items())
                },
                mean_wall_clock_seconds=_mean(canonical_wall_clock),
                mean_model_calls=_mean(canonical_model_calls),
                mean_dollar_cost=_mean(canonical_dollar_cost),
                infra_error_rate=_mean(canonical_infra),
                unresolved_infra_cells=unresolved_infra,
                bootstrap_ci95=ci95,
            )
        )

    valid_for_public = all(
        item.valid_for_public_leaderboard for item in model_summaries
    )
    invalid_reasons = sorted(
        {
            reason
            for item in model_summaries
            for reason in item.invalid_reasons
        }
    )
    return EvaluationSummary(
        evaluation_id=manifest.evaluation_id,
        benchmark_id=manifest.benchmark_id,
        benchmark_name=manifest.benchmark_name,
        benchmark_status=manifest.benchmark_status,
        manifest_fingerprint=manifest.manifest_fingerprint,
        generated_at=datetime.now(timezone.utc).isoformat(),
        task_set=manifest.task_set,
        task_count=len(manifest.task_ids),
        attempts=manifest.attempts,
        bootstrap_samples=manifest.bootstrap_samples,
        valid_for_public_leaderboard=valid_for_public,
        invalid_reasons=invalid_reasons,
        model_summaries=model_summaries,
    )


def load_summary_rows(
    reports_dir: Path,
    *,
    benchmark_id: str | None = None,
) -> list[tuple[Path, EvaluationSummary, ModelEvaluationSummary]]:
    """Load one row per model summary from summary.json files."""
    rows: list[tuple[Path, EvaluationSummary, ModelEvaluationSummary]] = []
    if not reports_dir.exists():
        return rows
    for path in sorted(reports_dir.rglob("summary.json")):
        summary = EvaluationSummary.from_json(path)
        if benchmark_id is not None and summary.benchmark_id != benchmark_id:
            continue
        for model_summary in summary.model_summaries:
            rows.append((path, summary, model_summary))
    return rows


def latest_summary_rows(
    reports_dir: Path,
    *,
    benchmark_id: str | None = None,
) -> list[tuple[Path, EvaluationSummary, ModelEvaluationSummary]]:
    """Return the latest summary row per benchmark/provider/model."""
    latest: dict[tuple[str, str, str], tuple[Path, EvaluationSummary, ModelEvaluationSummary]] = {}
    for row in load_summary_rows(reports_dir, benchmark_id=benchmark_id):
        path, summary, model_summary = row
        key = (summary.benchmark_id, model_summary.provider, model_summary.model)
        current = latest.get(key)
        if current is None or summary.generated_at > current[1].generated_at:
            latest[key] = row
    return list(latest.values())


def resolve_evaluation_dir(eval_ref: str | Path, reports_dir: Path) -> Path:
    """Resolve an evaluation id or path into an evaluation directory."""
    eval_path = Path(eval_ref)
    if eval_path.is_dir():
        return eval_path
    if eval_path.is_file():
        return eval_path.parent

    matches = [
        path.parent
        for path in reports_dir.rglob("manifest.json")
        if path.parent.name == str(eval_ref)
    ]
    if not matches:
        raise ValueError(f"Evaluation not found: {eval_ref}")
    if len(matches) > 1:
        raise ValueError(f"Evaluation id {eval_ref} is ambiguous under {reports_dir}")
    return matches[0]


def _validate_artifact_against_manifest(
    artifact: RunArtifact,
    *,
    manifest: EvaluationManifest,
    cell: EvaluationCell,
) -> None:
    """Reject stale or foreign artifacts mixed into an evaluation root."""
    evaluation = artifact.evaluation
    if evaluation is None:
        raise ValueError(
            f"Artifact for cell {cell.cell_id} is missing evaluation metadata"
        )
    if evaluation.manifest_fingerprint != manifest.manifest_fingerprint:
        raise ValueError(
            f"Mixed task revisions or harness settings detected in {manifest.evaluation_id}: "
            f"artifact fingerprint {evaluation.manifest_fingerprint[:8]} "
            f"!= manifest {manifest.manifest_fingerprint[:8]}"
        )
    if evaluation.cell_id != cell.cell_id:
        raise ValueError(
            f"Artifact cell mismatch: {evaluation.cell_id} != {cell.cell_id}"
        )
    if artifact.task_revision.combined != cell.task_revision:
        raise ValueError(
            f"Mixed task revisions detected for {cell.task_id}: "
            f"{artifact.task_revision.combined[:8]} != {cell.task_revision[:8]}"
        )


def _bootstrap_interval(values: list[float], samples: int) -> tuple[float, float]:
    """Return a deterministic 95% bootstrap confidence interval."""
    if not values:
        return (0.0, 0.0)
    if len(values) == 1:
        return (values[0], values[0])

    rng = random.Random(0)
    estimates: list[float] = []
    for _ in range(max(1, samples)):
        estimate = _mean([rng.choice(values) for _ in range(len(values))])
        estimates.append(estimate)
    estimates.sort()
    low_index = int(0.025 * (len(estimates) - 1))
    high_index = int(0.975 * (len(estimates) - 1))
    return (estimates[low_index], estimates[high_index])


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return statistics.fmean(values)


def _write_leaderboard_csv(path: Path, summary: EvaluationSummary) -> None:
    """Write a flat leaderboard CSV for the evaluation."""
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "benchmark_id",
                "evaluation_id",
                "benchmark_status",
                "provider",
                "model",
                "valid_for_public_leaderboard",
                "task_pass_rate_at_1",
                "task_pass_rate_at_k",
                "mean_total_score",
                "partial_rate",
                "infra_error_rate",
                "mean_wall_clock_seconds",
                "mean_model_calls",
                "mean_dollar_cost",
                "invalid_reasons",
            ]
        )
        for item in sorted(
            summary.model_summaries,
            key=lambda row: (
                0 if row.valid_for_public_leaderboard else 1,
                -row.task_pass_rate_at_1,
                -row.mean_total_score,
                row.provider,
                row.model,
            ),
        ):
            writer.writerow(
                [
                    summary.benchmark_id,
                    summary.evaluation_id,
                    summary.benchmark_status,
                    item.provider,
                    item.model,
                    str(item.valid_for_public_leaderboard).lower(),
                    f"{item.task_pass_rate_at_1:.6f}",
                    "" if item.task_pass_rate_at_k is None else f"{item.task_pass_rate_at_k:.6f}",
                    f"{item.mean_total_score:.6f}",
                    f"{item.partial_rate:.6f}",
                    f"{item.infra_error_rate:.6f}",
                    f"{item.mean_wall_clock_seconds:.6f}",
                    f"{item.mean_model_calls:.6f}",
                    f"{item.mean_dollar_cost:.6f}",
                    " | ".join(item.invalid_reasons),
                ]
            )


def _write_leaderboard_markdown(path: Path, summary: EvaluationSummary) -> None:
    """Write a markdown leaderboard for the evaluation."""
    headers = [
        "Provider/Model",
        "Valid",
        "Pass@1",
        "Pass@K",
        "Mean Total",
        "Partial",
        "Infra",
    ]
    lines = [
        f"# {summary.benchmark_name} {summary.task_set}",
        "",
        (
            f"Evaluation `{summary.evaluation_id}` on benchmark `{summary.benchmark_id}` "
            f"(status: `{summary.benchmark_status}`)"
        ),
        "",
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for item in sorted(
        summary.model_summaries,
        key=lambda row: (
            0 if row.valid_for_public_leaderboard else 1,
            -row.task_pass_rate_at_1,
            -row.mean_total_score,
            row.provider,
            row.model,
        ),
    ):
        lines.append(
            "| "
            + " | ".join(
                [
                    f"{item.provider}/{item.model}",
                    "yes" if item.valid_for_public_leaderboard else "no",
                    f"{item.task_pass_rate_at_1:.3f}",
                    "-" if item.task_pass_rate_at_k is None else f"{item.task_pass_rate_at_k:.3f}",
                    f"{item.mean_total_score:.3f}",
                    f"{item.partial_rate:.3f}",
                    f"{item.infra_error_rate:.3f}",
                ]
            )
            + " |"
        )
    path.write_text("\n".join(lines) + "\n")


def _write_audit_pack(
    eval_root: Path,
    manifest: EvaluationManifest,
    summary: EvaluationSummary,
) -> Path:
    """Write a small stratified sample of notable artifacts for manual audit."""
    samples: dict[str, list[dict[str, Any]]] = {
        "surprising_passes": [],
        "borderline_partials": [],
        "strong_fails": [],
        "infra_failures": [],
    }
    artifact_records: list[tuple[RunArtifact, Path]] = []
    for cell in manifest.cells:
        if not cell.artifact_paths:
            continue
        path = eval_root / cell.artifact_paths[-1]
        if not path.exists():
            continue
        artifact_records.append((RunArtifact.from_json(path), path))

    pass_candidates = sorted(
        (
            (artifact.total, artifact, path)
            for artifact, path in artifact_records
            if artifact.verdict == "pass"
        ),
        key=lambda item: item[0],
    )
    partial_candidates = sorted(
        (
            (abs(artifact.total - 0.8), artifact, path)
            for artifact, path in artifact_records
            if artifact.verdict == "partial"
        ),
        key=lambda item: item[0],
    )
    fail_candidates = sorted(
        (
            (-artifact.total, artifact, path)
            for artifact, path in artifact_records
            if artifact.verdict == "fail"
            and not (
                artifact.evaluation and artifact.evaluation.infra_failure is not None
            )
        ),
        key=lambda item: item[0],
    )
    infra_candidates = [
        (artifact, path)
        for artifact, path in artifact_records
        if artifact.evaluation and artifact.evaluation.infra_failure is not None
    ]

    for _, artifact, path in pass_candidates[: max(1, manifest.audit_sample_size // 3)]:
        samples["surprising_passes"].append(_audit_entry(eval_root, artifact, path))
    for _, artifact, path in partial_candidates[: max(1, manifest.audit_sample_size // 3)]:
        samples["borderline_partials"].append(_audit_entry(eval_root, artifact, path))
    for _, artifact, path in fail_candidates[: max(1, manifest.audit_sample_size // 3)]:
        samples["strong_fails"].append(_audit_entry(eval_root, artifact, path))
    for artifact, path in infra_candidates[:1]:
        samples["infra_failures"].append(_audit_entry(eval_root, artifact, path))

    payload = {
        "evaluation_id": summary.evaluation_id,
        "benchmark_id": summary.benchmark_id,
        "generated_at": summary.generated_at,
        "samples": samples,
    }
    audit_path = eval_root / "audit-pack.json"
    audit_path.write_text(json.dumps(payload, indent=2))
    return audit_path


def _audit_entry(eval_root: Path, artifact: RunArtifact, path: Path) -> dict[str, Any]:
    """Render a compact audit-pack record."""
    return {
        "task_id": artifact.task_id,
        "provider": artifact.agent.get("name", "unknown"),
        "model": artifact.agent.get("model", "unknown"),
        "verdict": artifact.verdict,
        "total": artifact.total,
        "artifact_path": str(path.relative_to(eval_root)),
        "transcript_entries": len(artifact.transcript),
        "tool_usage_entries": len(artifact.tool_usage),
    }


def _path_slug(value: str) -> str:
    """Convert a free-form label into a stable path segment."""
    text = value.strip().lower()
    text = re.sub(r"[^a-z0-9._-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "unknown"
