"""Run artifact dataclasses and serialization."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from weaselbench._edits import should_ignore_edit_path


@dataclass
class CheckResultRecord:
    """Result of a single hidden check."""

    name: str
    type: str
    axis: str
    passed: bool
    message: str = ""


@dataclass
class VisibleCheckRecord:
    """Result of a visible (shell command) check."""

    command: str
    exit_code: int
    passed: bool


@dataclass
class AxisScore:
    """Score for a single axis."""

    name: str
    weight: float
    raw_score: float
    weighted_score: float


@dataclass
class BudgetUsage:
    """Resource usage for a run."""

    wall_clock_seconds: float = 0.0
    model_calls: int = 0
    dollar_cost: float = 0.0


@dataclass
class RunStats:
    """Derived run statistics for quick comparison across artifacts."""

    total_tool_calls: int = 0
    agent_tool_calls: int = 0
    changed_files: int = 0
    added_files: int = 0
    modified_files: int = 0
    deleted_files: int = 0


@dataclass
class FinalStateChangedFile:
    """Changed file details captured from the final workspace state."""

    path: str
    change: str
    before_hash: str | None = None
    after_hash: str | None = None
    before_bytes: int | None = None
    after_bytes: int | None = None
    is_text: bool = False
    before_text: str | None = None
    after_text: str | None = None
    content_truncated: bool = False


@dataclass
class FinalState:
    """Captured final-state surface for changed files only."""

    mode: str = "changed_surface"
    changed_files: list[FinalStateChangedFile] = field(default_factory=list)


@dataclass
class TaskRevision:
    """Fingerprints for the exact task variant used in a run."""

    combined: str = "unknown"
    task_spec: str = "unknown"
    prompt: str = "unknown"
    verifier: str = "unknown"
    workspace: str = "unknown"


@dataclass
class RunTermination:
    """How the agent process ended."""

    reason: str = "completed"
    returncode: int | None = None
    idle_timeout_seconds: float | None = None
    absolute_timeout_seconds: float | None = None


@dataclass
class EvaluationMetadata:
    """Evaluation-scoped metadata attached to a run artifact."""

    benchmark_id: str = ""
    evaluation_id: str = ""
    cell_id: str = ""
    attempt_index: int = 0
    provider_model: str = ""
    harness_revision: str = "unknown"
    manifest_fingerprint: str = "unknown"
    runtime: str = "host"
    runtime_fingerprint: str = "unknown"
    runtime_image: str | None = None
    runtime_image_fingerprint: str = "unknown"
    infra_failure: str | None = None
    realism_profile: str | None = None


@dataclass
class RunArtifact:
    """Complete output of a benchmark run."""

    run_id: str
    task_id: str
    started_at: datetime
    ended_at: datetime
    agent: dict[str, str] = field(default_factory=lambda: {"name": "dry-run", "version": "0.0"})
    transcript: list[dict] = field(default_factory=list)
    tool_usage: list[dict] = field(default_factory=list)
    edits: list[dict] = field(default_factory=list)
    budget_usage: BudgetUsage = field(default_factory=BudgetUsage)
    run_stats: RunStats = field(default_factory=RunStats)
    final_state: FinalState | None = None
    task_revision: TaskRevision = field(default_factory=TaskRevision)
    termination: RunTermination | None = None
    evaluation: EvaluationMetadata | None = None
    visible_results: list[VisibleCheckRecord] = field(default_factory=list)
    hidden_results: list[CheckResultRecord] = field(default_factory=list)
    axes: list[AxisScore] = field(default_factory=list)
    total: float = 0.0
    verdict: str = "fail"

    def to_dict(self) -> dict:
        """Serialize to the run-artifact schema shape."""
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
            "agent": self.agent,
            "transcript": self.transcript,
            "tool_usage": self.tool_usage,
            "edits": self.edits,
            "budget_usage": asdict(self.budget_usage),
            "run_stats": asdict(self.run_stats),
            **(
                {"final_state": asdict(self.final_state)}
                if self.final_state is not None
                else {}
            ),
            "task_revision": asdict(self.task_revision),
            **(
                {"termination": asdict(self.termination)}
                if self.termination is not None
                else {}
            ),
            **(
                {"evaluation": asdict(self.evaluation)}
                if self.evaluation is not None
                else {}
            ),
            "check_results": {
                "visible": [asdict(v) for v in self.visible_results],
                "hidden": [asdict(h) for h in self.hidden_results],
            },
            "scores": {
                "axes": [asdict(a) for a in self.axes],
                "total": self.total,
                "verdict": self.verdict,
            },
        }

    def to_json(self, path: Path) -> None:
        """Write artifact to a JSON file."""
        path.write_text(json.dumps(self.to_dict(), indent=2, default=str))

    @classmethod
    def from_json(cls, path: Path) -> RunArtifact:
        """Load artifact from a JSON file."""
        raw = json.loads(path.read_text())

        # Re-filter edits to strip generated artifacts from legacy runs
        raw_edits = raw.get("edits", [])
        edits = [
            e for e in raw_edits
            if not should_ignore_edit_path(e["path"], prompt_filename=None)
        ]

        # Recompute file-count stats from filtered edits
        run_stats_raw = raw.get("run_stats", {})
        run_stats = RunStats(
            total_tool_calls=run_stats_raw.get("total_tool_calls", 0),
            agent_tool_calls=run_stats_raw.get("agent_tool_calls", 0),
            changed_files=len(edits),
            added_files=sum(1 for e in edits if e.get("change") == "added"),
            modified_files=sum(1 for e in edits if e.get("change") == "modified"),
            deleted_files=sum(1 for e in edits if e.get("change") == "deleted"),
        )

        artifact = cls(
            run_id=raw["run_id"],
            task_id=raw["task_id"],
            started_at=datetime.fromisoformat(raw["started_at"]),
            ended_at=datetime.fromisoformat(raw["ended_at"]),
            agent=raw.get("agent", {}),
            transcript=raw.get("transcript", []),
            tool_usage=raw.get("tool_usage", []),
            edits=edits,
            budget_usage=BudgetUsage(
                **raw.get("budget_usage", {}),
            ),
            run_stats=run_stats,
            final_state=(
                FinalState(
                    mode=raw["final_state"].get("mode", "changed_surface"),
                    changed_files=[
                        FinalStateChangedFile(**item)
                        for item in raw["final_state"].get("changed_files", [])
                    ],
                )
                if raw.get("final_state") is not None
                else None
            ),
            task_revision=TaskRevision(
                **raw.get("task_revision", {}),
            ),
            termination=(
                RunTermination(**raw["termination"])
                if raw.get("termination") is not None
                else None
            ),
            evaluation=(
                EvaluationMetadata(**raw["evaluation"])
                if raw.get("evaluation") is not None
                else None
            ),
            visible_results=[
                VisibleCheckRecord(**v)
                for v in raw.get("check_results", {}).get("visible", [])
            ],
            hidden_results=[
                CheckResultRecord(**h)
                for h in raw.get("check_results", {}).get("hidden", [])
            ],
            axes=[AxisScore(**a) for a in raw.get("scores", {}).get("axes", [])],
            total=raw.get("scores", {}).get("total", 0.0),
            verdict=raw.get("scores", {}).get("verdict", "fail"),
        )
        return artifact
