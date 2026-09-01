---
name: sas-interface-documentation
description: Author and review the data_catalog YAML documenting a converted process's interface outputs — the kept outputs in its output_schema.jsonl. The interface half of sas-conversion-planning, split out because it needs the built code as well as the plan; once loaded into metadata_db, its YAML is what the next process's sas-data-resolution resolves against.
argument-hint: "[docs/activities/sas_conversion/{process} folder]"
---

# sas-interface-documentation

## Guidelines

1. **`output_schema.jsonl` sets the scope** — every kept output it carries is documented, and a process without the file has nothing to document. Which outputs are kept was decided and user-confirmed at extraction; that judgment is not revisited here
2. **Undocumented interface variables break the next conversion, not this one** — when the consuming process is converted, its resolution queries the catalog this YAML is loaded into, and anything missing surfaces there as a `missing_variable` gap. Nothing in the current process fails, which is why this step is easy to skip and must not be
3. **Three inputs govern the YAML, each authoritative for one thing** — never substitute one for another:
   - 3.1. **`output_schema.jsonl`** (in the plan's `source_dir`) — the SAS-side variable list, as the SAS process writes it. Every kept output's columns come from here, never from the SAS source read again
   - 3.2. **The conversion plan** — the renames, derivations, grain statements, and resolution decisions the mappings and `notes` are authored from
   - 3.3. **The implemented code** — what the converted side actually writes. Column names, types, and nullability on the converted side come from the code, not from the plan's intent
4. **Document both sides and the mappings between them** — the interface tables under `{team}.int_{process}_sas` as the SAS process writes them, the converted counterparts under `{team}.int_{process}_converted` as the code writes them, and `column_mappings` from every interface column into the converted side
5. **Every variable in `output_schema.jsonl` becomes a documented column with a mapping** — a variable the converted output drops or reshapes gets a no-equivalent mapping (`target_expression: null`) naming the substitute in its `notes`, never no mapping at all
6. **The conversion mints the interface schemas, never the data source** — each side's `schema.yaml` creates its `int_{process}_{side}` schema; an absent `{team}` data source is reported as a prerequisite (guideline 7). Author per side: `schema.yaml`, `tables.yaml`, columns, and — only when the kept outputs relate to one another — `table_relationships.yaml`, so a consumer joining them never hits an undocumented relationship
7. **What is never authored here** — `data_source.yaml`, `deployments.yaml`, and `systems.yaml` are maintained separately, as is the metadata_db load. Two consequences to report, not fix: the YAML protects the next conversion only once loaded, and the new schemas' tables fail a consumer's deployment gate until the steward adds them to each source's `deployments.yaml`
8. **Reconcile against the code before writing** — every converted-side column exists in what the code writes, and every column the code writes is documented. A disagreement is a finding for the user, not something to paper over by documenting the plan's intent
9. **Report what changed and what disagreed** — the files written or amended, the column and mapping counts, and any reconciliation finding from guideline 8

## Reference

### Output Conventions

`{process}` is the folder passed as the argument (confirmed by the inventory's `meta` record); `{team}` is the project team's data source, taken from the paths in the plan's interface-validation task (ask the user when the plan lacks one). The two schemas under it are `int_{process}_sas` and `int_{process}_converted`. Both column forms are valid; never both for one scope.

Every path below is rooted at `docs/activities/sas_conversion/{process}/data_catalog/sources/{team}/`.

| Artifact | Path |
|----------|------|
| **Schemas** (one per side) | `int_{process}_sas/schema.yaml`, and `int_{process}_converted/schema.yaml` |
| **Interface tables** | `int_{process}_sas/tables.yaml` |
| **Interface columns** | `int_{process}_sas/columns.yaml`, or a `columns/` folder of shard files |
| **Mappings** | `int_{process}_sas/mappings/int_{process}_converted.yaml` |
| **Converted tables** | `int_{process}_converted/tables.yaml` |
| **Converted columns** | `int_{process}_converted/columns.yaml`, or a `columns/` folder |
| **Relationships** (only when outputs relate) | `table_relationships.yaml` beside each side's `tables.yaml` |

### Reference Files

| File | Purpose |
|------|---------|
| [authoring_conventions.md](references/authoring_conventions.md) | The authoring contract — what each file is for, the id/mapping/no-equivalent conventions, what the YAML depends on downstream, and the per-field legend for every catalog table this skill writes |
| [metadata_db_example/](references/metadata_db_example/) | Worked example — the `demo_proj` team's `ocs_claims` outputs on both sides (`int_ocs_claims_sas` and `int_ocs_claims_converted`), both column forms, and the mappings including a no-equivalent |

## Workflow

1. **Read the reference files** — [authoring_conventions.md](references/authoring_conventions.md), then the worked example in `references/metadata_db_example/`
2. **Read the plans** — every conversion activity file in the process folder at `$ARGUMENTS` (the latest version of each), including Key Data Decisions
3. **Read `output_schema.jsonl`** — in the same folder; it is both the scope and the SAS-side variable list
4. **Stop if it is absent** — the process has no kept outputs; report that and write nothing
5. **Note the team and process** — `{process}` from the folder and the file's `meta` record; `{team}` from the plan's interface-validation task paths, or from the user
6. **Read the implemented code** — the models or scripts the plan produced, for the converted side's actual column names, types, and nullability
7. **If the `data_catalog/` paths (Reference → Output Conventions) hold no YAML yet** — author it:
   - 7.1. **Author both `schema.yaml`s** — the description of each side's process schema
   - 7.2. **Author the SAS side** — `tables.yaml` with each output's grain in `notes` (the grain comes from the plan), and the columns from `output_schema.jsonl`
   - 7.3. **Author the converted side** — `tables.yaml` and columns, from the code
   - 7.4. **Author the mappings** — one entry per interface column, direct expressions for renames and no-equivalent entries for dropped or reshaped variables (guideline 5)
   - 7.5. **Author `table_relationships.yaml`** — only when the kept outputs relate to one another (guideline 6)
8. **If they already hold YAML** — review it, amending what is wrong and authoring anything missing:
   - 8.1. **Read what is there** — both sides and the mappings
   - 8.2. **Review** — take every guideline in turn and flag each violation; a variable in `output_schema.jsonl` with no mapping, and a documented column the code does not produce, are the two that break the next conversion
   - 8.3. **Clarify** — ask the user about ambiguous findings or trade-offs before applying fixes
   - 8.4. **Apply fixes** — amend the YAML to resolve them
9. **Reconcile** — every `output_schema.jsonl` variable mapped, every mapping target a real converted column, every converted column reached by a mapping or named as a substitute in a no-equivalent's notes, and the converted side agreeing with the code (guideline 8)
10. **Write and report** — per guideline 9, including the two load-side consequences from guideline 7
