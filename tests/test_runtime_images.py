"""Tests for local docker runtime image helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

from weaselbench.runtime_images import ensure_docker_image, local_dockerfile_for_image


def test_local_dockerfile_for_image_maps_weaselbench_names(tmp_path: Path):
    agent_runtime = tmp_path / "containers" / "agent-runtime"
    agent_runtime.mkdir(parents=True)
    (agent_runtime / "Dockerfile").write_text("FROM scratch\n")

    go_container = tmp_path / "containers" / "go"
    go_container.mkdir(parents=True)
    (go_container / "Dockerfile").write_text("FROM scratch\n")

    assert local_dockerfile_for_image(
        "weaselbench-agent-runtime:local",
        repo_root=tmp_path,
    ) == agent_runtime / "Dockerfile"
    assert local_dockerfile_for_image(
        "weaselbench/go:0.1",
        repo_root=tmp_path,
    ) == go_container / "Dockerfile"
    assert local_dockerfile_for_image(
        "ghcr.io/example/custom:latest",
        repo_root=tmp_path,
    ) is None


def test_ensure_docker_image_builds_missing_local_image(tmp_path: Path, monkeypatch):
    go_container = tmp_path / "containers" / "go"
    go_container.mkdir(parents=True)
    dockerfile = go_container / "Dockerfile"
    dockerfile.write_text("FROM scratch\n")

    calls: list[list[str]] = []
    inspect_calls = 0

    def fake_run(cmd, capture_output, text, timeout):
        nonlocal inspect_calls
        calls.append(cmd)
        if cmd[:3] == ["docker", "image", "inspect"]:
            inspect_calls += 1
            return subprocess.CompletedProcess(cmd, 1, "", "missing")
        if cmd[:2] == ["docker", "build"]:
            return subprocess.CompletedProcess(cmd, 0, "built", "")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("weaselbench.runtime_images.subprocess.run", fake_run)

    image = ensure_docker_image("weaselbench/go:0.1", repo_root=tmp_path)

    assert image == "weaselbench/go:0.1"
    assert inspect_calls == 2
    assert calls[-1] == [
        "docker",
        "build",
        "-f",
        str(dockerfile),
        "-t",
        "weaselbench/go:0.1",
        str(tmp_path),
    ]


def test_ensure_docker_image_retries_timeout_then_uses_existing_image(
    tmp_path: Path, monkeypatch
):
    go_container = tmp_path / "containers" / "go"
    go_container.mkdir(parents=True)
    (go_container / "Dockerfile").write_text("FROM scratch\n")

    calls: list[tuple[list[str], int]] = []
    inspect_attempts = 0
    status_messages: list[str] = []

    def fake_run(cmd, capture_output, text, timeout):
        nonlocal inspect_attempts
        calls.append((cmd, timeout))
        if cmd[:3] == ["docker", "image", "inspect"]:
            inspect_attempts += 1
            if inspect_attempts == 1:
                raise subprocess.TimeoutExpired(cmd, timeout)
            return subprocess.CompletedProcess(cmd, 0, "present", "")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("weaselbench.runtime_images.subprocess.run", fake_run)

    image = ensure_docker_image(
        "weaselbench/go:0.1",
        repo_root=tmp_path,
        status_callback=status_messages.append,
    )

    assert image == "weaselbench/go:0.1"
    assert inspect_attempts == 2
    assert calls == [
        (["docker", "image", "inspect", "weaselbench/go:0.1"], 30),
        (["docker", "image", "inspect", "weaselbench/go:0.1"], 60),
    ]
    assert status_messages == [
        "Docker image lookup for weaselbench/go:0.1 timed out after 30s; retrying"
    ]
