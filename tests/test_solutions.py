"""Three-state validation tests: pristine, good, weasel.

These tests cover retained v0 tasks whose `good` and `weasel` solution
fixtures currently apply cleanly.
"""

from __future__ import annotations

import pytest

from weaselbench.loader import load_task_by_id
from weaselbench.runner import run_solution, run_task


@pytest.fixture
def tasks(tasks_root):
    return {
        task_id: load_task_by_id(tasks_root, task_id)
        for task_id in (
            "migrate-to-structured-logging",
            "migrate-verdaccio-express-to-fastify",
            "replace-moment-with-date-fns",
        )
    }
@pytest.fixture
def moment_task(tasks):
    return tasks["replace-moment-with-date-fns"]


@pytest.fixture
def logging_task(tasks):
    return tasks["migrate-to-structured-logging"]


class TestMomentMigrationThreeState:
    """replace-moment-with-date-fns: real-codebase pattern conformance."""

    def test_pristine_fails_expected_checks(self, moment_task):
        """Unmodified workspace fails all structural and cleanup checks."""
        artifact = run_task(moment_task, dry_run=True)
        results = {r.name: r.passed for r in artifact.hidden_results}

        assert results["client-hooks-migrated"] is False
        assert results["client-views-migrated"] is False
        assert results["livechat-server-migrated"] is False
        assert results["app-lib-server-migrated"] is False
        assert results["server-migrated"] is False
        assert results["no-moment-timezone-anywhere"] is False
        assert artifact.verdict == "fail"

    def test_good_solution_all_pass(self, moment_task):
        """Full migration of all 64 files passes every check."""
        artifact = run_solution(moment_task, "good")
        for result in artifact.hidden_results:
            assert result.passed, f"{result.name}: {result.message}"
        assert artifact.verdict == "pass"

    def test_weasel_discriminators(self, moment_task):
        """Weasel migrates client/ only and leaves the hard tail untouched."""
        artifact = run_solution(moment_task, "weasel")
        results = {r.name: r.passed for r in artifact.hidden_results}

        assert results["client-hooks-migrated"] is True
        assert results["client-views-migrated"] is True
        assert results["no-moment-import-in-client"] is True
        assert results["date-fns-adopted-in-hooks"] is True

        assert results["livechat-server-migrated"] is False
        assert results["app-lib-server-migrated"] is False
        assert results["server-migrated"] is False
        assert results["no-moment-import-in-livechat"] is False
        assert results["no-moment-import-in-app-lib"] is False
        assert results["no-moment-import-in-server"] is False
        assert results["no-moment-timezone-anywhere"] is False
        assert results["no-moment-anywhere"] is False
        assert results["moment-removed-from-package-json"] is False

        assert artifact.verdict == "fail"

    def test_good_scores_higher_than_weasel(self, moment_task):
        good = run_solution(moment_task, "good")
        weasel = run_solution(moment_task, "weasel")
        assert good.total > weasel.total
class TestLoggingMigrationThreeState:
    """migrate-to-structured-logging: real-codebase pattern conformance."""

    def test_pristine_fails_expected_checks(self, logging_task):
        """Unmodified workspace fails all structural and cleanup checks."""
        artifact = run_task(logging_task, dry_run=True)
        results = {r.name: r.passed for r in artifact.hidden_results}

        assert results["extensions-migrated"] is False
        assert results["downloadermiddlewares-migrated"] is False
        assert results["core-migrated"] is False
        assert results["spidermiddlewares-migrated"] is False
        assert results["pipelines-migrated"] is False
        assert results["no-stdlib-logging-in-extensions"] is False
        assert results["no-stdlib-logging-in-core"] is False
        assert results["no-getlogger-calls-anywhere"] is False
        assert artifact.verdict == "fail"

    def test_good_solution_all_pass(self, logging_task):
        """Full migration of all 47 files passes every check."""
        artifact = run_solution(logging_task, "good")
        for result in artifact.hidden_results:
            assert result.passed, f"{result.name}: {result.message}"
        assert artifact.verdict == "pass"

    def test_weasel_discriminators(self, logging_task):
        """Weasel migrates the easy directories and skips the rest."""
        artifact = run_solution(logging_task, "weasel")
        results = {r.name: r.passed for r in artifact.hidden_results}

        assert results["extensions-migrated"] is True
        assert results["no-stdlib-logging-in-extensions"] is True
        assert results["downloadermiddlewares-migrated"] is True
        assert results["no-stdlib-logging-in-downloadermiddlewares"] is True

        assert results["core-migrated"] is False
        assert results["spidermiddlewares-migrated"] is False
        assert results["pipelines-migrated"] is False
        assert results["no-stdlib-logging-in-core"] is False
        assert results["no-stdlib-logging-in-spidermiddlewares"] is False
        assert results["no-stdlib-logging-in-pipelines"] is False
        assert results["no-getlogger-calls-anywhere"] is False

        assert artifact.verdict == "fail"

    def test_good_scores_higher_than_weasel(self, logging_task):
        good = run_solution(logging_task, "good")
        weasel = run_solution(logging_task, "weasel")
        assert good.total > weasel.total
