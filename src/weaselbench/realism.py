"""Realism profiles for benchmark detectability reduction.

A RealismProfile bundles all environment-presentation decisions (paths,
filenames, env vars, naming) into a frozen, versioned config.  Two built-in
profiles ship: ``sterile`` (current behaviour) and ``normal_repo`` (removes
benchmark tells).  The profile is selected per-run via CLI/config, included
in manifest fingerprints, and recorded in run artifacts.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RealismProfile:
    """Frozen, versioned set of environment-presentation knobs."""

    name: str
    version: int  # bump invalidates manifest fingerprints
    visible_workspace: Path
    visible_runtime: Path
    agent_home: Path
    prompt_filename: str | None  # None = skip writing prompt file
    expose_task_env_vars: bool
    tempdir_prefix: str

    def fingerprint_dict(self) -> dict:
        """Return a stable dict suitable for inclusion in manifest hashes."""
        return {
            "name": self.name,
            "version": self.version,
            "visible_workspace": str(self.visible_workspace),
            "visible_runtime": str(self.visible_runtime),
            "agent_home": str(self.agent_home),
            "prompt_filename": self.prompt_filename,
            "expose_task_env_vars": self.expose_task_env_vars,
            "tempdir_prefix": self.tempdir_prefix,
        }


def _sterile_profile() -> RealismProfile:
    """Profile matching all current hardcoded values."""
    return RealismProfile(
        name="sterile",
        version=2,
        visible_workspace=Path("/workspace"),
        visible_runtime=Path("/run/agent"),
        agent_home=Path("/home/agent"),
        prompt_filename="TASK.md",
        expose_task_env_vars=True,
        tempdir_prefix="weaselbench-live-",
    )


def _normal_repo_profile() -> RealismProfile:
    """Profile that removes obvious benchmark tells."""
    return RealismProfile(
        name="normal_repo",
        version=2,
        visible_workspace=Path("/home/dev/work/project"),
        visible_runtime=Path("/tmp/.session"),
        agent_home=Path("/home/dev"),
        prompt_filename=None,  # provider mode: skip file; runner handles non-provider fallback
        expose_task_env_vars=False,
        tempdir_prefix="tmp-run-",
    )


_PROFILE_FACTORIES = {
    "sterile": _sterile_profile,
    "normal_repo": _normal_repo_profile,
}

BUILTIN_PROFILES = frozenset(_PROFILE_FACTORIES)


def resolve_profile(name: str | None) -> RealismProfile:
    """Resolve a profile name to a frozen profile instance.

    Defaults to ``"sterile"`` when *name* is ``None``.
    Raises ``ValueError`` on unknown names.
    """
    if name is None:
        name = "normal_repo"
    factory = _PROFILE_FACTORIES.get(name)
    if factory is None:
        raise ValueError(
            f"Unknown realism profile {name!r}. "
            f"Available: {', '.join(sorted(BUILTIN_PROFILES))}"
        )
    return factory()
