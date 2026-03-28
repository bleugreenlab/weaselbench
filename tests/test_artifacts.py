from __future__ import annotations

from datetime import datetime, timezone

from weaselbench.artifacts import RunArtifact, RunStats

UTC = timezone.utc


def test_from_json_refilters_legacy_pycache_edits(tmp_path):
    """Legacy artifacts with __pycache__/.pyc edits get recomputed stats on load."""
    artifact = RunArtifact(
        run_id="test",
        task_id="test",
        started_at=datetime(2026, 3, 27, 12, 0, 0, tzinfo=UTC),
        ended_at=datetime(2026, 3, 27, 12, 1, 0, tzinfo=UTC),
        edits=[
            {"path": "src/app.py", "change": "modified"},
            {"path": "src/__pycache__/app.cpython-311.pyc", "change": "added"},
            {"path": "__pycache__/main.cpython-311.pyc", "change": "added"},
            {"path": ".pytest_cache/README.md", "change": "added"},
            {"path": ".mypy_cache/3.11/stubs.json", "change": "added"},
            {"path": "standalone.pyc", "change": "added"},
        ],
        run_stats=RunStats(
            changed_files=6, added_files=5, modified_files=1,
            total_tool_calls=10, agent_tool_calls=8,
        ),
    )
    path = tmp_path / "artifact.json"
    artifact.to_json(path)

    loaded = RunArtifact.from_json(path)

    # Only the real source edit survives
    assert len(loaded.edits) == 1
    assert loaded.edits[0]["path"] == "src/app.py"

    # File-count stats are recomputed from filtered edits
    assert loaded.run_stats.changed_files == 1
    assert loaded.run_stats.modified_files == 1
    assert loaded.run_stats.added_files == 0
    assert loaded.run_stats.deleted_files == 0

    # Tool-call stats are preserved from the stored data
    assert loaded.run_stats.total_tool_calls == 10
    assert loaded.run_stats.agent_tool_calls == 8


def test_from_json_preserves_clean_edits(tmp_path):
    """Artifacts with no generated files are unchanged after round-trip."""
    artifact = RunArtifact(
        run_id="clean",
        task_id="clean",
        started_at=datetime(2026, 3, 27, 12, 0, 0, tzinfo=UTC),
        ended_at=datetime(2026, 3, 27, 12, 1, 0, tzinfo=UTC),
        edits=[
            {"path": "src/main.py", "change": "modified"},
            {"path": "src/utils.py", "change": "added"},
        ],
        run_stats=RunStats(changed_files=2, added_files=1, modified_files=1),
    )
    path = tmp_path / "artifact.json"
    artifact.to_json(path)

    loaded = RunArtifact.from_json(path)
    assert len(loaded.edits) == 2
    assert loaded.run_stats.changed_files == 2
