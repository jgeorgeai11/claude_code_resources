---
name: sas-documentation
description: Document or review SAS files against project conventions. First step of the SAS conversion workflow — the documentation is what sas-conversion-planning plans from. Use when documenting one or more SAS files or reviewing existing SAS documentation; each file is handled in its own parallel pass and gets its own documentation file.
argument-hint: "[files-or-scope]"
allowed-tools: Agent(sas-documentation-agent)
---

# sas-documentation

## Guidelines

1. **Orchestrate, never document inline** — All documentation and review happens in spawned `sas-documentation-agent` workers; this skill only resolves the work and summarizes results in the CLI
2. **One worker per file, in parallel** — Spawn a separate `sas-documentation-agent` for each `.sas` file (to document) or documentation file (to review) so they run concurrently in isolated contexts
3. **SAS scope** — Document `.sas` files; review their documentation files; skip anything else
4. **First step of the conversion workflow** — The documentation feeds the pipeline downstream: `sas-conversion-planning` reads every doc file in the process folder, and the shared `process_name` frontmatter is what ties the conversion artifacts together

## Reference

### Reference Files

| File | Purpose |
|------|---------|
| [sas_documentation_template.md](references/sas_documentation_template.md) | Structure each documentation file follows (used by `sas-documentation-agent`) |
| [doc_20260321v01_p01_load_raw_data.md](references/doc_20260321v01_p01_load_raw_data.md) | Example: single-macro file (`p01_load_raw_data.sas`) |
| [doc_20260321v01_p00_run_claims_analysis.md](references/doc_20260321v01_p00_run_claims_analysis.md) | Example: non-macro code + macro definition (`p00_run_claims_analysis.sas`) |

## Workflow

1. **Resolve the work** — Turn `$ARGUMENTS` into a concrete list of `.sas` files to document, or documentation files to review
2. **Fan out** — Spawn one `sas-documentation-agent` per file (per the Guidelines), passing **only** the file's path — no contents or extra instructions, since the worker already carries its own
3. **Summarize in the CLI** — report each file handled and its outcome; see the CLI summary example below

## Example

### CLI summary

```
Documented 2 SAS files under docs/activities/sas_conversion/claims_analysis/:
- code/claims_analysis/p01_load_raw_data.sas → doc_20260321v01_p01_load_raw_data.md (1 macro, 1 step)
- code/claims_analysis/p00_run_claims_analysis.sas → doc_20260321v01_p00_run_claims_analysis.md (open code + 1 macro, 3 steps)
```
