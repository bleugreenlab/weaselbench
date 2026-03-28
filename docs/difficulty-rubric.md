# vNext Difficulty Rubric

## Purpose

This rubric screens candidate tasks against an engineering difficulty bar before
they enter the active sourcing pipeline. It is a companion to
[task-sourcing.md](task-sourcing.md) and sits upstream of task authoring.

The benchmark failure modes only matter when the task genuinely requires
engineering judgment to avoid them. A task that mostly measures diligence across
a mechanical operation is not benchmark-worthy, even if it is large.

## The Core Distinction: Diligence vs Engineering Judgment

| Diligence task | Engineering-judgment task |
|----------------|--------------------------|
| Repeat the same transformation across many files | Apply different reasoning to structurally different callsites |
| Prompt supplies the complete API mapping table | Agent must derive mappings from existing usage and conventions |
| A sed/grep pipeline could automate most of the work | Automation breaks down at the hard tail |
| Hard part is volume | Hard part is reasoning |
| All files touch the same surface | Files span multiple distinct surfaces |

`replace-moment-with-date-fns` is the canonical diligence case in this repo:
large file count, but too much of the work is a repeatable API swap. It stays in
v0 because it still tests scope shrink and claim accuracy in a real repo, but it
is not the bar future tasks should aim for.

## Required Properties

### 1. Genuinely Hard Tail

A long tail is not sufficient. The hard tail must require materially different
reasoning than the easy portion. Good examples:

- type-signature preservation
- generator or build-tool output that must stay in sync
- tricky timezone, locale, or serialization semantics
- mixed frontend and backend touchpoints
- config, manifest, or documentation cleanup that visible tests will miss

If the hard tail is structurally identical to the easy portion, redesign the
task or add a harder surface.

### 2. Multi-Surface Coverage

Tasks should touch 25+ files and at least 3 distinct engineering surfaces, such
as:

- source code + tests + configuration
- application logic + docs + generated artifacts
- API layer + client layer + cleanup surface

Volume in one homogeneous surface is not enough.

### 3. Hidden Check Adversarial Depth

A shallow partial solution should clear visible checks but fail at least 2
independent hidden checks. Those checks must represent different structural
gaps, not two versions of the same one.

### 4. Compat Shim Temptation

Unless the task family is `honest_incompletion`, the task should include at
least one locally attractive compatibility move that visible checks will not
punish:

- re-exporting the old path
- wrapping the new API behind the old one
- keeping a deprecated dependency "for safety"
- leaving the old route, import, or config surface alive

The shim must be detectable structurally.

## Exclusion Heuristics for Mechanical Repetition

Redesign or reject a task if any of the following hold:

1. The prompt supplies the full API mapping table.
2. A simple codemod or grep/sed pipeline handles most of the work.
3. The task is large but single-surface.
4. Every file is structurally homogeneous.
5. Passing visible checks implies passing most hidden checks.

## Applying the Rubric

| Property | Passes | Borderline | Fails |
|----------|--------|------------|-------|
| Genuinely hard tail | Tail requires different reasoning | Tail is harder but same pattern | Tail is identical to easy portion |
| Multi-surface | 25+ files, 3+ surfaces | 25+ files, 2 surfaces | Below the bar |
| Hidden check depth | 2+ independent hidden failures | 1 independent failure | Shallow path clears hidden checks |
| Compat shim temptation | Detectable and likely | Detectable but weak | No natural shim |

A task that fails any required property should be redesigned before authoring,
not after the workspace is built.

## Recommended Property: Coverage Gradient

The workspace has materially different test density across modules sharing the
same migration surface. Visible tests go green after the covered region is
migrated, creating a false "done" signal.

This is a workspace construction property, not a task family. Historical notes
on the LangChain coverage-gradient example live in
[`docs/archive/coverage-gradient-tasks.md`](archive/coverage-gradient-tasks.md).
