---
name: sas-data-resolution
description: Resolve a SAS variable inventory against metadata_db — origin columns, mapping candidates, physical addresses, joins, and concepts. Third step of the SAS conversion workflow; consumes sas-variable-extraction's input_schema.jsonl and its output feeds sas-conversion-planning.
argument-hint: "[input_schema.jsonl path]"
---

# sas-data-resolution

## Guidelines

1. **Run the resolver, never resolve by hand** — `scripts/resolve_schema.py` holds the resolution rules and the queries, and is covered by its own tests; querying metadata_db directly and assembling records by hand reproduces neither
2. **Never hand-edit the output** — Rerun the resolver instead. Resolution is deterministic, so the same inventory must always reproduce the same file; an edit makes the output unreproducible and silently diverges it from the rules
3. **Read failures, do not work around them** — The resolver exits non-zero rather than writing a plausible, wrong file. Fix the cause and rerun; never patch past it
4. **The resolver validates its own output** — it publishes only what passes, and a rejected run leaves a `.draft` that is always the last run's. Never promote one by hand (see [readme.md](references/readme.md) → Failure modes)
5. **Resolution decides nothing planning must decide** — A variable matching several origin columns carries all of them, and a column with several mapped candidates carries all of them. Report these rather than choosing

## Reference

### Reference Files

| File | Purpose |
|------|---------|
| [resolve_schema.py](scripts/resolve_schema.py) | The resolver — run this; it holds the resolution rules and the metadata_db queries |
| [sas_data_resolution_example.toml](references/sas_data_resolution_example.toml) | Example config for a real process |
| [resolve_schema.toml](scripts/config/resolve_schema.toml) | The working config — points at the committed example inventory for an end-to-end smoke test |
| [mcp_client.py](scripts/mcp_client.py) | The JSON-RPC client the resolver queries `metadata_db` through |
| [data_val_schema_resolution.py](scripts/data_validation/data_val_schema_resolution.py) | The output validator the resolver runs before publishing; its field constants are the record-shape contract, and it also runs standalone against any resolution file |
| [data_val_catalog_gaps.py](scripts/data_validation/data_val_catalog_gaps.py) | The work-order validator, run as the gaps file is written and standalone via `--input-data` |
| [jsonl_checks.py](scripts/data_validation/jsonl_checks.py) | The field, segment, scope and reader checks both validators share, so neither can drift from the other |
| [sas_data_resolution_example.jsonl](references/sas_data_resolution_example.jsonl) | Example output — a real run of the committed sample inventory (`ocs.non_institutional` in warehouse → `edwc_prd` in edw) |
| [readme.md](references/readme.md) | How resolution works, end to end — the coordinates, every query and when it is skipped, the matching/filtering/status rules, the two gap gates, the write order and output checks, every failure mode with its remedy, and the three output layouts |

### Output

The file has one axis: `dest_*` records describe the world the emitted code reads, and an `origin_*` record appears exactly where the origin world differed; `origin_sas_*` records are always the origin's. A published resolution fully accounts for the SAS input — at least one variable, at least one origin column each, and in transition at least one candidate per column. Anything the catalog cannot account for exits non-zero instead of publishing.

Every record type, what it carries, and every exit cause with its remedy are in [readme.md](references/readme.md); `data_val_schema_resolution.py`'s field constants are the enforced contract.

### Runtime requirements

The resolver is an MCP client, not a database client: it needs the `metadata_db` MCP HTTP server reachable at the config's `mcp_url`, and a bearer token in the variable named by `mcp_token_env` (default `MCP_METADATA_DB_TOKEN`, as `.mcp.json` uses). No secret ever lives in the config.

## Workflow

1. **Write a config for this run** — copy [sas_data_resolution_example.toml](references/sas_data_resolution_example.toml), set `input_schema` to the `input_schema.jsonl` passed as `$ARGUMENTS`, and set `output_dir` (`docs/activities/sas_conversion` for a real process; the `{process}` folder comes from the inventory)
2. **Resolve** — `uv run .claude/skills/sas-data-resolution/scripts/resolve_schema.py --config <the config from step 1>`
3. **On a non-zero exit** — diagnose per [readme.md](references/readme.md) → Failure modes, fix the cause, and rerun
4. **Report** — the output path and, from the script's summary log, counts of resolutions by status, ambiguous variables, dest tables, dest columns, joins, and concepts
