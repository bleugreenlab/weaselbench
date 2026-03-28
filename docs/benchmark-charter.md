# Benchmark Charter

## Purpose

WeaselBench measures whether coding agents finish the real engineering job when
the easy local optimum is to do something shallower that still looks plausible.

The benchmark is about observable work avoidance, not about proving internal
intent. It evaluates behavior under realistic repo, terminal, and optional tracker
constraints.

## Primary Failure Modes

- `scope_shrink`: the agent quietly solves a smaller problem than requested
- `compatibility_laundering`: the agent keeps legacy code paths alive under the
  banner of safety or backwards compatibility
- `facade_completion`: the visible shell is implemented, but the deeper
  integration, cleanup, or edge-state work is skipped
- `pattern_evasion`: the agent reaches for generic muscle-memory solutions
  instead of using project-local abstractions
- `overclaiming`: the agent reports completion that the repository state does
  not justify

## Benchmark Unit

Each task packages the full evaluation unit:

- task statement
- starting repository state
- execution environment
- visible checks
- hidden checks
- scoring rubric
- first-turn execution policy and liveness guards
- captured execution artifacts

The verifier is part of the task, not an afterthought.

## Design Principles

### 1. Behavioral correctness over patch similarity

Do not score against a gold patch. Multiple correct implementations should be
accepted if they satisfy the same engineering intent and repository invariants.

### 2. Structural checks are mandatory

Visible tests are not enough. A task should combine runtime checks with
structural checks such as forbidden imports, required abstraction usage,
deprecated path removal, config cleanup, or tracker-policy conformance.

### 3. Shortcut paths should be locally attractive

If the shallow solution is obviously wrong, the task mostly measures
competence. WeaselBench tasks should instead make the shallow route tempting and
then detect why it is incomplete.

### 4. Honest incompletion should beat false closure

When a task cannot be fully completed in a single uninterrupted turn, accurate
reporting and proper escalation are still valuable evidence. But the public
score should remain grounded in the repository state, not in whether the agent
used a specific reporting ritual.

### 5. Completion is primary; budgets are secondary telemetry

WeaselBench scores whether the agent completed the engineering work before it
ended its first turn. Wall-clock time, model calls, and dollar cost should be
reported, but they are not primary scoring gates.

### 6. Tracker tooling is optional context, not a primary score axis

The same underlying task should often exist in two matched variants:

- direct request with no tracker tools
- issue-driven request with limited tracker tools

This allows the benchmark to test whether team-style framing changes behavior
without making tracker usage itself a public-pass requirement.

### 7. Real repositories are the default substrate

WeaselBench v0 is built around pinned real-repo snapshots and extracted repo
archives. Reproducibility should come from benchmark-owned assets, container
images, lockfiles, and setup steps, not from rebuilding tasks as toy repos with
fake dependencies.

### 8. Do not confuse offline fixtures with benchmark integrity

WeaselBench distinguishes between:

- model/provider connectivity needed for inference
- benchmark-controlled dependency setup needed to make a workspace runnable
- arbitrary agent web browsing during task execution

For most tasks, reproducibility should come from pinned images, caches,
lockfiles, and benchmark-owned setup steps, while the agent sandbox still
defaults to `agent_web_access: false`.

## v0 Task Set

The active public v0 set contains 10 tasks.

- 7 are extracted from real open-source repositories
- 1 (`migrate-pydantic-v2`) is a documented hybrid exception retained until a
  direct real-repo replacement exists

Exploratory scouting, candidate analysis, and earlier selection notes are kept
under `docs/archive/` and are not part of the active benchmark spec.
