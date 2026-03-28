"""Task asset preparation helpers."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable

from weaselbench.loader import Task


StatusCallback = Callable[[str], None] | None


def declared_assets(task: Task) -> list[dict]:
    """Return normalized asset declarations for a task.

    Backwards compatibility:
    - if a task uses repo_archive and has a task-local snapshot.sh but no
      explicit matching asset declaration, synthesize a script-backed asset.
    """
    assets = list(task.assets)
    env = task.data["environment"]
    if "repo_archive" in env:
        archive_name = env["repo_archive"]
        if not any(asset["path"] == archive_name for asset in assets):
            snapshot_script = task.task_dir / "snapshot.sh"
            if snapshot_script.is_file():
                assets.append(
                    {
                        "path": archive_name,
                        "description": "Generated repo archive",
                        "source": {"type": "script", "path": "snapshot.sh"},
                    }
                )
    return assets


def prepare_task_assets(
    task: Task,
    *,
    force: bool = False,
    status_callback: StatusCallback = None,
) -> list[Path]:
    """Ensure all declared task assets are present and valid."""
    prepared: list[Path] = []
    for asset in declared_assets(task):
        prepared.append(
            _prepare_asset(task, asset, force=force, status_callback=status_callback)
        )
    return prepared


def _prepare_asset(
    task: Task,
    asset: dict,
    *,
    force: bool,
    status_callback: StatusCallback,
) -> Path:
    rel_path = asset["path"]
    asset_path = (task.task_dir / rel_path).resolve()
    source = asset["source"]

    if asset_path.exists() and not force:
        _status(status_callback, f"Asset ready for {task.id}: {rel_path}")
        return asset_path

    asset_path.parent.mkdir(parents=True, exist_ok=True)
    if source["type"] == "script":
        _prepare_asset_via_script(task, rel_path, asset_path, source["path"], status_callback)
    elif source["type"] == "download":
        _prepare_asset_via_download(rel_path, asset_path, source, status_callback)
    else:  # pragma: no cover - schema should prevent this
        raise ValueError(f"Unsupported asset source type: {source['type']}")

    if not asset_path.exists():
        raise RuntimeError(f"Asset preparation did not produce expected file: {rel_path}")
    _status(
        status_callback,
        f"Prepared asset for {task.id}: {rel_path} ({_format_size(asset_path.stat().st_size)})",
    )
    return asset_path


def _prepare_asset_via_script(
    task: Task,
    rel_path: str,
    asset_path: Path,
    script_rel_path: str,
    status_callback: StatusCallback,
) -> None:
    script_path = (task.task_dir / script_rel_path).resolve()
    if not script_path.is_file():
        raise FileNotFoundError(f"Asset script not found: {script_path}")
    _status(status_callback, f"Generating asset for {task.id}: {rel_path} via {script_path.name}")
    result = subprocess.run(
        [str(script_path)],
        cwd=task.task_dir.resolve(),
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to generate asset {rel_path} via {script_path.name} "
            f"(exit {result.returncode}).\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def _prepare_asset_via_download(
    rel_path: str,
    asset_path: Path,
    source: dict,
    status_callback: StatusCallback,
) -> None:
    url = source["url"]
    expected_sha256 = source["sha256"].lower()
    _status(status_callback, f"Downloading asset: {rel_path} from {url}")
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with urllib.request.urlopen(url) as response, tmp_path.open("wb") as fh:
            shutil.copyfileobj(response, fh)
        actual_sha256 = _sha256_file(tmp_path)
        if actual_sha256 != expected_sha256:
            raise RuntimeError(
                f"Downloaded asset checksum mismatch for {rel_path}: "
                f"expected {expected_sha256}, got {actual_sha256}"
            )
        os.replace(tmp_path, asset_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _status(status_callback: StatusCallback, message: str) -> None:
    if status_callback is not None:
        status_callback(message)


def _format_size(num_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    size = float(num_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{num_bytes}B"
