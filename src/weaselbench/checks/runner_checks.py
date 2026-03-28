"""Shell command checks: hidden_test."""

from __future__ import annotations

import subprocess
from pathlib import Path

from weaselbench.artifacts import CheckResultRecord


def check_hidden_test(
    name: str,
    target: str,
    workspace: Path,
    axis: str,
    failure_message: str,
) -> CheckResultRecord:
    """Run a shell command and check exit code."""
    try:
        result = subprocess.run(
            target,
            shell=True,
            cwd=workspace,
            capture_output=True,
            timeout=120,
        )
        passed = result.returncode == 0
    except subprocess.TimeoutExpired:
        return CheckResultRecord(
            name=name,
            type="hidden_test",
            axis=axis,
            passed=False,
            message=f"{failure_message} (timed out)",
        )
    except Exception as e:
        return CheckResultRecord(
            name=name,
            type="hidden_test",
            axis=axis,
            passed=False,
            message=f"{failure_message} (error: {e})",
        )

    if not passed:
        return CheckResultRecord(
            name=name,
            type="hidden_test",
            axis=axis,
            passed=False,
            message=failure_message,
        )

    return CheckResultRecord(
        name=name, type="hidden_test", axis=axis, passed=True
    )
