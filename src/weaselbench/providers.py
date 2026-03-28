"""Provider-specific live-run launch adapters."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from weaselbench.loader import Task

@dataclass
class ProviderLaunchSpec:
    """Concrete external command plus metadata for a live run."""

    command: list[str]
    env: dict[str, str] = field(default_factory=dict)
    pass_prompt_stdin: bool = False


def build_provider_launch(
    provider: str,
    task: Task,
    workspace: Path,
    prompt: str,
    prompt_file: Path | None,
    runtime_dir: Path,
    extra_args: list[str] | None = None,
    model: str | None = None,
) -> ProviderLaunchSpec:
    """Build a provider-specific command line and MCP configuration."""
    extra_args = list(extra_args or [])
    runtime_dir.mkdir(parents=True, exist_ok=True)

    if provider == "claude":
        return _build_claude_launch(
            task,
            workspace,
            prompt,
            runtime_dir,
            extra_args,
            model,
        )
    if provider == "codex":
        return _build_codex_launch(
            task,
            workspace,
            prompt,
            extra_args,
            model,
        )

    raise ValueError(f"Unsupported provider: {provider}")


def _build_claude_launch(
    task: Task,
    workspace: Path,
    prompt: str,
    runtime_dir: Path,
    extra_args: list[str],
    model: str | None,
) -> ProviderLaunchSpec:
    command = [
        "claude",
        "-p",
        "--permission-mode",
        "bypassPermissions",
        "--add-dir",
        str(workspace),
    ]
    command.extend(_claude_default_stream_args(extra_args))
    command.extend(_claude_default_disallowed_tool_args(extra_args))
    if model:
        command.extend(["--model", model])
    command.extend(extra_args)
    # Prompt must go via stdin: --add-dir is variadic and swallows trailing
    # positional arguments, so appending the prompt to the command would make
    # claude interpret it as another directory.
    return ProviderLaunchSpec(
        command=command,
        pass_prompt_stdin=True,
    )


def _build_codex_launch(
    task: Task,
    workspace: Path,
    prompt: str,
    extra_args: list[str],
    model: str | None,
) -> ProviderLaunchSpec:
    command = [
        "codex",
        "exec",
        "-C",
        str(workspace),
        "--skip-git-repo-check",
        "-s",
        "workspace-write",
        "-c",
        "approval_policy=\"never\"",
        "-c",
        f"sandbox_workspace_write.network_access={str(bool(task.data['environment'].get('agent_web_access', False))).lower()}",
    ]
    if model:
        command.extend(["-m", model])
    command.extend(extra_args)
    command.append(prompt)
    return ProviderLaunchSpec(command=command)


def _toml_inline(value) -> str:
    """Render a small Python structure as TOML inline syntax."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_inline(item) for item in value) + "]"
    if isinstance(value, dict):
        items = ", ".join(f"{key}={_toml_inline(item)}" for key, item in value.items())
        return "{" + items + "}"
    return str(value)


def _claude_default_stream_args(args: list[str]) -> list[str]:
    """Return default Claude print-mode streaming flags when not overridden."""
    defaults: list[str] = []
    has_verbose = "--verbose" in args
    has_output_format = "--output-format" in args or any(
        arg.startswith("--output-format=") for arg in args
    )
    if not has_verbose:
        defaults.append("--verbose")
    if not has_output_format:
        defaults.extend(["--output-format", "stream-json"])
    return defaults


def _claude_default_disallowed_tool_args(args: list[str]) -> list[str]:
    """Disallow Claude-only planning/delegation tools for provider parity."""
    explicit_values: list[str] = []
    for index, arg in enumerate(args):
        if arg in ("--disallowedTools", "--disallowed-tools"):
            if index + 1 < len(args):
                explicit_values.append(args[index + 1])
            continue
        if arg.startswith("--disallowedTools=") or arg.startswith("--disallowed-tools="):
            explicit_values.append(arg.split("=", 1)[1])

    seen_tools: set[str] = set()
    for value in explicit_values:
        seen_tools.update(value.replace(",", " ").split())

    required_tools = ("EnterPlanMode", "Task")
    missing = [tool for tool in required_tools if tool not in seen_tools]
    if not missing:
        return []

    return ["--disallowedTools", ",".join(missing)]
