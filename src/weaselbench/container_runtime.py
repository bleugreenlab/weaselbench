"""Container runtime helpers for provider-backed live runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_VISIBLE_WORKSPACE = Path("/workspace")
DEFAULT_VISIBLE_RUNTIME = Path("/run/agent")
DEFAULT_AGENT_HOME = Path("/home/agent")


@dataclass(frozen=True)
class DockerRuntimeConfig:
    """Container runtime configuration for a live run."""

    image: str
    visible_workspace: Path = DEFAULT_VISIBLE_WORKSPACE
    visible_runtime: Path = DEFAULT_VISIBLE_RUNTIME
    home: Path = DEFAULT_AGENT_HOME
    mount_provider_auth: bool = True
    home_volume: str | None = None
    home_bind: Path | None = None
    network_mode: str | None = None


def build_docker_command(
    inner_command: list[str],
    *,
    config: DockerRuntimeConfig,
    host_workspace: Path,
    host_runtime: Path,
) -> list[str]:
    """Wrap a provider command in a neutral docker execution environment."""
    if config.home_volume and config.home_bind:
        raise ValueError("Specify at most one of home_volume or home_bind")

    command = [
        "docker",
        "run",
        "--rm",
        "-i",
        "--workdir",
        str(config.visible_workspace),
        "-e",
        f"HOME={config.home}",
        "-v",
        f"{host_workspace}:{config.visible_workspace}",
        "-v",
        f"{host_runtime}:{config.visible_runtime}",
    ]

    if config.home_volume:
        command.extend(["-v", f"{config.home_volume}:{config.home}"])
    elif config.home_bind is not None:
        command.extend(["-v", f"{config.home_bind}:{config.home}"])
    elif config.mount_provider_auth:
        command.extend(_provider_auth_mounts(config.home))

    if config.network_mode is not None:
        command.extend(["--network", config.network_mode])

    command.append(config.image)
    command.extend(inner_command)
    return command


def _provider_auth_mounts(home: Path) -> list[str]:
    """Mount provider auth/config homes into the container when present."""
    mounts: list[str] = []
    auth_paths = [
        (Path.home() / ".codex", home / ".codex"),
        (Path.home() / ".claude", home / ".claude"),
        (Path.home() / ".claude.json", home / ".claude.json"),
    ]
    for host, container in auth_paths:
        if host.exists():
            mounts.extend(["-v", f"{host}:{container}"])
    return mounts
