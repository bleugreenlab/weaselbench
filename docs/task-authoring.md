# Task Authoring Guide

## What Makes a Good WeaselBench Task

A good task has three properties:

1. The shallow fix is easy and plausible.
2. The repository has a clear local notion of the right way to do the work.
3. The verifier can distinguish a structurally complete solution from a merely
   passing one.

## Start From a Real Repository

For the active v0 benchmark, author from a real repository snapshot first.

- Extract the smallest repo subset that still preserves the real hard tail.
- Keep the repo structure honest: source, tests, config, docs, manifests, and
  other cleanup surfaces that the task actually depends on.
- Avoid rebuilding the repo as a toy clone with fake packages or benchmark-only
  dependency stubs.

If a full extraction is impractical, document the hybrid design explicitly and
why the task still preserves real engineering constraints.

## Preferred Task Families

### Migration and Removal

Prompt the agent to migrate to a new abstraction and remove the old path. The
bad solution adds the new path while leaving the legacy implementation alive.

### Refactor for the Next Change

Design the task so a paired follow-up change would be painful if the agent used
local conditionals, compatibility shims, or hardcoded logic.

### Pattern Conformance

Require use of internal wrappers, UI primitives, service layers, migration
helpers, or theme tokens. Verify both the desired path and the absence of the
forbidden one.

### Facade Completion

Require the full feature surface:

- happy path
- empty, loading, and error states
- persistence or backend integration
- telemetry or analytics
- config or documentation touchpoints
- tests that cover more than the golden path

### Review Repair

Start from a plausible but insufficient patch and review comments that call out
deeper repository concerns. The task is to resolve the critique, not to minimize
the diff.

### Honest Incompletion

Design some tasks so the correct outcome within a single turn is partial
completion plus accurate status reporting. These tasks should still anchor
their public score in repository state, while treating tracker/status behavior
as auxiliary evidence or a separate reporting track.

## Anti-Patterns

- greenfield benchmark repos with weak project conventions
- tiny function-level problems where the smallest patch is genuinely correct
- prompts that are ambiguous about whether old paths should survive
- verifiers that enforce one exact implementation instead of desired behavior
- fake local dependencies added only to make a benchmark workspace feel larger
- authoring a fabricated repo when a pinned archive plus benchmark-owned setup
  would preserve the real task more honestly

## Environment Policy

Do not treat "no arbitrary web browsing by the agent" and "the workspace must be
fully offline" as the same requirement. The task schema uses two separate
fields:

- `agent_web_access` (default: `false`): whether the agent sandbox allows
  outbound network during task execution
- `setup_network_access` (default: `true`): whether benchmark-controlled setup
  commands may use the network

Prefer this order:

1. pinned runtime image
2. task-declared assets prepared by `weaselbench setup`
3. lockfile-backed setup commands
4. benchmark-controlled caches or allowlisted registries for dependency setup
5. only then, fully checked-in fixtures if the task actually benefits from them

For most WeaselBench tasks, the important integrity property is that the agent
cannot cheaply search for answers or fetch alternative implementations during
the run. It is usually not important that `pnpm install` or `uv pip install`
would fail in a cold air-gapped container.

## Declaring Large Assets

Large extracted repos should declare benchmark-owned assets in
`environment.assets`.

Supported asset sources:

- `script`: generate the asset from a task-local script such as `snapshot.sh`
- `download`: fetch a pinned artifact from a URL and verify its SHA-256 digest

Example:

```yaml
environment:
  repo_archive: next-v15.5.14.tar.gz
  assets:
    - path: next-v15.5.14.tar.gz
      source:
        type: script
        path: snapshot.sh
```

This is preferable to undocumented setup conventions or assuming contributors
will regenerate large archives manually.

## Authoring Checklist

- The prompt states whether legacy paths must be removed.
- If tracker tooling is present, the prompt states whether escalation is
  allowed or expected.
- The task README names the source repo and pinned commit or release.
- The README explains how the archive or workspace is materialized.
- Visible checks prove basic functionality.
- Hidden checks detect shallow completion and pattern evasion.
- The task includes at least one cleanup-oriented check.
- The task build/test setup is runnable under the benchmark's dependency policy.
- If a status/update tool is part of the task variant, its claims can be
  compared against repo state.
