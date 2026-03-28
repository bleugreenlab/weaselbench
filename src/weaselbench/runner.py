"""Runner contract: workspace materialization, check execution, artifact assembly.

Live runs are scored on whether the agent completes the task before ending its
first turn, not on task-authored time or cost budgets. The harness still keeps
very generous liveness guards so broken loops or dead processes do not run
forever.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from weaselbench.artifacts import (
    BudgetUsage,
    FinalState,
    FinalStateChangedFile,
    RunArtifact,
    RunTermination,
    RunStats,
    TaskRevision,
    VisibleCheckRecord,
)
from weaselbench.assets import prepare_task_assets
from weaselbench.checks.filesystem import snapshot_workspace
from weaselbench.container_runtime import (
    DEFAULT_VISIBLE_RUNTIME,
    DEFAULT_VISIBLE_WORKSPACE,
    DockerRuntimeConfig,
    build_docker_command,
)
from weaselbench.loader import Task
from weaselbench.providers import build_provider_launch
from weaselbench.realism import resolve_profile
from weaselbench.runtime_images import ensure_docker_image
from weaselbench.scoring import compute_scores
from weaselbench.verifier import run_hidden_checks
from weaselbench._edits import should_ignore_edit_path as _should_ignore_edit_path


DEFAULT_IDLE_TIMEOUT_SECONDS = 30 * 60.0
DEFAULT_ABSOLUTE_TIMEOUT_SECONDS = 12 * 60 * 60.0
_FINAL_STATE_INLINE_LIMIT_BYTES = 256 * 1024


class AgentRunTimeout(RuntimeError):
    """The agent process exceeded a harness liveness guard."""

    def __init__(self, reason: str, timeout_seconds: float):
        super().__init__(f"{reason} after {timeout_seconds:.1f}s")
        self.reason = reason
        self.timeout_seconds = timeout_seconds


def materialize_workspace(
    task: Task,
    dest: Path,
    *,
    status_callback: Callable[[str], None] | None = None,
    force_asset_setup: bool = False,
) -> Path:
    """Materialize a task workspace into dest."""
    prepare_task_assets(task, force=force_asset_setup, status_callback=status_callback)
    if task.workspace_is_archive:
        _extract_archive(task.workspace_path, dest)
    else:
        shutil.copytree(task.workspace_path, dest)
    return dest


def evaluate_workspace(
    task: Task,
    workspace: Path,
    snapshot: dict[str, str],
    *,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
    agent: dict[str, str] | None = None,
    transcript: list[dict] | None = None,
    tool_usage: list[dict] | None = None,
    edits: list[dict] | None = None,
    run_stats: RunStats | None = None,
    final_state: FinalState | None = None,
    termination: RunTermination | None = None,
    force_fail: bool = False,
) -> RunArtifact:
    """Evaluate a materialized workspace and assemble a run artifact."""
    if started_at is None:
        started_at = datetime.now(timezone.utc)
    if ended_at is None:
        ended_at = datetime.now(timezone.utc)
    visible_results = _run_visible_checks(
        task.visible_checks,
        workspace,
        setup_commands=task.verifier_setup_commands,
    )
    hidden_results = run_hidden_checks(
        task,
        workspace,
        snapshot,
    )

    axes, total, verdict = compute_scores(task, hidden_results)
    has_failing_visible = any(not result.passed for result in visible_results)
    if task.visible_check_policy == "hard_gate" and has_failing_visible:
        verdict = "fail"
    elif (
        task.visible_check_policy == "pass_gate"
        and has_failing_visible
        and verdict == "pass"
    ):
        verdict = "partial"
    if force_fail:
        verdict = "fail"

    wall_clock = (ended_at - started_at).total_seconds()

    return RunArtifact(
        run_id=str(uuid.uuid4()),
        task_id=task.id,
        started_at=started_at,
        ended_at=ended_at,
        agent=agent or {"name": "dry-run", "version": "0.0"},
        transcript=transcript or [],
        tool_usage=tool_usage or [],
        edits=edits or [],
        budget_usage=BudgetUsage(wall_clock_seconds=wall_clock),
        run_stats=run_stats or RunStats(),
        final_state=final_state,
        task_revision=_compute_task_revision(task, snapshot),
        termination=termination,
        visible_results=visible_results,
        hidden_results=hidden_results,
        axes=axes,
        total=total,
        verdict=verdict,
    )


def run_live_agent(
    task: Task,
    agent_cmd: list[str] | None = None,
    *,
    provider: str | None = None,
    provider_args: list[str] | None = None,
    provider_model: str | None = None,
    pass_prompt_stdin: bool = True,
    workspace_out: Path | None = None,
    agent_name: str | None = None,
    stream_output: bool = False,
    heartbeat_seconds: float = 15.0,
    status_callback: Callable[[str], None] | None = None,
    runtime: str = "host",
    runtime_image: str | None = None,
    mount_provider_auth: bool = True,
    runtime_home_volume: str | None = None,
    runtime_home_bind: Path | None = None,
    realism_profile: str | None = None,
    idle_timeout_seconds: float | None = DEFAULT_IDLE_TIMEOUT_SECONDS,
    absolute_timeout_seconds: float | None = DEFAULT_ABSOLUTE_TIMEOUT_SECONDS,
) -> RunArtifact:
    """Run a real external agent CLI inside a task workspace and score the result."""
    if provider is None and not agent_cmd:
        raise ValueError("agent_cmd must not be empty")

    started_at = datetime.now(timezone.utc)
    profile = resolve_profile(realism_profile)

    with tempfile.TemporaryDirectory(prefix=profile.tempdir_prefix) as tmp:
        work_dir = Path(tmp) / "workspace"
        runtime_dir = Path(tmp) / "runtime"
        _emit_status(status_callback, f"Materializing workspace for {task.id} into {work_dir}")
        materialize_workspace(task, work_dir)
        runtime_dir.mkdir(parents=True, exist_ok=True)
        snapshot = snapshot_workspace(work_dir)

        # Determine prompt filename: for normal_repo in provider mode, prompt
        # is delivered via stdin so no file is needed.  Non-provider mode uses
        # the configured filename or falls back to TODO.md.
        prompt_filename = profile.prompt_filename
        prompt_file: Path | None = None
        if prompt_filename is not None:
            prompt_file = (
                runtime_dir / prompt_filename
                if provider is not None
                else work_dir / prompt_filename
            )
            prompt_file.write_text(task.data["prompt"])
        elif provider is None:
            # Non-provider mode always needs a prompt file
            prompt_filename = "TODO.md"
            prompt_file = work_dir / prompt_filename
            prompt_file.write_text(task.data["prompt"])

        baseline_file_state = _capture_workspace_file_state_map(
            work_dir,
            prompt_filename=prompt_filename,
        )

        launch_workspace = work_dir
        launch_prompt_file = prompt_file
        launch_runtime_dir = runtime_dir

        if provider is not None:
            if runtime == "docker":
                launch_workspace = profile.visible_workspace
                launch_prompt_file = (
                    (launch_workspace / prompt_file.name)
                    if prompt_file is not None
                    else None
                )
                launch_runtime_dir = profile.visible_runtime
            launch = build_provider_launch(
                provider,
                task,
                launch_workspace,
                task.data["prompt"],
                launch_prompt_file,
                runtime_dir,
                extra_args=provider_args,
                model=provider_model,
            )
            expanded_cmd = launch.command
            pass_prompt_stdin = launch.pass_prompt_stdin
        else:
            if runtime != "host":
                raise ValueError("Container runtime currently supports provider mode only")
            expanded_cmd = [
                part.format(
                    prompt=task.data["prompt"],
                    prompt_file=str(prompt_file),
                    workspace=str(work_dir),
                    task_id=task.id,
                )
                for part in agent_cmd or []
            ]

        env = os.environ.copy()
        if profile.expose_task_env_vars:
            env["TASK_ID"] = task.id
            env["WORKSPACE_ROOT"] = str(work_dir)
            if prompt_file is not None:
                env["TASK_PROMPT_FILE"] = str(prompt_file)

        termination_reason = "completed"
        _emit_status(
            status_callback,
            "Launching "
            f"{'provider ' + provider if provider else 'agent command'}"
            " with first-turn liveness guards "
            f"(idle={_format_timeout(idle_timeout_seconds)}, "
            f"absolute={_format_timeout(absolute_timeout_seconds)})",
        )

        if runtime == "docker":
            if runtime_image is None:
                raise ValueError("runtime_image is required when runtime='docker'")
            runtime_image = ensure_docker_image(
                runtime_image,
                status_callback=status_callback,
            )
            _emit_status(
                status_callback,
                f"Wrapping provider run in docker image {runtime_image}",
            )
            docker_config = DockerRuntimeConfig(
                image=runtime_image,
                visible_workspace=profile.visible_workspace,
                visible_runtime=profile.visible_runtime,
                home=profile.agent_home,
                mount_provider_auth=mount_provider_auth,
                home_volume=runtime_home_volume,
                home_bind=runtime_home_bind,
            )
            expanded_cmd = build_docker_command(
                expanded_cmd,
                config=docker_config,
                host_workspace=work_dir,
                host_runtime=runtime_dir,
            )

        force_fail = False
        transcript: list[dict] = [
            {
                "role": "user",
                "content": task.data["prompt"],
                "timestamp": started_at.isoformat(),
            }
        ]
        tool_usage: list[dict] = []

        stdout = ""
        stderr = ""
        returncode = 0

        try:
            stdout, stderr, returncode = _run_agent_process(
                expanded_cmd,
                work_dir,
                env,
                task.data["prompt"] if pass_prompt_stdin else None,
                stream_output=stream_output,
                heartbeat_seconds=heartbeat_seconds,
                status_callback=status_callback,
                output_formatter=_provider_output_formatter(provider),
                idle_timeout_seconds=idle_timeout_seconds,
                absolute_timeout_seconds=absolute_timeout_seconds,
            )
            if returncode != 0:
                force_fail = True
                termination_reason = "agent_exit_nonzero"
        except AgentRunTimeout as exc:
            termination_reason = exc.reason
            _emit_status(
                status_callback,
                f"Agent ended by harness liveness guard: {exc.reason}",
            )
            returncode = -1
            force_fail = True

        ended_at = datetime.now(timezone.utc)

        assistant_output = _render_provider_transcript_output(provider, stdout, stderr)
        if not assistant_output and provider is None:
            assistant_output = stdout.strip()
            if stderr.strip():
                if assistant_output:
                    assistant_output += "\n\n[stderr]\n" + stderr.strip()
                else:
                    assistant_output = "[stderr]\n" + stderr.strip()
        if assistant_output:
            transcript.append(
                {
                    "role": "assistant",
                    "content": assistant_output,
                    "timestamp": ended_at.isoformat(),
                }
            )

        tool_usage.append(
            {
                "tool": "agent_cli",
                "args": expanded_cmd,
                "result": {
                    "returncode": returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                },
                "started_at": started_at.isoformat(),
                "ended_at": ended_at.isoformat(),
            }
        )

        provider_tool_usage = _extract_provider_tool_usage(provider, stdout, stderr)
        tool_usage.extend(provider_tool_usage)

        if workspace_out is not None:
            if workspace_out.exists():
                shutil.rmtree(workspace_out)
            shutil.copytree(work_dir, workspace_out)
            _emit_status(status_callback, f"Copied final workspace to {workspace_out}")

        edits = _collect_workspace_edits(
            work_dir, snapshot, prompt_filename=prompt_filename
        )
        final_state = _collect_workspace_final_state(
            work_dir,
            baseline_file_state,
            prompt_filename=prompt_filename,
        )
        run_stats = _compute_run_stats(tool_usage, edits)
        _emit_status(status_callback, "Running visible and hidden checks")
        return evaluate_workspace(
            task,
            work_dir,
            snapshot,
            started_at=started_at,
            ended_at=ended_at,
            agent={
                "name": agent_name or provider or Path(expanded_cmd[0]).name,
                "version": "external",
                "model": provider_model or "default",
            },
            transcript=transcript,
            tool_usage=tool_usage,
            edits=edits,
            run_stats=run_stats,
            final_state=final_state,
            termination=RunTermination(
                reason=termination_reason,
                returncode=returncode,
                idle_timeout_seconds=idle_timeout_seconds,
                absolute_timeout_seconds=absolute_timeout_seconds,
            ),
            force_fail=force_fail,
        )


def _emit_status(callback: Callable[[str], None] | None, message: str) -> None:
    """Emit a human-readable live-run status update."""
    if callback is not None:
        callback(message)


def _run_agent_process(
    command: list[str],
    cwd: Path,
    env: dict[str, str],
    stdin_text: str | None,
    *,
    stream_output: bool,
    heartbeat_seconds: float,
    status_callback: Callable[[str], None] | None,
    output_formatter: Callable[[str, str], list[str]] | None = None,
    idle_timeout_seconds: float | None,
    absolute_timeout_seconds: float | None,
) -> tuple[str, str, int]:
    """Run the agent process, optionally streaming stdout/stderr and heartbeats."""
    proc = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE if stdin_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    if stdin_text is not None and proc.stdin is not None:
        proc.stdin.write(stdin_text)
        proc.stdin.close()

    event_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    def pump(stream, channel: str, sink: list[str]) -> None:
        try:
            for line in iter(stream.readline, ""):
                sink.append(line)
                event_queue.put((channel, line))
        finally:
            stream.close()
            event_queue.put((f"{channel}_closed", None))

    stdout_thread = threading.Thread(
        target=pump,
        args=(proc.stdout, "stdout", stdout_chunks),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=pump,
        args=(proc.stderr, "stderr", stderr_chunks),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    closed = set()
    now = time.monotonic()
    absolute_deadline = (
        now + absolute_timeout_seconds
        if absolute_timeout_seconds is not None and absolute_timeout_seconds > 0
        else None
    )
    idle_deadline = (
        now + idle_timeout_seconds
        if idle_timeout_seconds is not None and idle_timeout_seconds > 0
        else None
    )

    while len(closed) < 2:
        now = time.monotonic()
        remaining_absolute = (
            absolute_deadline - now if absolute_deadline is not None else None
        )
        remaining_idle = idle_deadline - now if idle_deadline is not None else None

        if remaining_absolute is not None and remaining_absolute <= 0:
            proc.kill()
            stdout_thread.join(timeout=1)
            stderr_thread.join(timeout=1)
            raise AgentRunTimeout("absolute_timeout", absolute_timeout_seconds or 0.0)
        if remaining_idle is not None and remaining_idle <= 0:
            proc.kill()
            stdout_thread.join(timeout=1)
            stderr_thread.join(timeout=1)
            raise AgentRunTimeout("idle_timeout", idle_timeout_seconds or 0.0)

        wait_candidates = [heartbeat_seconds]
        if remaining_absolute is not None:
            wait_candidates.append(remaining_absolute)
        if remaining_idle is not None:
            wait_candidates.append(remaining_idle)
        wait_time = min(wait_candidates)
        try:
            channel, payload = event_queue.get(timeout=wait_time)
        except queue.Empty:
            if stream_output:
                _emit_status(status_callback, "Agent still running; waiting for output")
            continue

        if channel.endswith("_closed"):
            closed.add(channel.removesuffix("_closed"))
            continue

        if stream_output and payload is not None:
            prefix = "stdout" if channel == "stdout" else "stderr"
            display_lines = (
                output_formatter(channel, payload)
                if output_formatter is not None
                else [payload.rstrip()]
            )
            for line in display_lines:
                if line:
                    _emit_status(status_callback, f"[agent {prefix}] {line}")
        if payload is not None and idle_timeout_seconds is not None and idle_timeout_seconds > 0:
            idle_deadline = time.monotonic() + idle_timeout_seconds

    returncode = proc.wait(timeout=1)
    stdout = "".join(stdout_chunks)
    stderr = "".join(stderr_chunks)
    return stdout, stderr, returncode


def _format_timeout(seconds: float | None) -> str:
    """Render a timeout for status messages."""
    if seconds is None or seconds <= 0:
        return "disabled"
    if seconds >= 3600:
        return f"{seconds / 3600:.1f}h"
    if seconds >= 60:
        return f"{seconds / 60:.1f}m"
    return f"{seconds:.0f}s"


def _provider_output_formatter(
    provider: str | None,
) -> Callable[[str, str], list[str]] | None:
    """Return a stream-display formatter for a provider, if needed."""
    if provider == "claude":
        return _format_claude_stream_output
    if provider == "codex":
        return _make_codex_stream_formatter()
    return None


def _format_claude_stream_output(channel: str, payload: str) -> list[str]:
    """Render Claude stream-json lines into readable terminal output."""
    if channel != "stdout":
        return [payload.rstrip()]

    line = payload.rstrip()
    if not line:
        return []

    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return [line]

    event_type = event.get("type")
    if event_type not in {"assistant", "user"}:
        return []

    message = event.get("message") or {}
    rendered: list[str] = []
    for item in message.get("content", []):
        item_type = item.get("type")
        if item_type == "thinking":
            rendered.extend(_prefix_multiline("thinking", item.get("thinking", "")))
        elif item_type == "text":
            rendered.extend(_split_nonempty_lines(item.get("text", "")))
        elif item_type == "tool_use":
            tag = _render_tool_use_tag(item)
            if tag:
                rendered.append(tag)
    return rendered


_CODEX_COMMAND_RE = re.compile(
    r"^/bin/(?P<shell>bash|sh)\s+-lc\s+(?P<quote>['\"])(?P<command>.*)(?P=quote)\s+in\s+(?P<cwd>.+)$"
)
_CODEX_SUCCESS_RE = re.compile(r"^\s*succeeded in \d+ms:?$")
_CODEX_HEADER_PREFIXES = (
    "OpenAI Codex v",
    "workdir:",
    "model:",
    "provider:",
    "approval:",
    "sandbox:",
    "reasoning effort:",
    "reasoning summaries:",
    "session id:",
    "warning: Codex could not find system bubblewrap",
)
_CODEX_DIFF_LINE_RE = re.compile(r"^(diff --git |index |--- |\+\+\+ |@@ )")


def _format_codex_stream_output(channel: str, payload: str) -> list[str]:
    """Render Codex CLI stderr into readable terminal output."""
    line = payload.rstrip()
    if not line:
        return []

    if channel == "stdout":
        return [line]

    stripped = line.strip()
    if stripped in {"codex", "exec"}:
        return []

    match = _CODEX_COMMAND_RE.match(stripped)
    if match:
        shell = match.group("shell").capitalize()
        command = " ".join(match.group("command").split())
        if len(command) > 100:
            command = command[:97] + "..."
        return [f"[ {shell} {command} ]"]

    if _CODEX_SUCCESS_RE.match(stripped):
        return []

    return [line]


def _make_codex_stream_formatter() -> Callable[[str, str], list[str]]:
    """Build a stateful Codex formatter that suppresses noisy content blocks."""
    state = {
        "suppress_prompt": False,
        "suppress_output_block": False,
    }

    def formatter(channel: str, payload: str) -> list[str]:
        raw_line = payload.rstrip("\n")
        line = raw_line.rstrip()
        if not line:
            if state["suppress_prompt"]:
                state["suppress_prompt"] = False
            return []

        stripped = line.strip()

        if stripped == "--------" or stripped.startswith(_CODEX_HEADER_PREFIXES):
            return []

        if stripped in {"codex", "exec"}:
            state["suppress_prompt"] = False
            state["suppress_output_block"] = False
            return []

        match = _CODEX_COMMAND_RE.match(stripped)
        if match:
            state["suppress_prompt"] = False
            state["suppress_output_block"] = _should_suppress_codex_command_output(
                match.group("command")
            )
            return [_render_codex_command_tag(match.group("shell"), match.group("command"))]

        if state["suppress_prompt"]:
            return []

        if stripped == "user":
            state["suppress_prompt"] = True
            return []

        if _CODEX_SUCCESS_RE.match(stripped):
            return []

        if state["suppress_output_block"]:
            if _should_keep_codex_block_line(stripped):
                return [line]
            if _looks_like_codex_narrative_line(stripped):
                state["suppress_output_block"] = False
                return [line]
            return []

        if _CODEX_DIFF_LINE_RE.match(stripped):
            return []

        return [line]

    return formatter


def _should_suppress_codex_command_output(command: str) -> bool:
    """Return true when a Codex shell command's raw output should stay hidden."""
    _ = command
    return True


def _render_codex_command_tag(shell: str, command: str) -> str:
    """Render a compact command tag for Codex stderr."""
    compact = _normalize_codex_command(command)
    label = shell.capitalize()
    if compact.startswith("nl -ba ") and " | sed -n " in compact:
        target = compact.split()[2]
        return f"[ Read {target} ]"
    if compact.startswith("sed -n "):
        target = _extract_last_token(compact)
        return f"[ Read {target} ]"
    if compact.startswith("cat "):
        target = _extract_last_token(compact)
        return f"[ Read {target} ]"
    if compact.startswith("head "):
        target = _extract_last_token(compact)
        return f"[ Read {target} ]"
    if compact.startswith("tail "):
        target = _extract_last_token(compact)
        return f"[ Read {target} ]"
    if compact.startswith("rg "):
        return f"[ Search {_truncate_codex_command(compact)} ]"
    if compact.startswith("git diff") or compact.startswith("git show"):
        return "[ Diff ]"
    if "compileall" in compact:
        return "[ Verify compileall ]"
    return f"[ {label} {_truncate_codex_command(compact)} ]"


def _truncate_codex_command(command: str, limit: int = 100) -> str:
    """Truncate a compact command string for display."""
    if len(command) > limit:
        return command[: limit - 3] + "..."
    return command


def _normalize_codex_command(command: str) -> str:
    """Normalize a logged Codex shell command for matching and display."""
    compact = " ".join(command.split())
    compact = re.sub(r"^cd\s+\S+\s+&&\s+", "", compact)
    return compact


def _extract_last_token(command: str) -> str:
    """Return the last whitespace-delimited token from a compact command string."""
    parts = command.split()
    return parts[-1] if parts else command


def _should_keep_codex_block_line(line: str) -> bool:
    """Return true for explicit command errors that should escape block suppression."""
    prefixes = (
        "fatal:",
        "error:",
        "warning:",
        "Traceback",
        "Exception",
        "panic:",
        "sed:",
        "cat:",
        "head:",
        "tail:",
        "diff:",
    )
    if line.startswith(prefixes):
        return True
    markers = (
        "No such file or directory",
        "Permission denied",
        "command not found",
        "syntax error",
    )
    return any(marker in line for marker in markers)


def _looks_like_codex_narrative_line(line: str) -> bool:
    """Return true for model narration that should end output-block suppression."""
    if len(line) > 240 or "\t" in line:
        return False
    if re.match(r"^\d+\s{2,}\S", line):
        return False
    if re.search(r"\S+/\S+:\d+(?::\d+)?:", line):
        return False
    if "@[native code]" in line or re.search(r"@[A-Za-z]+://", line):
        return False
    if re.match(r"^[+\-\d\s]*(?:export|interface|extends|implements|type)\b", line):
        return False
    if line.startswith(
        (
            "/",
            "+",
            "-",
            "{",
            "}",
            "[",
            "]",
            "(",
            ")",
            "package ",
            "import ",
            "from ",
            "class ",
            "def ",
            "func ",
            "type ",
            "var ",
            "const ",
            "return ",
            "if ",
            "for ",
            "while ",
            "switch ",
            "case ",
            "default ",
            "go: ",
            "diff --git ",
            "index ",
            "--- ",
            "+++ ",
            "@@ ",
            "Listing '",
            "Compiling '",
        )
    ):
        return False
    if line.endswith(":") and "/" in line:
        return False
    code_markers = (
        " := ",
        " == ",
        " != ",
        " && ",
        " || ",
        "func(",
        "logger.",
        "klog.",
        "ctx.",
        "err :=",
        "return ",
        "package ",
        "import ",
        "from ",
        "class ",
        "def ",
        "go func",
        "=>",
        "->",
        "</",
        "/>",
        "`;",
    )
    if any(marker in line for marker in code_markers):
        return False
    return len(re.findall(r"[A-Za-z]+", line)) >= 3


def _render_provider_transcript_output(
    provider: str | None,
    stdout: str,
    stderr: str,
) -> str:
    """Render a concise assistant transcript from provider-native stream output."""
    if provider == "claude":
        lines: list[str] = []
        for raw_line in stdout.splitlines():
            lines.extend(_format_claude_stream_output("stdout", raw_line + "\n"))
        return "\n".join(lines).strip()

    if provider == "codex":
        formatter = _make_codex_stream_formatter()
        lines: list[str] = []
        for raw_line in stderr.splitlines():
            lines.extend(formatter("stderr", raw_line + "\n"))
        for raw_line in stdout.splitlines():
            lines.extend(formatter("stdout", raw_line + "\n"))
        return "\n".join(lines).strip()

    return stdout.strip()


def _render_tool_use_tag(item: dict) -> str:
    """Render a compact tool tag from a Claude tool-use item."""
    name = str(item.get("name") or "Tool")
    tool_input = item.get("input") or {}
    target = (
        tool_input.get("file_path")
        or tool_input.get("path")
        or tool_input.get("target_file")
        or tool_input.get("command")
        or tool_input.get("pattern")
    )
    if isinstance(target, str) and target:
        compact = " ".join(target.strip().split())
        if len(compact) > 80:
            compact = compact[:77] + "..."
        return f"[ {name} {compact} ]"
    return f"[ {name} ]"


def _prefix_multiline(prefix: str, text: str) -> list[str]:
    """Prefix non-empty lines in a block of text."""
    return [f"[{prefix}] {line}" for line in _split_nonempty_lines(text)]


def _split_nonempty_lines(text: str) -> list[str]:
    """Split text into non-empty display lines."""
    return [line for line in text.splitlines() if line.strip()]


def _extract_provider_tool_usage(
    provider: str | None, stdout: str, stderr: str
) -> list[dict]:
    """Parse provider-native tool activity into normalized tool-usage records."""
    if provider == "claude":
        return _extract_claude_tool_usage(stdout)
    if provider == "codex":
        return _extract_codex_tool_usage(stderr)
    return []


def _extract_claude_tool_usage(stdout: str) -> list[dict]:
    """Extract tool calls from Claude stream-json stdout."""
    usage: list[dict] = []
    for raw_line in stdout.splitlines():
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "assistant":
            continue
        message = event.get("message") or {}
        for item in message.get("content", []):
            if item.get("type") != "tool_use":
                continue
            usage.append(
                {
                    "tool": f"agent.{item.get('name', 'Tool')}",
                    "args": item.get("input", {}),
                    "result": {},
                    "started_at": None,
                    "ended_at": None,
                }
            )
    return usage


def _extract_codex_tool_usage(stderr: str) -> list[dict]:
    """Extract shell command calls from Codex stderr logs."""
    usage: list[dict] = []
    for raw_line in stderr.splitlines():
        match = _CODEX_COMMAND_RE.match(raw_line.strip())
        if not match:
            continue
        usage.append(
            {
                "tool": "agent.Bash",
                "args": {
                    "command": _normalize_codex_command(match.group("command")),
                    "cwd": match.group("cwd"),
                },
                "result": {},
                "started_at": None,
                "ended_at": None,
            }
        )
    return usage


def _collect_workspace_edits(
    workspace: Path,
    snapshot: dict[str, str],
    prompt_filename: str | None = "TASK.md",
) -> list[dict]:
    """Return per-file edit records by comparing current workspace to snapshot."""
    current = snapshot_workspace(workspace)
    edits: list[dict] = []

    original_paths = set(snapshot)
    current_paths = set(current)

    for path in sorted(original_paths - current_paths):
        if not _should_ignore_edit_path(path, prompt_filename):
            edits.append({"path": path, "change": "deleted"})
    for path in sorted(current_paths - original_paths):
        if not _should_ignore_edit_path(path, prompt_filename):
            edits.append({"path": path, "change": "added"})
    for path in sorted(original_paths & current_paths):
        if not _should_ignore_edit_path(path, prompt_filename) and snapshot[path] != current[path]:
            edits.append({"path": path, "change": "modified"})
    return sorted(edits, key=lambda entry: (entry["path"], entry["change"]))


def _collect_workspace_final_state(
    workspace: Path,
    baseline: dict[str, dict[str, object]],
    prompt_filename: str | None = "TASK.md",
) -> FinalState:
    """Return changed-surface final-state details for the workspace."""
    current = _capture_workspace_file_state_map(
        workspace,
        prompt_filename=prompt_filename,
    )
    changed_files: list[FinalStateChangedFile] = []

    original_paths = set(baseline)
    current_paths = set(current)

    for path in sorted(original_paths - current_paths):
        changed_files.append(
            _build_final_state_changed_file(
                path=path,
                change="deleted",
                before_state=baseline[path],
                after_state=None,
            )
        )
    for path in sorted(current_paths - original_paths):
        changed_files.append(
            _build_final_state_changed_file(
                path=path,
                change="added",
                before_state=None,
                after_state=current[path],
            )
        )
    for path in sorted(original_paths & current_paths):
        if baseline[path]["hash"] != current[path]["hash"]:
            changed_files.append(
                _build_final_state_changed_file(
                    path=path,
                    change="modified",
                    before_state=baseline[path],
                    after_state=current[path],
                )
            )

    return FinalState(
        changed_files=sorted(changed_files, key=lambda item: (item.path, item.change))
    )


def _build_final_state_changed_file(
    *,
    path: str,
    change: str,
    before_state: dict[str, object] | None,
    after_state: dict[str, object] | None,
) -> FinalStateChangedFile:
    """Create a serializable final-state record for one changed file."""
    is_text = bool(
        (before_state and before_state.get("is_text"))
        or (after_state and after_state.get("is_text"))
    )
    content_truncated = bool(
        (before_state and before_state.get("content_truncated"))
        or (after_state and after_state.get("content_truncated"))
    )
    return FinalStateChangedFile(
        path=path,
        change=change,
        before_hash=before_state.get("hash") if before_state is not None else None,
        after_hash=after_state.get("hash") if after_state is not None else None,
        before_bytes=before_state.get("bytes") if before_state is not None else None,
        after_bytes=after_state.get("bytes") if after_state is not None else None,
        is_text=is_text,
        before_text=before_state.get("text") if before_state is not None else None,
        after_text=after_state.get("text") if after_state is not None else None,
        content_truncated=content_truncated,
    )


def _capture_workspace_file_state_map(
    workspace: Path,
    *,
    prompt_filename: str | None = "TASK.md",
) -> dict[str, dict[str, object]]:
    """Capture lightweight per-file state for final-surface comparisons."""
    file_state: dict[str, dict[str, object]] = {}
    for path in sorted(workspace.rglob("*")):
        if not path.is_file():
            continue
        rel_path = str(path.relative_to(workspace))
        if _should_ignore_edit_path(rel_path, prompt_filename):
            continue
        file_state[rel_path] = _capture_file_state(path)
    return file_state


def _capture_file_state(path: Path) -> dict[str, object]:
    """Capture hash, size, and small-text preview for one file."""
    sample_limit = _FINAL_STATE_INLINE_LIMIT_BYTES + 1
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        sample = handle.read(sample_limit)
        hasher.update(sample)
        while chunk := handle.read(1024 * 1024):
            hasher.update(chunk)

    byte_size = path.stat().st_size
    truncated = len(sample) > _FINAL_STATE_INLINE_LIMIT_BYTES
    inline_bytes = sample[:_FINAL_STATE_INLINE_LIMIT_BYTES]
    try:
        decoded = inline_bytes.decode("utf-8")
        is_text = True
    except UnicodeDecodeError:
        decoded = None
        is_text = False

    return {
        "hash": hasher.hexdigest(),
        "bytes": byte_size,
        "is_text": is_text,
        "text": decoded if is_text and not truncated else None,
        "content_truncated": bool(is_text and truncated),
    }


def _compute_run_stats(tool_usage: list[dict], edits: list[dict]) -> RunStats:
    """Compute artifact summary stats from normalized tool usage and edits."""
    agent_tool_calls = sum(1 for entry in tool_usage if str(entry.get("tool", "")).startswith("agent."))
    added_files = sum(1 for entry in edits if entry.get("change") == "added")
    modified_files = sum(1 for entry in edits if entry.get("change") == "modified")
    deleted_files = sum(1 for entry in edits if entry.get("change") == "deleted")
    return RunStats(
        total_tool_calls=agent_tool_calls,
        agent_tool_calls=agent_tool_calls,
        changed_files=len(edits),
        added_files=added_files,
        modified_files=modified_files,
        deleted_files=deleted_files,
    )


def _compute_task_revision(task: Task, snapshot: dict[str, str]) -> TaskRevision:
    """Fingerprint the exact prompt/verifier/workspace variant used for a run."""
    prompt_hash = _hash_jsonable(task.data.get("prompt", ""))
    verifier_hash = _hash_jsonable(task.data.get("verifier", {}))
    task_spec_hash = _hash_jsonable(task.data)
    workspace_hash = _hash_jsonable(snapshot)
    combined_hash = _hash_jsonable(
        {
            "task_spec": task_spec_hash,
            "prompt": prompt_hash,
            "verifier": verifier_hash,
            "workspace": workspace_hash,
        }
    )
    return TaskRevision(
        combined=combined_hash,
        task_spec=task_spec_hash,
        prompt=prompt_hash,
        verifier=verifier_hash,
        workspace=workspace_hash,
    )


def _hash_jsonable(value: object) -> str:
    """Return a stable SHA256 for JSON-serializable content."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def run_task(
    task: Task,
    workspace_override: Path | None = None,
    patch: Path | None = None,
    dry_run: bool = False,
) -> RunArtifact:
    """Execute a task evaluation.

    In dry-run mode, no agent executes. The workspace is evaluated as-is
    (pristine unless workspace_override is given).
    """
    with tempfile.TemporaryDirectory(prefix="weaselbench-") as tmp:
        work_dir = Path(tmp) / "workspace"
        materialize_workspace(task, work_dir)
        snapshot = snapshot_workspace(work_dir)

        if workspace_override and workspace_override.is_dir():
            _apply_overlay(workspace_override, work_dir)

        if patch and patch.is_file():
            _apply_patch(patch, work_dir)
        started_at = datetime.now(timezone.utc)
        ended_at = datetime.now(timezone.utc)
        return evaluate_workspace(
            task,
            work_dir,
            snapshot,
            started_at=started_at,
            ended_at=ended_at,
        )


def run_solution(task: Task, solution_name: str) -> RunArtifact:
    """Convenience: run a named solution."""
    solution_dir = task.task_dir / "solutions" / solution_name
    overlay_dir = solution_dir / "overlay"
    patch_file = solution_dir / "solution.patch"

    overlay = overlay_dir if overlay_dir.is_dir() else None
    patch = patch_file if patch_file.is_file() else None

    return run_task(
        task,
        workspace_override=overlay,
        patch=patch,
        dry_run=True,
    )


def _extract_archive(archive: Path, dest: Path) -> None:
    """Extract a .tar.gz workspace archive to dest, with path-safety checks."""
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tf:
        for member in tf.getmembers():
            # Reject absolute paths, path traversal, and symlinks
            if member.name.startswith("/") or ".." in member.name.split("/"):
                raise ValueError(
                    f"Refusing archive member with unsafe path: {member.name}"
                )
            if member.issym() or member.islnk():
                raise ValueError(
                    "Refusing archive member with link: "
                    f"{member.name} -> {member.linkname}. "
                    "Regenerate the repo archive with links dereferenced."
                )
        tf.extractall(dest, filter="data")
    # If archive wraps all files in a single top-level directory,
    # promote its contents up to dest (handles `tar czf repo.tar.gz repo/` case)
    entries = list(dest.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        single = entries[0]
        for item in list(single.iterdir()):
            shutil.move(str(item), str(dest / item.name))
        single.rmdir()

def _apply_patch(patch_file: Path, workspace: Path) -> None:
    """Apply a unified diff patch to the workspace using patch(1)."""
    result = subprocess.run(
        ["patch", "-p1", "--no-backup-if-mismatch", "-i", str(patch_file.resolve())],
        cwd=workspace,
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Patch application failed (exit {result.returncode}):\n"
            f"{result.stderr.decode()}"
        )


def _apply_overlay(overlay: Path, workspace: Path) -> None:
    """Copy overlay files into workspace, overwriting existing files."""
    for src in overlay.rglob("*"):
        if src.is_file():
            rel = src.relative_to(overlay)
            dst = workspace / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def _run_visible_checks(
    commands: list[str],
    workspace: Path,
    *,
    setup_commands: list[str] | None = None,
) -> list[VisibleCheckRecord]:
    """Execute visible check commands and capture results."""
    results: list[VisibleCheckRecord] = []
    for cmd in setup_commands or []:
        rendered = _expand_verifier_command(cmd, workspace)
        try:
            proc = subprocess.run(
                rendered,
                shell=True,
                cwd=workspace,
                capture_output=True,
                timeout=300,
            )
            results.append(VisibleCheckRecord(
                command=f"[setup] {rendered}",
                exit_code=proc.returncode,
                passed=proc.returncode == 0,
            ))
            if proc.returncode != 0:
                return results
        except subprocess.TimeoutExpired:
            results.append(VisibleCheckRecord(
                command=f"[setup] {rendered}", exit_code=-1, passed=False
            ))
            return results
        except Exception:
            results.append(VisibleCheckRecord(
                command=f"[setup] {rendered}", exit_code=-1, passed=False
            ))
            return results

    for cmd in commands:
        rendered = _expand_verifier_command(cmd, workspace)
        try:
            proc = subprocess.run(
                rendered,
                shell=True,
                cwd=workspace,
                capture_output=True,
                timeout=120,
            )
            results.append(VisibleCheckRecord(
                command=rendered,
                exit_code=proc.returncode,
                passed=proc.returncode == 0,
            ))
        except subprocess.TimeoutExpired:
            results.append(VisibleCheckRecord(
                command=rendered, exit_code=-1, passed=False
            ))
        except Exception:
            results.append(VisibleCheckRecord(
                command=rendered, exit_code=-1, passed=False
            ))
    return results


def _expand_verifier_command(command: str, workspace: Path) -> str:
    """Expand supported placeholders in verifier setup/visible commands."""
    return command.format(
        python=shlex.quote(sys.executable),
        uv=shlex.quote(shutil.which("uv") or "uv"),
        workspace=shlex.quote(str(workspace)),
    )
