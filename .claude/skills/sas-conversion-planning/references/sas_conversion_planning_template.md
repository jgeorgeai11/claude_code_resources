---
name: "{yyyymmddv##}_{activity_name}"
goal: {Describe the conversion objective in 2-3 sentences}
source_dir: {docs/activities/sas_conversion/{process} — the SAS documentation, both inventories, and the resolution}
target_language: {python | dbt}
created: {timestamp}
updated: {timestamp}
---

## Implementation Plan

### Phase 1 — {phase title}

1. [pending] {Brief description} - `{target filepath}`
   - 1.1. {Operation description referencing target schema.table.column names}
   - 1.2. {Operation description}

2. [pending] {Brief description} - `{target filepath}`
   - 2.1. {Operation description}

### Phase 2 — {phase title}

3. [pending] {Brief description} - `{target filepath}`
   - 3.1. {Operation description}

### Phase 3 — Pre-transfer checks

4. [pending] Check locally before transfer
   - 4.1. {the local checks for this target_language}

5. [pending] Validate the {process} interface documentation against the built code - `docs/activities/sas_conversion/{process}/data_catalog/sources/{team}/int_{process}_sas/` and `docs/activities/sas_conversion/{process}/data_catalog/sources/{team}/int_{process}_converted/` *(omit if `source_dir` holds no `output_schema.jsonl`)*
   - 5.1. {what the plan reconciles between the YAML and the code}

## Key Data Decisions and Considerations

1. {Omitted SAS step} — {Why it has no equivalent} *(source: {SAS doc name}, step {N.N})*
2. {Behavioral difference between SAS and the target that affects correctness} — {What to do about it}
3. {The ambiguous variable, column, or table pair} — chose {the origin column, candidate, join, or substitute} over {the alternatives}; {why, citing the field that decided it: `use_when`, `description`, `notes`, `validated`}
4. Destination run — {command}; passing looks like {what to check}; then {full-data rerun}
