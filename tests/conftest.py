"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def repo_root() -> Path:
    """Root of the weaselbench repo."""
    return Path(__file__).parents[1]


@pytest.fixture
def tasks_root(repo_root) -> Path:
    """Root of the tasks directory."""
    return repo_root / "tasks"


@pytest.fixture
def schema_path(repo_root) -> Path:
    return repo_root / "schemas" / "task.schema.json"
