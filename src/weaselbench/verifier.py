"""Check execution engine.

Dispatches each check to a type-specific handler in the order:
1. Filesystem checks (forbid_path, require_file_update)
2. Structural checks (forbid_import, require_import)
3. Shell checks (hidden_test)
"""

from __future__ import annotations

from pathlib import Path

from weaselbench.artifacts import CheckResultRecord
from weaselbench.checks.filesystem import check_forbid_path, check_require_file_update
from weaselbench.checks.runner_checks import check_hidden_test
from weaselbench.checks.structural import check_forbid_import, check_require_import, check_require_import_all
from weaselbench.loader import Task

# Check types grouped by execution phase
_FILESYSTEM_TYPES = {"forbid_path", "require_file_update"}
_STRUCTURAL_TYPES = {"forbid_import", "require_import", "require_import_all"}
_SHELL_TYPES = {"hidden_test"}
def run_hidden_checks(
    task: Task,
    workspace: Path,
    snapshot: dict[str, str],
) -> list[CheckResultRecord]:
    """Execute all hidden checks in the correct order."""
    checks = task.hidden_checks
    results: list[CheckResultRecord] = []

    # Phase 1: Filesystem
    for check in checks:
        if check["type"] in _FILESYSTEM_TYPES:
            results.append(
                _dispatch_check(
                    check,
                    task,
                    workspace,
                    snapshot,
                )
            )

    # Phase 2: Structural
    for check in checks:
        if check["type"] in _STRUCTURAL_TYPES:
            results.append(
                _dispatch_check(
                    check,
                    task,
                    workspace,
                    snapshot,
                )
            )

    # Phase 3: Shell
    for check in checks:
        if check["type"] in _SHELL_TYPES:
            results.append(
                _dispatch_check(
                    check,
                    task,
                    workspace,
                    snapshot,
                )
            )

    return results


def _dispatch_check(
    check: dict,
    task: Task,
    workspace: Path,
    snapshot: dict[str, str],
) -> CheckResultRecord:
    """Dispatch a single check to its handler."""
    check_type = check["type"]
    name = check["name"]
    axis = check["axis"]
    target = check.get("target", "")
    file_globs = check.get("files")
    failure_message = check["failure_message"]

    if check_type == "forbid_path":
        return check_forbid_path(name, target, workspace, axis, failure_message)

    if check_type == "require_file_update":
        return check_require_file_update(name, target, workspace, snapshot, axis, failure_message)

    if check_type == "forbid_import":
        return check_forbid_import(name, target, workspace, file_globs, axis, failure_message)

    if check_type == "require_import":
        return check_require_import(name, target, workspace, file_globs, axis, failure_message)

    if check_type == "require_import_all":
        return check_require_import_all(name, target, workspace, file_globs, axis, failure_message)

    if check_type == "hidden_test":
        return check_hidden_test(name, target, workspace, axis, failure_message)

    return CheckResultRecord(
        name=name,
        type=check_type,
        axis=axis,
        passed=False,
        message=f"Unknown check type: {check_type}",
    )
