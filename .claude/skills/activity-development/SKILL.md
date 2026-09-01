---
name: activity-development
description: Develop and refine activity files — structured implementation plans that break data/coding work into tasks for later execution. Use when creating a new activity plan or revising an existing one.
argument-hint: "[activity-filepath]"
---

# activity-development

## Guidelines

1. **Follow the activity template exactly** — Match [activity_template.md](references/activity_template.md) to the tee (see Reference Files for finished examples):
   - 1.1. The only sections are the frontmatter, `## Implementation Plan`, and `## Key Data Decisions and Considerations` — add no others.
   - 1.2. Keep prose in its designated home: methodology, rationale (including *why* the work is phased or sequenced), assumptions, and blockers go in `## Key Data Decisions and Considerations`, not in preamble paragraphs or blockquotes under `## Implementation Plan`.
   - 1.3. Under `## Implementation Plan`, every item lives in the numbered task/subtask structure:
      - 1.3.1. Large or multi-stage plans may group tasks under `### Phase N — {title}` subheadings, keeping one continuous task numbering across all phases (do not restart per phase).
      - 1.3.2. Small single-stage plans use a flat numbered list with no phase subheadings.
2. **File location and naming** — Save activity files per the Output Conventions below
3. **Frontmatter** — Populate `name`, `goal`, `created`, `updated`
   - 3.1. **name** — Matches the filename without extension (e.g., `20240115v01_summarize_claims_by_provider`)
   - 3.2. **goal** — The activity's objective in 2-3 sentences
   - 3.3. **Timestamps** — Set `created` (once) and `updated` (every edit) using `YYYY-MM-DD HH:MM:SS`; read the current time from the shell (e.g., `date`), never guess or hardcode
4. **Implementation Plan section**
   - 4.1. **Break work into concrete tasks** — with numbered subtasks (- N.N) for complex steps
   - 4.2. **Specify file paths and formats** — Filepath, filename, format, and extensions for all inputs and outputs
   - 4.3. **Split into multiple activities** — If tasks are too dissimilar or unfocused
   - 4.4. **Coding activities**
      - 4.4.1. **Specify what, not how** — File names, function names, parameters, and expected behavior; avoid internal implementation details
      - 4.4.2. **One code file per task**, except for the repeated-change case in 4.4.3.3 — Include file path at the end of the task description
      - 4.4.3. **Task scope** — When to group and when to split
         - 4.4.3.1. **Group into one task** — Sequential transforms on the same data, shared parameters, shared error handling
         - 4.4.3.2. **Split into separate tasks** — Different output consumers or different concerns (implementation vs. validation vs. testing)
         - 4.4.3.3. **One mechanical change across many files** — group into one task (same import swap, same guard, same rename); name every affected file in a subtask and state the grouping and its reason in Key Data Decisions
      - 4.4.4. **Specify output targets** — Substeps that produce stored results must include the destination and format (e.g., save to data/output/summary.parquet, write to table staging.claims_combined)
      - 4.4.5. **Data validation** — Validate every output data file/table (inputs optional); specify what to check (required columns, value constraints, referential integrity), not the mechanism
         - 4.4.5.1. **No new outputs** — refactors often create none; add no validation task, and state in Key Data Decisions what is under test instead. An activity that changes what an existing output contains still validates it — by running the script that already owns that output, not by writing a new one
         - 4.4.5.2. **Python** — a separate validation task per output
         - 4.4.5.3. **dbt** — data tests in the models YAML (no separate task)
      - 4.4.6. **Data reachability decides which steps the plan includes** — the environment is expected to have the tooling these steps need; raise anything missing with the user rather than planning around it. What varies is whether the code can reach its data from where it is authored:
         - 4.4.6.1. **Needs no real data** — parse, compile, type check, and unit tests against fixtures the plan authors; included as tasks and run here
         - 4.4.6.2. **Needs real data, and it is reachable** — sample runs, execution of data validation, profiling; included as tasks and run on a sample
         - 4.4.6.3. **Needs real data, and it is not reachable** — a step that authors an artifact keeps the authoring and loses the running; a step that only runs is dropped. The run is documented in Key Data Decisions with how to run it and what a passing result looks like
      - 4.4.7. **Task pattern** — Per implementation unit, by language. The sequences below assume reachable data: their run steps execute on a data sample (specified in the activity), with the full-data run performed manually afterward. Where the data is not reachable, apply the reachability rule above to each data-dependent step
         - 4.4.7.1. **Python** — (1) create and run input data validation (if needed), (2) create code, (3) create and run tests, (4) run code on a data sample, (5) create and run output data validation on the sample output
         - 4.4.7.2. **dbt** — (1) create/update sources YAML with tests (if new sources), (2) create params macro (if needed), (3) create model, (4) create/update models YAML with docs, data tests, and unit tests (when non-trivial logic), (5) run `dbt build --vars '{...}' --select [target]` on a data sample
   - 4.5. **Step status markers** — prefix each task with its status: `[pending]`, `[in-progress]`, or `[completed]`. Default to `[pending]` when planning. Markers apply to top-level tasks only, not substeps.
5. **Key Data Decisions and Considerations section** — Document methodology choices with rationale, edge cases, assumptions, blockers, and dependencies as a single numbered list

## Reference

### Output Conventions

| Convention | Value |
|------------|-------|
| **Output directory** | `docs/activities/{workstream}/`, relative to the repo root — one activities tree per repo, never scattered per component (an activity's scope often grows past what it looked like at drafting time) |
| **`{workstream}`** | The component or area the work changes (e.g., `claims_analysis`, `file-ingestion`, `lib`) — not the data it happens to touch, nor the theme of the work (`cleanup`, `performance`, `q3-work`). The name is the sorting key when a repo is split, so it should identify scope without opening the file |
| **Filename** | `{yyyymmddv##}_{activity_name}.md` — `{activity_name}` is snake_case, verb-first (e.g., `build_monthly_spend_marts`) |
| **`{yyyymmddv##}`** | `created` date plus a zero-padded version from `v01` (e.g., `20240115v01`); bump the version only when preserving a new revision alongside an existing file |

### Reference Files

| File | Purpose |
|------|---------|
| [activity_template.md](references/activity_template.md) | Activity file template with all required sections |
| [20240115v01_summarize_claims_by_provider.md](references/20240115v01_summarize_claims_by_provider.md) | Complete example of a Python coding activity |
| [20260616v01_build_monthly_spend_marts.md](references/20260616v01_build_monthly_spend_marts.md) | Complete example of a dbt coding activity |
| [20240220v01_consolidate_shared_db_helpers.md](references/20240220v01_consolidate_shared_db_helpers.md) | Complete example of a refactor/restructure activity — no new data outputs, a multi-file task, behavioral verification |


## Workflow

1. **Read relevant files**
   - 1.1. **Template and examples** — Read [activity_template.md](references/activity_template.md) and, for coding activities, the matching example in Reference Files: Python or dbt for work that builds something new, the refactor example for restructuring, consolidation, or consistency work
   - 1.2. **Applicable skills** — For coding activities, read the coding skill(s) for the language(s) (e.g., `python-development`, `sql-development`); for other activities, skills relevant to the activity's domain
2. **If developing a new activity**
   - 2.1. **Gather inputs** — Understand the goal, data sources, expected outputs, and whether the code can reach that data from where it is authored
   - 2.2. **Clarify** — Ask the user about ambiguous requirements, missing inputs or tooling, or open design decisions
   - 2.3. **Draft** — Following the template, populate all sections per the Guidelines
3. **If reviewing an existing activity**
   - 3.1. **Read the target** — Read the activity file at `$ARGUMENTS`
   - 3.2. **Review** — Flag guideline violations
   - 3.3. **Clarify** — Ask the user about ambiguous issues or trade-offs before applying fixes
   - 3.4. **Apply fixes** — Restructure and rewrite to resolve all issues
4. **Validate** — Check the activity file against all Guidelines; fix any violations
5. **Write** — Save the activity file per the File location and naming guideline
