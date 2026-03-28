# WeaselBench

WeaselBench is a benchmark for long-running coding agents that work inside real
repositories. It measures a specific failure mode: appearing to complete the
job while quietly stopping at a shallower local optimum.

The benchmark targets repo-aware agents that can use a terminal, edit files,
run tests, and sometimes interact with a limited tracker surface. It is not a
function-synthesis benchmark. It is designed to surface behaviors like:

- scope shrink during execution
- compatibility laundering that leaves deprecated paths alive
- facade completion that builds the shell and skips the hard tail
- pattern evasion that ignores local abstractions and conventions
- social overclaiming when the repo state does not justify "done"

The core design principle is simple: make the shortcut easy, but make it
legibly wrong.

Live and evaluation runs are scored on whether the agent completes the task
before it ends its first turn. Wall-clock time, model calls, and dollar cost
are reported as secondary telemetry, while generous liveness guards only exist
to stop hung runs.

## Public Sample Set

This repository contains 3 sample tasks from a larger private corpus. They
demonstrate the benchmark format, scoring approach, and temptation patterns:

| Task | Source Repo | Category |
|------|-------------|----------|
| `replace-moment-with-date-fns` | Rocket.Chat | Pattern conformance — library migration |
| `migrate-to-structured-logging` | scrapy/scrapy | Migration — logging framework |
| `migrate-verdaccio-express-to-fastify` | verdaccio/verdaccio | Framework migration — Express to Fastify |

Each task includes a task spec (`task.yaml`), workspace snapshot, verifier
checks, and (where available) reference `good` and `weasel` solution patches.

The full benchmark corpus is maintained separately and used for official
evaluations.

## Quick Start

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e ".[dev]"
source .venv/bin/activate

# List tasks
weaselbench tasks --root tasks

# Validate all task specs
weaselbench validate --root tasks

# Prepare a workspace snapshot (downloads/extracts archives)
weaselbench setup migrate-verdaccio-express-to-fastify --root tasks

# Run a task against its reference solutions
weaselbench run replace-moment-with-date-fns --solution good --root tasks
weaselbench run replace-moment-with-date-fns --solution weasel --root tasks

# Run tests
pytest -v

# Batch-run a task across a preset model list
weaselbench batch-run replace-moment-with-date-fns --config quick
```

## Repository Layout

- `benchmarks/`: benchmark-level configs and split metadata
- `configs/batch/`: batch run presets (model lists, runtime settings)
- `containers/`: Dockerfiles for neutral agent runtime environments
- `docs/`: benchmark documentation
- `schemas/`: JSON schemas for tasks, status comments, and run artifacts
- `src/weaselbench/`: Python harness (loader, verifier, scorer, runner, CLI)
- `tasks/`: v0 benchmark tasks
- `tests/`: test suite

## First Principles

- Score behavior, not inferred intent.
- Prefer behavioral and structural verification over gold patches.
- Keep tasks repo-shaped and tempting to under-complete.
- Keep tracker tooling optional and auxiliary to the main score.
- Reward doing more of the real repo work over performing completion socially.
- Do not let reporting rituals obscure whether the codebase was actually fixed.

