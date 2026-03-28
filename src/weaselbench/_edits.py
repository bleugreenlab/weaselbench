"""Shared helpers for filtering generated / dependency file edits."""

from __future__ import annotations

_IGNORED_EDIT_PREFIXES = (
    "node_modules/",
    ".next/",
    ".turbo/",
    ".vite/",
    "coverage/",
    "dist/",
    "build/",
    "__pycache__/",
    ".pytest_cache/",
    ".mypy_cache/",
)

_IGNORED_EDIT_SUFFIXES = (".pyc", ".pyo")


def should_ignore_edit_path(
    path: str, prompt_filename: str | None = "TASK.md"
) -> bool:
    """Exclude dependency installs and generated build/test output from edit stats."""
    if prompt_filename and path == prompt_filename:
        return True
    if path.startswith(_IGNORED_EDIT_PREFIXES):
        return True
    if path.endswith(_IGNORED_EDIT_SUFFIXES):
        return True
    # Match __pycache__/ anywhere in the path (e.g. pkg/__pycache__/mod.cpython-311.pyc)
    if "/__pycache__/" in path:
        return True
    return False
