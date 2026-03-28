"""Task discovery and schema validation."""

from __future__ import annotations

import json
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, ValidationError


@dataclass
class Task:
    """A validated WeaselBench task spec with its filesystem location."""

    data: dict[str, Any]
    task_dir: Path

    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def title(self) -> str:
        return self.data["title"]

    @property
    def task_family(self) -> str:
        return self.data["labels"]["task_family"]

    @property
    def workspace_path(self) -> Path:
        env = self.data["environment"]
        if "repo_archive" in env:
            return self.task_dir / env["repo_archive"]
        return self.task_dir / env["workspace_snapshot"]

    @property
    def workspace_is_archive(self) -> bool:
        return "repo_archive" in self.data["environment"]

    @property
    def hidden_checks(self) -> list[dict]:
        return self.data["verifier"]["hidden_checks"]

    @property
    def visible_checks(self) -> list[str]:
        return self.data["verifier"]["visible_checks"]

    @property
    def verifier_setup_commands(self) -> list[str]:
        return self.data["verifier"].get("setup_commands", [])

    @property
    def scoring_axes(self) -> list[dict]:
        return self.data["scoring"]["axes"]

    @property
    def visible_check_policy(self) -> str:
        return self.data["verifier"].get("visible_check_policy", "advisory")

    @property
    def agent_web_access(self) -> bool:
        return self.data["environment"].get("agent_web_access", False)

    @property
    def setup_network_access(self) -> bool:
        return self.data["environment"].get("setup_network_access", True)

    @property
    def acceptance_criteria(self) -> list[str]:
        return self.data.get("acceptance_criteria", [])

    @property
    def assets(self) -> list[dict[str, Any]]:
        return self.data["environment"].get("assets", [])

    @property
    def provider_runtime(self) -> dict[str, Any]:
        return self.data["environment"].get("provider_runtime", {})


def _load_schema(schema_path: Path | None = None) -> dict:
    if schema_path is None:
        schema_path = Path(__file__).parents[2] / "schemas" / "task.schema.json"
    return json.loads(schema_path.read_text())


def discover_tasks(root: Path) -> list[Path]:
    """Find all task.yaml files under root."""
    return sorted(root.rglob("task.yaml"))


def validate_task(
    task_path: Path, schema: dict | None = None
) -> tuple[dict, list[str]]:
    """Parse YAML and validate against schema. Returns (data, errors)."""
    if schema is None:
        schema = _load_schema()

    try:
        data = yaml.safe_load(task_path.read_text())
    except Exception as e:
        return {}, [f"YAML parse error: {e}"]

    if not isinstance(data, dict):
        return {}, ["Task file does not contain a YAML mapping"]

    validator = Draft202012Validator(schema)
    errors = sorted(
        (f"{'.'.join(str(p) for p in e.absolute_path)}: {e.message}" if e.absolute_path else e.message
         for e in validator.iter_errors(data)),
    )

    if not errors:
        errors.extend(_semantic_validation_errors(data, task_path))

    return data, errors


def _semantic_validation_errors(data: dict[str, Any], task_path: Path) -> list[str]:
    """Validate semantic constraints that JSON Schema does not express well."""
    errors: list[str] = []

    hidden_checks = data.get("verifier", {}).get("hidden_checks", [])
    scoring_axes = data.get("scoring", {}).get("axes", [])
    env = data.get("environment", {})
    task_dir = task_path.parent

    declared_axes = {axis["name"] for axis in scoring_axes}
    used_axes = {check["axis"] for check in hidden_checks}
    required_pass_axes = set(data.get("scoring", {}).get("required_pass_axes", []))

    for axis_name in sorted(declared_axes - used_axes):
        errors.append(
            f"scoring.axes declares '{axis_name}' but no hidden check uses that axis"
        )

    for axis_name in sorted(used_axes - declared_axes):
        errors.append(
            f"hidden_checks uses axis '{axis_name}' but scoring.axes does not declare it"
        )

    for axis_name in sorted(required_pass_axes - declared_axes):
        errors.append(
            f"scoring.required_pass_axes includes '{axis_name}' but scoring.axes does not declare it"
        )

    if "repo_archive" in env:
        archive_path = task_dir / env["repo_archive"]
        asset_paths = {asset["path"] for asset in env.get("assets", [])}
        snapshot_script = task_dir / "snapshot.sh"
        if (
            not archive_path.exists()
            and env["repo_archive"] not in asset_paths
            and not snapshot_script.is_file()
        ):
            errors.append(
                f"environment.repo_archive points to missing file: {archive_path.relative_to(task_dir)}"
            )
        if archive_path.is_file():
            errors.extend(_repo_archive_validation_errors(archive_path, task_dir))
    if "workspace_snapshot" in env:
        workspace_path = task_dir / env["workspace_snapshot"]
        if not workspace_path.exists():
            errors.append(
                f"environment.workspace_snapshot points to missing path: {workspace_path.relative_to(task_dir)}"
            )

    for asset in env.get("assets", []):
        source = asset["source"]
        if source["type"] == "script":
            script_path = task_dir / source["path"]
            if not script_path.is_file():
                errors.append(
                    f"environment.assets script source points to missing file: {script_path.relative_to(task_dir)}"
                )

    provider_runtime = env.get("provider_runtime")
    if provider_runtime:
        runtime = provider_runtime.get("runtime")
        runtime_image = provider_runtime.get("runtime_image")
        if runtime == "docker" and not runtime_image:
            errors.append(
                "environment.provider_runtime.runtime_image is required when runtime is 'docker'"
            )
        if runtime == "host" and runtime_image:
            errors.append(
                "environment.provider_runtime.runtime_image is only valid when runtime is 'docker'"
            )

    return errors


def _repo_archive_validation_errors(archive_path: Path, task_dir: Path) -> list[str]:
    """Validate checked-in repo archives for extraction-time hazards."""
    try:
        with tarfile.open(archive_path) as tf:
            links = [member for member in tf.getmembers() if member.issym() or member.islnk()]
    except tarfile.TarError as exc:
        return [
            "environment.repo_archive is not a readable tar archive: "
            f"{archive_path.relative_to(task_dir)} ({exc})"
        ]

    if not links:
        return []

    first = links[0]
    return [
        "environment.repo_archive contains tar links; regenerate it with links "
        f"dereferenced: {archive_path.relative_to(task_dir)} "
        f"({first.name} -> {first.linkname})"
    ]


def load_all_tasks(root: Path) -> list[Task]:
    """Discover and validate all tasks under root. Raises on any error."""
    schema = _load_schema()
    tasks: list[Task] = []
    all_errors: list[str] = []

    for task_path in discover_tasks(root):
        data, errors = validate_task(task_path, schema)
        if errors:
            all_errors.extend(f"{task_path}: {e}" for e in errors)
        else:
            tasks.append(Task(data=data, task_dir=task_path.parent))

    if all_errors:
        raise ValueError(
            f"Task validation errors:\n" + "\n".join(f"  - {e}" for e in all_errors)
        )

    return tasks


def load_task_by_id(root: Path, task_id: str) -> Task:
    """Load and validate a single task by id without requiring sibling tasks to validate."""
    schema = _load_schema()
    candidate_errors: list[tuple[Path, list[str]]] = []
    for task_path in discover_tasks(root):
        data, errors = validate_task(task_path, schema)
        if data.get("id") != task_id:
            if errors and task_path.parent.name == task_id:
                candidate_errors.append((task_path, errors))
            continue
        if errors:
            raise ValueError(
                f"Task validation errors:\n"
                + "\n".join(f"  - {task_path}: {error}" for error in errors)
            )
        return Task(data=data, task_dir=task_path.parent)
    if candidate_errors:
        raise ValueError(
            "Task validation errors:\n"
            + "\n".join(
                f"  - {task_path}: {error}"
                for task_path, errors in candidate_errors
                for error in errors
            )
        )
    raise ValueError(f"Task not found: {task_id}")
