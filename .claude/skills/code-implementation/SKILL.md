---
name: code-implementation
description: Implement code from activity plans and code-review files. Use when implementing one or more activity files, applying code-review fixes, or handling an ad-hoc coding task; plans that touch the same source files are implemented together in one worker.
argument-hint: "[files-or-task]"
allowed-tools: Agent(code-implementation-agent)
---

# code-implementation

## Guidelines

1. **Orchestrate, never implement inline** — All implementation happens in spawned `code-implementation-agent` workers; this skill never writes code itself
2. **One worker per group of coupled plans, in parallel** — Group the activity/code-review files, then spawn a separate `code-implementation-agent` for each group:
   - 2.1. **Plans that edit the same source file always share a worker** — the hard rule; two workers in one file race, and the survivor is whichever wrote last. A review file and its test file's review, or a module's review and its suite's, usually pair for exactly this reason
   - 2.2. **Plans whose fixes should agree share a worker** — siblings implementing the same contract get one worker so the same defect is not fixed two different ways
   - 2.3. **Keep groups small** — 2–4 files; unrelated plans stay separate and run concurrently
3. **Implementation Plan files** — Activity files and code-review files each carry an Implementation Plan for a worker to execute; an ad-hoc task without a file goes to a single worker

## Reference

### Reference Files

| File | Purpose |
|------|---------|
| [cr_20240115v01_generate_provider_summary.md](references/cr_20240115v01_generate_provider_summary.md) | Completed code-review file after implementation, findings resolved (used by `code-implementation-agent`) |

## Workflow

1. **Resolve and group the work** — Turn `$ARGUMENTS` into a concrete list of files to implement (activity and/or code-review files), or a single ad-hoc task; map which source files each plan touches and partition per the Guidelines
2. **Fan out** — Spawn one `code-implementation-agent` per group, passing **only** the file paths — no contents or extra instructions, since the worker already carries its own. For an ad-hoc task, spawn one worker with the task description.
3. **Summarize in the CLI** — report each file or task handled and its outcome; see the CLI summary example below

## Example

### CLI summary

```
Implemented 3 files in 2 workers:
- docs/activities/claims_analysis/20240115v01_summarize_claims_by_provider.md — 6/6 tasks completed
- docs/code_review/code/claims_analysis/cr_20240115v01_generate_provider_summary.md — 5/5 tasks completed (8 findings implemented, 2 deferred)
- docs/code_review/code/claims_analysis/unit_tests/cr_20240115v01_test_generate_provider_summary.md — 2/2 tasks completed  [grouped: same source files]
```
