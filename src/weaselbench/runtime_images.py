"""Helpers for task-scoped docker runtime images."""

from __future__ import annotations

import re
import subprocess
import threading
from pathlib import Path
from typing import Callable


StatusCallback = Callable[[str], None] | None

_LOCKS_GUARD = threading.Lock()
_IMAGE_LOCKS: dict[str, threading.Lock] = {}
_IMAGE_INSPECT_TIMEOUTS = (30, 60, 120)


def ensure_docker_image(
    image: str,
    *,
    repo_root: Path | None = None,
    status_callback: StatusCallback = None,
) -> str:
    """Build a local weaselbench docker image on demand when a Dockerfile exists."""
    repo_root = repo_root or Path(__file__).resolve().parents[2]
    dockerfile = local_dockerfile_for_image(image, repo_root=repo_root)
    if dockerfile is None:
        return image
    if _docker_image_exists(image, status_callback=status_callback):
        return image

    lock = _lock_for_image(image)
    with lock:
        if _docker_image_exists(image, status_callback=status_callback):
            return image
        _emit_status(
            status_callback,
            f"Building local docker image {image} from {dockerfile.relative_to(repo_root)}",
        )
        result = subprocess.run(
            ["docker", "build", "-f", str(dockerfile), "-t", image, str(repo_root)],
            capture_output=True,
            text=True,
            timeout=1800,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to build docker image {image} from {dockerfile} "
                f"(exit {result.returncode}).\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
    return image


def local_dockerfile_for_image(image: str, *, repo_root: Path | None = None) -> Path | None:
    """Return the in-repo Dockerfile that builds a named weaselbench image."""
    repo_root = repo_root or Path(__file__).resolve().parents[2]
    if image == "weaselbench-agent-runtime:local":
        dockerfile = repo_root / "containers" / "agent-runtime" / "Dockerfile"
        return dockerfile if dockerfile.is_file() else None

    match = re.fullmatch(r"weaselbench/([a-z0-9._-]+):[a-zA-Z0-9._-]+", image)
    if match is None:
        return None
    dockerfile = repo_root / "containers" / match.group(1) / "Dockerfile"
    return dockerfile if dockerfile.is_file() else None


def _docker_image_exists(
    image: str, *, status_callback: StatusCallback = None
) -> bool:
    last_error: subprocess.TimeoutExpired | None = None
    for attempt, timeout_seconds in enumerate(_IMAGE_INSPECT_TIMEOUTS, start=1):
        try:
            result = subprocess.run(
                ["docker", "image", "inspect", image],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            return result.returncode == 0
        except subprocess.TimeoutExpired as exc:
            last_error = exc
            if attempt < len(_IMAGE_INSPECT_TIMEOUTS):
                _emit_status(
                    status_callback,
                    "Docker image lookup for "
                    f"{image} timed out after {timeout_seconds}s; retrying",
                )
                continue
    raise RuntimeError(
        "Timed out while checking local docker image "
        f"{image} after {len(_IMAGE_INSPECT_TIMEOUTS)} attempts. "
        "Docker may be temporarily unresponsive."
    ) from last_error


def _lock_for_image(image: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _IMAGE_LOCKS.setdefault(image, threading.Lock())


def _emit_status(status_callback: StatusCallback, message: str) -> None:
    if status_callback is not None:
        status_callback(message)
