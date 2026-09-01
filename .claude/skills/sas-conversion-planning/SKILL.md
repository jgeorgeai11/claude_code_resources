---
name: sas-conversion-planning
description: Produce and review target-oriented conversion activity plans from a process's SAS documentation and its input_schema_resolution.jsonl. Fourth step of the SAS conversion workflow. Use when planning or reviewing the conversion of a SAS process to a target language (dbt, Python, etc.).
argument-hint: "[activity-filepath]"
---

# sas-conversion-planning

## Guidelines

1. **Extends activity-development** — All [activity-development](../activity-development/SKILL.md) guidelines apply unless overridden below
2. **Follow the conversion planning template** — Use [sas_conversion_planning_template.md](references/sas_conversion_planning_template.md) in place of the activity template; this substitutes the template only, and activity-development's structural rules continue to apply
3. **File location and naming** — Save each conversion activity file per the Output Conventions below
4. **Frontmatter** — In addition to activity-development's `name`, `goal`, `created`, `updated`, populate `source_dir` and `target_language`; add no other fields
5. **Implementation Plan section** — The following extend or override the activity-development Implementation Plan guidelines:
   - 5.1. **One or more conversion activity files per process** — produce one, or split into several when the conversion structures more cleanly that way; the pre-transfer checks and the interface validation close the last file
   - 5.2. **Optimize for the target language** — combine, split, or restructure SAS steps for clean, idiomatic code; do not mirror SAS execution order unless it is optimal
     - 5.2.1. **Shared-macro patterns convert by intent** — convert the logic a shared utility macro applies, never the machinery that applies it (loops, partition fan-outs, macro variables); [common_sas_macros.md](references/common_sas_macros.md) states each macro's pattern and its conversion — a fan-out plus rollup, for example, usually collapses into one set-based operation
   - 5.3. **Maximize portability** — the converted code should run against any supported backend with no change beyond connection configuration; apply the target language's portability guidelines (Ibis for Python; dbt for dbt)
   - 5.4. **Specify conversion detail on top of the code shape activity-development asks for** — substeps carry what is specific to this conversion: renames, business logic, thresholds, join conditions, omitted steps. File names, function names, parameters, and expected behavior are still specified as for any coding activity; internal structure stays out — CTE names, statement ordering, syntax
   - 5.5. **A conversion is always the unreachable case** — the converted code reaches neither origin-system data nor destination data from here; it is transferred to the destination and run there by hand. Activity-development's reachability rule applies with its branch already decided, and two things are specific to conversions:
     - 5.5.1. **Nothing measures the origin** — no comparison against an origin copy and no origin-side profiling; differences from the origin are documented, not measured
     - 5.5.2. **Local checks catch errors early; they do not verify** — `dbt`: `dbt parse`, then unit tests against the local backend. `python`: `ibis.to_sql` against the destination dialect, then unit tests against the local backend (which backend comes from the target language's skill). These prove the code parses, resolves, and behaves on fixtures; only the post-transfer run establishes how the destination treats it
6. **The resolution supplies what the converted code reads** — every rule below is checkable against a drafted plan:
   - 6.1. **The conversion logic is planned from two sources in `source_dir`** — the SAS documentation says what the process does; `input_schema_resolution.jsonl` says what the converted code reads to do it:
     - 6.1.1. **The `.sas` source is not an input** — when the documentation is ambiguous or incomplete, fix it with `sas-documentation`; never plan from the code
     - 6.1.2. **[reading_the_resolution.md](references/reading_the_resolution.md) defines the resolution** — records, vocabulary, and the three layouts; the rules below assume it and do not restate it
   - 6.2. **Every name the plan emits traces to a record** — none is inferred from a SAS name
   - 6.3. **Table references are physical addresses** — no catalog `table_id` reaches emitted code
   - 6.4. **Each dataset follows its own transition** — mapped expressions where it transitions, original column names where it does not
   - 6.5. **Joins come from the `dest_join` records** — every table the plan reads is reachable through them. A table that is not is a catalog gap the resolution cannot detect: raise it to the user as an error naming the unrelatable tables — the missing `table_relationships` row must be documented and the resolution re-run — and never invent a join condition
   - 6.6. **Grain is stated and preserved** — the reproduction sits at the grain the SAS input carried (the `origin_join` assembly where one is documented, the parents' own `primary_key_columns` otherwise — a transition does not guarantee an `origin_join`), fan-out is addressed, and aggregates group at the dest table's grain
   - 6.7. **Types, nullability, and code values come from the records** — casts, missing-value handling, and literals match `dest_column` and `ref_table`
   - 6.8. **A copy switch is recorded, not tested** — an id-matched `origin_table`/`dest_table` pair guarantees matching columns, not matching rows; record the difference under Behavioral differences and plan nothing that reads the origin copy
   - 6.9. **Each ambiguity the resolution left open is decided** — one origin column per variable, one candidate per transitioning column, one join per table pair, with the rationale under Resolution decisions
   - 6.10. **Every `no_equivalent` substitute is named and its effect recorded** — the substitute the catalog documents, with rationale under Resolution decisions and its effect on key shape, grain, or match exactness under Behavioral differences
   - 6.11. **What the concepts say is reflected in the plan** — in the tasks, and in Behavioral differences
   - 6.12. **Nothing is guessed** — what the resolution does not answer is settled by querying the catalog through the `mcp__metadata_db__*` tools, or with the user
7. **Interface outputs are documented by a companion skill** — documenting them is part of this conversion, not a later stage; `sas-interface-documentation` carries it out because it needs the built code as well as this plan's mapping decisions
   - 7.1. **The plan's final task validates the YAML against the built code** — the converted side's documented columns, types, and nullability agree with what the code writes, and a missing YAML file fails the task rather than passing quietly
   - 7.2. **That task's paths name the project team, not a placeholder** — `sas-interface-documentation` reads `{team}` from them; confirm it with the user when the conversion does not make it obvious
8. **Key Data Decisions and Considerations section** — Document only what is essential for correct implementation or verifying conversion faithfulness:
   - 8.1. **Omitted SAS steps** — every SAS step with no equivalent in the plan, with its source doc step number; a step converted or replaced by a target-language equivalent is NOT omitted and must not appear here
   - 8.2. **Behavioral differences** — differences between SAS and the target that affect correctness: NULL handling (each side's `is_nullable` says whether it admits nulls; SAS missing values do not map onto them cleanly), sort order, missing value semantics, type coercion, row scope from a copy switch
   - 8.3. **Resolution decisions** — every origin column, candidate, join, and no-equivalent substitute chosen, with its rationale. The plan states the outcome; this states why
   - 8.4. **Destination run** — the command that runs the converted code after transfer, what a passing result looks like, and the full-data rerun. Documented, not planned, because no one here can execute it

## Reference

### Output Conventions

| Convention | Value |
|------------|-------|
| **Output directory** | `docs/activities/sas_conversion/{process}/` — `{process}` is the process being converted (from `source_dir`) |
| **Filename** | `{yyyymmddv##}_{activity_name}.md` — follows activity-development's convention (`{activity_name}` is snake_case, verb-first) |
| **`{yyyymmddv##}`** | `created` date plus a zero-padded version from `v01`; bump the version only when preserving a new revision alongside an existing file |

### Reference Files

| File | Purpose |
|------|---------|
| [reading_the_resolution.md](references/reading_the_resolution.md) | What `input_schema_resolution.jsonl` is, every record type and what planning reads it for, the vocabulary, and the three layouts by conversion shape |
| [common_sas_macros.md](references/common_sas_macros.md) | Shared Warehouse utility macros, one entry per macro: what a documented call means and how the pattern converts; how to tell a shared macro from a component macro, and what to do with one no entry covers |
| [sas_conversion_planning_template.md](references/sas_conversion_planning_template.md) | Conversion activity file template with all required sections |
| [20260323v01_convert_claims_analysis_to_dbt.md](references/20260323v01_convert_claims_analysis_to_dbt.md) | Example: dbt target, **no** data transition — models keep the SAS names |
| [20260727v01_convert_ocs_claims_to_python.md](references/20260727v01_convert_ocs_claims_to_python.md) | Example: Python target, **with** a data transition — every target name, expression, join, and decision comes from the resolution |

## Workflow

1. **Read relevant files**
   - 1.1. **The reference files** — read every file listed in Reference → Reference Files
   - 1.2. **The skill this extends** — [activity-development](../activity-development/SKILL.md)
2. **If creating new conversion activity files**
   - 2.1. **Gather inputs** — Ask for `source_dir` (holding the SAS documentation and `input_schema_resolution.jsonl`), `target_language` (`python` or `dbt`), and — when `source_dir` holds an `output_schema.jsonl` — the project team (guideline 7.2); explain each in plain language if unfamiliar
   - 2.2. **Read the target-language skills** — `python`: python-development core skills and the Ibis optional skill; `dbt`: sql-development core skills and the dbt optional skill
   - 2.3. **Read the sources** — the SAS documentation, the whole `input_schema_resolution.jsonl`, and, when present, `output_schema.jsonl` — the variable list `sas-interface-documentation` will author from
   - 2.4. **Read the target project's existing code** — the conventions already settled there, and the built code of any process this conversion reads from: the resolution carries a producer's columns and addresses, not the name its converted model or module goes by
   - 2.5. **Resolve every ambiguity** — Decide each origin column, candidate, and join, and note the rationale for Resolution decisions
   - 2.6. **Clarify** — Ask the user about ambiguous requirements, missing inputs, the `no_equivalent` substitutes and their effects, or open design decisions
   - 2.7. **Decide how many conversion activity files** — one or several
   - 2.8. **Draft** — Following the template, produce the implementation tasks and Key Data Decisions; the code-implementation-agent works only from the plan, so give it enough substep detail
3. **If reviewing existing conversion activity files**
   - 3.1. **Read the plan under review** — the conversion activity file(s) at `$ARGUMENTS`
   - 3.2. **Read the sources** — the SAS documentation, `input_schema_resolution.jsonl`, and, when present, `output_schema.jsonl` in the frontmatter's `source_dir`
   - 3.3. **Read the target-language skills** — per the create branch, chosen by the frontmatter's `target_language`
   - 3.4. **Read the target project's existing code** — per the create branch (2.4); a plan referencing another process's converted output is checked against what that process actually built
   - 3.5. **Review** — Take every guideline in turn, here and in activity-development, and flag each violation
   - 3.6. **Clarify** — Ask the user about ambiguous issues or trade-offs before applying fixes
   - 3.7. **Apply fixes** — Restructure and rewrite to resolve all issues
4. **Validate** — Check the conversion activity file(s) against every guideline in turn; fix any violations
5. **Write** — Save the conversion activity file(s) per the Output Conventions
6. **If `source_dir` holds an `output_schema.jsonl`** — the conversion is not finished with the plan: once the code exists, run `sas-interface-documentation`, which brings the catalog YAML in line with this plan's mapping decisions and the built code — authoring it, or reviewing what is already there
