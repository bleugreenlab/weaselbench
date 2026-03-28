"""Tests for task asset preparation."""

from __future__ import annotations

import hashlib
from pathlib import Path

from weaselbench.assets import declared_assets, prepare_task_assets
from weaselbench.loader import Task


def _task_with_assets(task_dir: Path, environment: dict) -> Task:
    return Task(
        data={
            "id": "asset-task",
            "title": "Asset task",
            "workflow": "issue_driven",
            "prompt": "prepare assets",
            "acceptance_criteria": ["assets ready"],
            "environment": {
                "container_image": "test:0.1",
                "allowed_tools": ["shell"],
                **environment,
            },
            "budgets": {
                "wall_clock_minutes": 5,
                "model_calls": 10,
                "dollar_cap": 0.5,
            },
            "labels": {
                "task_family": "migration_and_removal",
                "temptation_types": ["scope_shrink"],
            },
            "verifier": {
                "visible_checks": [],
                "hidden_checks": [
                    {
                        "name": "no-op",
                        "type": "hidden_test",
                        "target": "true",
                        "axis": "functional_completion",
                        "failure_message": "no-op failed",
                    }
                ],
            },
            "scoring": {
                "primary_metric": "task_success_rate",
                "axes": [{"name": "functional_completion", "weight": 1.0}],
            },
        },
        task_dir=task_dir,
    )


def test_prepare_task_assets_via_script(tmp_path: Path):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    script = task_dir / "snapshot.sh"
    script.write_text("#!/bin/sh\nprintf generated > archive.tar.gz\n")
    script.chmod(0o755)

    task = _task_with_assets(
        task_dir,
        {
            "repo_archive": "archive.tar.gz",
            "assets": [
                {
                    "path": "archive.tar.gz",
                    "source": {"type": "script", "path": "snapshot.sh"},
                }
            ],
        },
    )

    prepared = prepare_task_assets(task)
    assert prepared == [task_dir / "archive.tar.gz"]
    assert (task_dir / "archive.tar.gz").read_text() == "generated"


def test_prepare_task_assets_via_download(tmp_path: Path):
    source = tmp_path / "source.tar.gz"
    source.write_text("downloaded\n")
    sha256 = hashlib.sha256(source.read_bytes()).hexdigest()

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    task = _task_with_assets(
        task_dir,
        {
            "repo_archive": "archive.tar.gz",
            "assets": [
                {
                    "path": "archive.tar.gz",
                    "source": {
                        "type": "download",
                        "url": source.resolve().as_uri(),
                        "sha256": sha256,
                    },
                }
            ],
        },
    )

    prepared = prepare_task_assets(task)
    assert prepared == [task_dir / "archive.tar.gz"]
    assert (task_dir / "archive.tar.gz").read_text() == "downloaded\n"


def test_declared_assets_synthesizes_legacy_snapshot_script_asset(tmp_path: Path):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    script = task_dir / "snapshot.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)

    task = _task_with_assets(task_dir, {"repo_archive": "archive.tar.gz"})
    assets = declared_assets(task)
    assert len(assets) == 1
    assert assets[0]["path"] == "archive.tar.gz"
    assert assets[0]["source"]["type"] == "script"
