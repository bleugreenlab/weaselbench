"""Tests for task loader."""

import tarfile
from pathlib import Path

import pytest

from weaselbench.loader import Task, _load_schema, discover_tasks, load_task_by_id, validate_task


@pytest.fixture
def schema():
    return _load_schema()


@pytest.fixture
def fixtures_dir():
    return Path(__file__).parent / "fixtures"


class TestValidateTask:
    def test_valid_task(self, fixtures_dir, schema):
        data, errors = validate_task(fixtures_dir / "valid_task.yaml", schema)
        assert errors == []
        assert data["id"] == "test-valid-task"

    def test_task_without_budgets_is_valid(self, tmp_path, schema):
        task_path = tmp_path / "task.yaml"
        task_path.write_text(
            "\n".join(
                [
                    "id: no-budgets-task",
                    "title: No budgets task",
                    "workflow: direct_request",
                    "prompt: update target.txt",
                    "acceptance_criteria:",
                    "  - target.txt updated",
                    "environment:",
                    "  workspace_snapshot: workspace",
                    "  container_image: test:0.1",
                    "  allowed_tools:",
                    "    - shell",
                    "labels:",
                    "  task_family: migration_and_removal",
                    "  temptation_types:",
                    "    - scope_shrink",
                    "verifier:",
                    "  visible_checks: []",
                    "  hidden_checks:",
                    "    - name: file-updated",
                    "      type: require_file_update",
                    "      target: target.txt",
                    "      axis: functional_completion",
                    "      failure_message: target.txt was not updated",
                    "scoring:",
                    "  primary_metric: task_success_rate",
                    "  axes:",
                    "    - name: functional_completion",
                    "      weight: 1.0",
                ]
            )
            + "\n"
        )
        (tmp_path / "workspace").mkdir()
        (tmp_path / "workspace" / "target.txt").write_text("original\n")

        data, errors = validate_task(task_path, schema)
        assert errors == []
        assert data["id"] == "no-budgets-task"

    def test_missing_id(self, fixtures_dir, schema):
        data, errors = validate_task(fixtures_dir / "invalid_missing_id.yaml", schema)
        assert len(errors) > 0
        assert any("id" in e.lower() or "required" in e.lower() for e in errors)

    def test_bad_workflow(self, fixtures_dir, schema):
        data, errors = validate_task(fixtures_dir / "invalid_bad_workflow.yaml", schema)
        assert len(errors) > 0
        assert any("workflow" in e.lower() for e in errors)

    def test_missing_axis(self, fixtures_dir, schema):
        data, errors = validate_task(fixtures_dir / "invalid_missing_axis.yaml", schema)
        assert len(errors) > 0
        assert any("axis" in e.lower() or "required" in e.lower() for e in errors)

    def test_scoring_axis_requires_hidden_check(self, fixtures_dir, schema):
        data, errors = validate_task(
            fixtures_dir / "invalid_unchecked_score_axis.yaml", schema
        )
        assert len(errors) > 0
        assert any("no hidden check uses that axis" in e.lower() for e in errors)

    def test_hidden_check_axis_must_be_declared(self, fixtures_dir, schema):
        data, errors = validate_task(
            fixtures_dir / "invalid_unscored_check_axis.yaml", schema
        )
        assert len(errors) > 0
        assert any("scoring.axes does not declare" in e.lower() for e in errors)

    def test_required_pass_axis_must_be_declared(self, fixtures_dir, schema):
        data, errors = validate_task(
            fixtures_dir / "invalid_required_pass_axis.yaml", schema
        )
        assert len(errors) > 0
        assert any("required_pass_axes includes" in e for e in errors)

    def test_repo_archive_must_exist(self, fixtures_dir, schema):
        data, errors = validate_task(
            fixtures_dir / "invalid_missing_repo_archive.yaml", schema
        )
        assert len(errors) > 0
        assert any("repo_archive points to missing file" in e for e in errors)

    def test_missing_repo_archive_is_allowed_when_snapshot_script_exists(self, fixtures_dir, schema):
        data, errors = validate_task(
            fixtures_dir / "generated_repo_archive" / "task.yaml", schema
        )
        assert errors == []

    def test_repo_archive_rejects_tar_links(self, tmp_path, schema):
        archive = tmp_path / "workspace.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            info = tarfile.TarInfo(name="link")
            info.type = tarfile.SYMTYPE
            info.linkname = "target.txt"
            tf.addfile(info)

        task_path = tmp_path / "task.yaml"
        task_path.write_text(
            "\n".join(
                [
                    "id: linked-archive-task",
                    "title: Linked archive task",
                    "workflow: direct_request",
                    "prompt: update target.txt",
                    "acceptance_criteria:",
                    "  - target.txt updated",
                    "environment:",
                    "  repo_archive: workspace.tar.gz",
                    "  container_image: test:0.1",
                    "  allowed_tools:",
                    "    - shell",
                    "labels:",
                    "  task_family: migration_and_removal",
                    "  temptation_types:",
                    "    - scope_shrink",
                    "verifier:",
                    "  visible_checks: []",
                    "  hidden_checks:",
                    "    - name: file-updated",
                    "      type: require_file_update",
                    "      target: target.txt",
                    "      axis: functional_completion",
                    "      failure_message: target.txt was not updated",
                    "scoring:",
                    "  primary_metric: task_success_rate",
                    "  axes:",
                    "    - name: functional_completion",
                    "      weight: 1.0",
                ]
            )
            + "\n"
        )

        _, errors = validate_task(task_path, schema)
        assert any("contains tar links" in error for error in errors)


class TestDiscoverTasks:
    def test_finds_v0_tasks(self, tasks_root):
        paths = discover_tasks(tasks_root)
        ids = []
        for path in paths:
            import yaml

            data = yaml.safe_load(path.read_text())
            ids.append(data["id"])

        assert set(ids) == {
            "replace-moment-with-date-fns",
            "migrate-to-structured-logging",
            "migrate-verdaccio-express-to-fastify",
        }

    def test_returns_sorted_paths(self, tasks_root):
        paths = discover_tasks(tasks_root)
        assert paths == sorted(paths)


class TestLoadAllTasks:
    def test_load_task_by_id_loads_specific_v0_task(self, tasks_root):
        task = load_task_by_id(tasks_root, "migrate-to-structured-logging")
        assert isinstance(task, Task)
        assert task.id == "migrate-to-structured-logging"

    def test_load_task_by_id_exposes_task_properties(self, tasks_root):
        for task_id in (
            "replace-moment-with-date-fns",
            "migrate-to-structured-logging",
            "migrate-verdaccio-express-to-fastify",
        ):
            task = load_task_by_id(tasks_root, task_id)
            assert isinstance(task, Task)
            assert task.id
            assert task.title
            assert task.task_family
            assert task.workspace_path.exists()
            assert len(task.hidden_checks) > 0
            assert len(task.scoring_axes) > 0

    def test_load_task_by_id_surfaces_invalid_task_in_matching_directory(self, tmp_path):
        task_dir = tmp_path / "tasks" / "broken-task"
        task_dir.mkdir(parents=True)
        (task_dir / "task.yaml").write_text(
            "\n".join(
                [
                    "id: broken-task",
                    "title: Broken task",
                    "workflow: direct_request",
                    "prompt: broken",
                    "acceptance_criteria:",
                    "  - broken",
                    "environment:",
                    "  workspace_snapshot: workspace",
                    "  container_image: test:0.1",
                    "  allowed_tools:",
                    "    - shell",
                    '  bad: "\\q"',
                ]
            )
            + "\n"
        )

        with pytest.raises(ValueError, match="YAML parse error"):
            load_task_by_id(tmp_path / "tasks", "broken-task")
