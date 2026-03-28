# Task Sourcing Policy

## Purpose

This document defines the current sourcing policy for the active WeaselBench v0
task set.

WeaselBench measures whether an agent finishes the real engineering job inside a
real repository. The default task substrate is therefore a pinned snapshot of a
real codebase, not a fabricated benchmark repo.

Historical scouting notes live under [`docs/archive/`](archive/README.md).

## Default Task Substrate

New v0 tasks should start from a real repository state pinned to a specific
commit or release tag.

Prefer this order:

1. a pinned extracted repo archive declared in `environment.assets`
2. a checked-in workspace snapshot when the extracted subset is small enough
3. a benchmark-owned setup step that materializes missing dependencies or
   archives reproducibly

The benchmark should own the archive, image, and setup workflow. Task authors
should not rely on undocumented local steps.

## What Is In Scope

A task is a fit for the active v0 set when it:

- operates inside an existing codebase with established conventions
- has a shallow path that looks plausible locally
- exposes at least one structural completeness signal beyond visible tests
- requires work across multiple repo surfaces
- can be packaged as a reproducible benchmark-owned workspace

The current v0 public set is intentionally real-repo-first:

- Kubernetes contextual logging
- Scrapy structured logging
- GraphQL Code Generator config cleanup
- Next.js Link cleanup
- Rocket.Chat date handling migration
- Rocket.Chat SCSS-to-Emotion migration
- Storybook addon-essentials consolidation cleanup
- LangChain-pattern pydantic migration as one documented hybrid exception

## What Is Out of Scope

The active v0 set should not add tasks built from:

- purpose-built miniature repos
- fake dependency graphs created just for the benchmark
- hand-authored stubs that imitate a real package layout without the real hard
  tail
- toy workspaces where the benchmark difficulty comes only from volume

If a real snapshot is too large or too entangled to ship directly, the burden is
to justify a hybrid extraction explicitly. A hybrid task must preserve real repo
patterns, real migration surfaces, and the same engineering intent as the
upstream codebase. It is an exception, not a peer to the default approach.

## Hybrid Exception Policy

`migrate-pydantic-v2` is the current v0 exception. It is retained because it
captures a real LangChain migration pattern that would otherwise require pulling
in a much larger dependency graph. Any future hybrid task must document:

- the source repository and commit
- why a direct snapshot is impractical
- what real surfaces were preserved
- what benchmark-owned wrapper code was introduced
- why the wrapper does not collapse the hard tail into a toy problem

## Admission Checklist

Before a task enters the active set, confirm that:

- the workspace comes from a pinned real repo state or a justified hybrid
- the archive and setup path are benchmark-owned and reproducible
- the prompt states whether legacy paths must be removed
- the verifier catches the expected shallow completion path
- the task does not depend on fake local packages or invented upstream behavior
- the README explains source repo, commit, archive/setup path, and why the task
  belongs in WeaselBench

## Holdout and Refresh

Public v0 tasks are burnable. Future private sets should avoid reusing the same
repo shapes, module names, or hard-tail surfaces. Refresh should come from new
real-repo extractions, not from returning to fabricated task workspaces.
