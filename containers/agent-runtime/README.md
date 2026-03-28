# Agent Runtime Container

This image provides a neutral local execution substrate for provider-backed
`weaselbench live-run` runs.

Goals:

- expose only a normal repo workspace at `/workspace`
- keep benchmark control-plane files out of the visible repo tree
- put the tracker MCP binary on `PATH`
- provide both provider CLIs plus common engineering tools

Contents:

- `codex`
- `claude`
- `tracker-mcp-server`
- `git`, `rg`, `jq`, `patch`, `python3`, `bash`, `zsh`

Build:

```bash
docker build -f containers/agent-runtime/Dockerfile -t weaselbench-agent-runtime:local .
```

Run through the harness:

```bash
weaselbench live-run replace-moment-with-date-fns \
  --root tasks \
  --provider codex \
  --runtime docker \
  --runtime-image weaselbench-agent-runtime:local
```

By default, docker runtime mode mounts host provider auth/config homes when
present:

- `~/.codex`
- `~/.claude`
- `~/.claude.json`

Disable that with:

```bash
weaselbench live-run ... --runtime docker --no-mount-provider-auth
```

For providers that need a persistent in-container home, mount a named Docker
volume as `/home/agent` instead:

```bash
docker volume create claude-home

weaselbench live-run replace-moment-with-date-fns \
  --root tasks \
  --provider claude \
  --runtime docker \
  --runtime-image weaselbench-agent-runtime:local \
  --no-mount-provider-auth \
  --runtime-home-volume claude-home
```
