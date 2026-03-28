"""Filesystem checks: forbid_path, require_file_update."""

from __future__ import annotations

from pathlib import Path

from weaselbench.artifacts import CheckResultRecord


def check_forbid_path(
    name: str,
    target: str,
    workspace: Path,
    axis: str,
    failure_message: str,
) -> CheckResultRecord:
    """Fail if the target path exists in the workspace."""
    path = workspace / target
    if path.exists():
        return CheckResultRecord(
            name=name,
            type="forbid_path",
            axis=axis,
            passed=False,
            message=failure_message,
        )
    return CheckResultRecord(
        name=name, type="forbid_path", axis=axis, passed=True
    )


def check_require_file_update(
    name: str,
    target: str,
    workspace: Path,
    snapshot: dict[str, str],
    axis: str,
    failure_message: str,
) -> CheckResultRecord:
    """Fail if the target file has not changed from its snapshot hash."""
    path = workspace / target
    if not path.exists():
        return CheckResultRecord(
            name=name,
            type="require_file_update",
            axis=axis,
            passed=False,
            message=f"{failure_message} (file does not exist)",
        )

    current_hash = _hash_file(path)
    original_hash = snapshot.get(target)

    if original_hash is None:
        # File didn't exist in snapshot but exists now — it was created, counts as update
        return CheckResultRecord(
            name=name, type="require_file_update", axis=axis, passed=True
        )

    if current_hash == original_hash:
        return CheckResultRecord(
            name=name,
            type="require_file_update",
            axis=axis,
            passed=False,
            message=failure_message,
        )

    return CheckResultRecord(
        name=name, type="require_file_update", axis=axis, passed=True
    )


def _hash_file(path: Path) -> str:
    """Return a hash of the file contents."""
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot_workspace(workspace: Path) -> dict[str, str]:
    """Hash all files in workspace for later comparison."""
    hashes: dict[str, str] = {}
    for path in sorted(workspace.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(workspace))
            hashes[rel] = _hash_file(path)
    return hashes
