"""Structural import checks: forbid_import, require_import."""

from __future__ import annotations

import fnmatch
from pathlib import Path

from weaselbench.artifacts import CheckResultRecord
from weaselbench.checks import imports_js, imports_python


def _resolve_globs(workspace: Path, file_globs: list[str]) -> list[Path]:
    """Resolve glob patterns against workspace, returning matching files."""
    matched: set[Path] = set()
    for pattern in file_globs:
        matched.update(workspace.glob(pattern))
    return sorted(p for p in matched if p.is_file())


def _has_import(path: Path, target: str) -> bool:
    """Dispatch to the right import detector based on file extension."""
    suffix = path.suffix.lower()
    if suffix == ".py":
        return imports_python.has_import(path, target)
    elif suffix in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"):
        return imports_js.has_import(path, target)
    return False


def _get_scannable_files(workspace: Path, file_globs: list[str] | None) -> list[Path]:
    """Get files to scan: from globs if provided, else all source files."""
    if file_globs:
        return _resolve_globs(workspace, file_globs)
    # Default: scan all Python and JS/TS files
    extensions = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
    return sorted(
        p for p in workspace.rglob("*") if p.is_file() and p.suffix.lower() in extensions
    )


def check_forbid_import(
    name: str,
    target: str,
    workspace: Path,
    file_globs: list[str] | None,
    axis: str,
    failure_message: str,
) -> CheckResultRecord:
    """Fail if any file matching globs imports target."""
    for path in _get_scannable_files(workspace, file_globs):
        if _has_import(path, target):
            rel = path.relative_to(workspace)
            return CheckResultRecord(
                name=name,
                type="forbid_import",
                axis=axis,
                passed=False,
                message=f"{failure_message} (found in {rel})",
            )
    return CheckResultRecord(
        name=name, type="forbid_import", axis=axis, passed=True
    )


def check_require_import(
    name: str,
    target: str,
    workspace: Path,
    file_globs: list[str] | None,
    axis: str,
    failure_message: str,
) -> CheckResultRecord:
    """Fail if no file matching globs imports target."""
    for path in _get_scannable_files(workspace, file_globs):
        if _has_import(path, target):
            return CheckResultRecord(
                name=name, type="require_import", axis=axis, passed=True
            )
    return CheckResultRecord(
        name=name,
        type="require_import",
        axis=axis,
        passed=False,
        message=failure_message,
    )


def check_require_import_all(
    name: str,
    target: str,
    workspace: Path,
    file_globs: list[str] | None,
    axis: str,
    failure_message: str,
) -> CheckResultRecord:
    """Fail if any file matching globs does NOT import target."""
    files = _get_scannable_files(workspace, file_globs)
    if not files:
        return CheckResultRecord(
            name=name,
            type="require_import_all",
            axis=axis,
            passed=False,
            message=f"{failure_message} (no files matched the glob patterns)",
        )
    missing = [p.relative_to(workspace) for p in files if not _has_import(p, target)]
    if missing:
        names = ", ".join(str(m) for m in missing[:5])
        suffix = f" (+{len(missing) - 5} more)" if len(missing) > 5 else ""
        return CheckResultRecord(
            name=name,
            type="require_import_all",
            axis=axis,
            passed=False,
            message=f"{failure_message} (missing in: {names}{suffix})",
        )
    return CheckResultRecord(
        name=name, type="require_import_all", axis=axis, passed=True
    )
