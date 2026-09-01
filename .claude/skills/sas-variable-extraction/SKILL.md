---
name: sas-variable-extraction
description: The variable-inventory contract of the SAS conversion workflow's second step. The extraction script and its TOML config are moved to the environment holding the SAS data and run there manually; this skill documents the config-authoring rules and the artifacts — input_schema.jsonl feeds sas-data-resolution, output_schema.jsonl feeds sas-interface-documentation.
user-invocable: false
---

# sas-variable-extraction

## Guidelines

1. **The inventory comes from the script, never by hand** — `scripts/extract_sas_dataset_schemas.py` reads the SAS datasets and writes the inventories; the authored artifact is its TOML config. [sas_variable_extraction_example.toml](references/sas_variable_extraction_example.toml) is the annotated reference for every key
2. **The script and its config run where the SAS data lives** — moved there and run manually, with dataset paths as that environment sees them; the only local run is the committed smoke test. The move bundle is the `scripts/` folder — the extractor, its `data_validation/` folder, the `logconfig/` beside them, and the config — moved whole, so nothing has to be assembled by hand
3. **One config per process** — A process is a group of related `.sas` files forming one pipeline (an orchestrator and its component macros):
   - 3.1. **`process_name` must match across artifacts** — the same name in this config, the SAS documentation frontmatter, and the `{process}` folder the conversion artifacts live under; it is what ties them together
   - 3.2. **`[datasets]` holds what the process reads** — never intermediates or WORK datasets this process creates in passing. A permanent dataset another process wrote is an input like any other; the process's own kept outputs go under `[outputs]`
   - 3.3. **A split dataset takes a `*` in its key and its path** — one dataset stored as sibling files of identical shape (`CLM_00`–`CLM_99`) is one entry, `"SRCLIB.CLM_*" = { path = ".../clm_*.sas7bdat" }`. Both inventories record the pattern rather than the member read, so the split travels with the dataset; a `*` on one side only, or anywhere but the filename, is rejected at parse
4. **Point `origin_data_scope` at the closest documented representation of what the SAS process actually read** — input datasets come in two kinds under that one governing rule:
   - 4.1. **Source-system derived subsets** (the usual case) — derived analysis subsets some other process built, combining several cataloged parent tables under analysis-specific names. They are not themselves cataloged, so `origin_data_scope` names the cataloged **parents**; variable names are preserved from them, which is why one dataset's variables can resolve across several parent tables
   - 4.2. **Interface datasets** — written by another conversion process and documented under its producer's `{team}.int_{process}_sas` schema, so `origin_data_scope` names the interface table itself and `dest_data_scope` names the converted schema (`{team}.int_{process}_converted`)
   - 4.3. **Never redirect a consumer's scope at the interface's grandparents** — the source-system tables the producer itself read — to dodge a `missing_variable` failure. That failure is the correct signal that the upstream process's interface documentation must land first, and the grandparents would silently skip every rename and derivation the producer's conversion made
5. **Systems are process-wide; data scopes may narrow per dataset** — a per-dataset system is rejected at config parse. `dest_data_scope` present means a data transition (resolution consults `column_mappings`); absent means the destination objects are the origin objects, though the system may still change
6. **`[outputs]` inventories the kept outputs** — which are kept is the author's judgment; WORK/internal intermediates never appear, and the section is omitted entirely when there are none. Deleting it from a config that once had one orphans an already-published `output_schema.jsonl`, which the interface-documentation step would still read as current, so `overwrite` decides whether the next run removes that file or refuses outright
7. **Every section's key set is closed** — an unknown key is rejected at parse, never accepted and silently dropped. A misspelled coordinate is why: typo `dest_data_scope` and the dataset publishes without it, which the resolution step reads as "no data transition", skipping `column_mappings` and resolving confidently wrong with no log line anywhere
8. **Trust the script's gates** — it validates its own output and publishes only what passes (a failed run leaves a `.draft`; never promote one by hand), and a non-zero exit names what is wrong: fix the config and rerun

## Reference

### Reference Files

| File | Purpose |
|------|---------|
| [extract_sas_dataset_schemas.py](scripts/extract_sas_dataset_schemas.py) | The extractor — moved to the SAS environment and run there |
| [sas_variable_extraction_example.toml](references/sas_variable_extraction_example.toml) | Annotated example config |
| [sas_variable_extraction_example_input_schema.jsonl](references/sas_variable_extraction_example_input_schema.jsonl) | Example `input_schema.jsonl` |
| [sas_variable_extraction_example_output_schema.jsonl](references/sas_variable_extraction_example_output_schema.jsonl) | Example `output_schema.jsonl` — hand-authored to match the example config's `[outputs]` entry (its dataset paths are illustrative, so it cannot be regenerated by a run) |
| [data_val_extract_sas_dataset_schemas.py](scripts/data_validation/data_val_extract_sas_dataset_schemas.py) | The validator the extractor runs before publishing; runs standalone against either inventory kind — the contract is inferred only from the exact basenames, any other filename needs `--kind` |
| [extract_config.toml](scripts/config/extract_config.toml) | Working smoke test, the only local run — covers both inventory kinds and a split dataset, over sample datasets in `scripts/config/` and `scripts/unit_tests/fixtures/` |
