---
name: code-review
description: Review code files against project standards. Use when reviewing one or more code files, a module, or changed files; coupled files are reviewed together in one worker, and every file gets its own review file.
argument-hint: "[files-or-scope]"
allowed-tools: Agent(code-review-agent)
---

# code-review

## Guidelines

1. **Orchestrate, never review inline** — All file review happens in spawned `code-review-agent` workers; this skill never reviews files itself
2. **One worker per group of coupled files, in parallel** — Group the files, then spawn a separate `code-review-agent` for each group:
   - 2.1. **Group by coupling, not convenience** — a module and its test file, siblings implementing the same contract, a small package; files with nothing in common stay separate
   - 2.2. **Keep groups small** — 2–4 files; a large or complex file gets a worker to itself so its review is not diluted
   - 2.3. **A single file is a group of one** — the degenerate case, not an exception
3. **Code files only** — Include any source/code file; skip non-code files (e.g., docs, data)

## Reference

### Reference Files

| File | Purpose |
|------|---------|
| [cr_template.md](references/cr_template.md) | Structure each per-file review follows (used by `code-review-agent`) |
| [cr_20240115v01_generate_provider_summary.md](references/cr_20240115v01_generate_provider_summary.md) | Filled-in example of a completed per-file review |

## Workflow

1. **Resolve and group files** — Turn `$ARGUMENTS` into a concrete list of code files, then partition it into groups per the Guidelines
2. **Fan out** — Spawn one `code-review-agent` per group, passing **only** the file paths — no contents or extra instructions, since the worker already carries its own
3. **Summarize in the CLI** — report each file reviewed and finding counts by severity; see the CLI summary example below

## Example

### CLI summary

```
Reviewed 3 files in 2 workers — 1 critical, 3 major, 3 minor:
- code/claims_analysis/generate_provider_summary.py (1 critical, 2 major, 1 minor)
- code/claims_analysis/unit_tests/test_generate_provider_summary.py (1 major, 1 minor)  [grouped with the module]
- code/claims_analysis/data_validation/data_val_claims.py (1 minor)
```
