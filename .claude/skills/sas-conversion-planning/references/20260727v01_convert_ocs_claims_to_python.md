---
name: 20260727v01_convert_ocs_claims_to_python
goal: Convert the ocs_claims SAS process to Python (Ibis), rebuilding its claim and line inputs from the EDW Medicare view (edwc_prd.claims_vw_prd) instead of the OCS extracts they were built from. Runs in the edw system.
source_dir: docs/activities/sas_conversion/ocs_claims
target_language: python
created: 2026-07-27 10:30:00
updated: 2026-08-08 11:43:02
---

## Implementation Plan

1. [pending] Configure connection - `code/ocs_claims/connection.py`
   - 1.1. Backend connection factory for the local DuckDB backend and the Snowflake destination; selected via config, no code change between them

2. [pending] Create TOML config - `code/ocs_claims/config/ocs_claims.toml`
   - 2.1. Connection settings: backend, credentials loaded from .env
   - 2.2. Parameters: `sample_limit` (none = full data; a row cap for the verify run)

3. [pending] Build the claim base - `code/ocs_claims/claims.py`
   - 3.1. Read `edwc_prd.claims_vw_prd.v_clm`; filter `clm_fnl_act_ind = 'Y'` before anything else — without it a claim appears once per processing version
   - 3.2. Select and rename to the SAS names the downstream analysis expects:
     - 3.2.1. `clm_type` = `lpad(clm_type_cd::text, 2, '0')` — OCS carries the code as a 2-character string
     - 3.2.2. `pmt_amt` = `clm_pmt_amt`
     - 3.2.3. `clm_from_dt` = `clm_from_dt` (carried for the line join and the summary)
   - 3.3. Replace the OCS claim identity: carry the 4-part key (`geo_mbr_sk`, `clm_dt_grp_sk`, `clm_type_cd`, `clm_num_sk`) in place of `claimno`/`recno`, and `mbr_sk` in place of `person_key`
   - 3.4. If `sample_limit` is set, cap rows

4. [pending] Create and run unit tests for the claim base - `code/ocs_claims/unit_tests/test_claims.py`
   - 4.1. The final-action filter excludes non-final rows
   - 4.2. `clm_type` keeps its leading zero and is always 2 characters

5. [pending] Build the line base - `code/ocs_claims/lines.py`
   - 5.1. Read `edwc_prd.claims_vw_prd.v_clm_line` joined to the claim base on the 4-part key **plus `clm_from_dt`** — the dest join's required 5th condition; omitting it associates lines with the wrong claim
   - 5.2. Select `lineitem` = `clm_line_num`, `prf_prvdr` = `clm_rndrg_prvdr_pin_num`; carry the claim keys
   - 5.3. Grain: one row per line — the dest table's 5-part key (`clm_line_num` + the 4-part key)

6. [pending] Create and run unit tests for the line base - `code/ocs_claims/unit_tests/test_lines.py`
   - 6.1. The join emits one row per line and never fans a line across claims (the 5th condition holds)
   - 6.2. Line renames carry through

7. [pending] Build the beneficiary summary - `code/ocs_claims/summary.py`
   - 7.1. From the claim base, aggregate per `mbr_sk`: `clm_cnt` = count of claims, `first_from_dt` = min(`clm_from_dt`), `tot_pmt_amt` = sum(`pmt_amt`)
   - 7.2. Grain: one row per `mbr_sk` — the converted counterpart of the SAS per-`person_key` summary

8. [pending] Create and run unit tests for the summary - `code/ocs_claims/unit_tests/test_summary.py`
   - 8.1. One row per `mbr_sk`; counts and totals match hand-built fixture claims

9. [pending] Orchestrate - `code/ocs_claims/main.py`
   - 9.1. Read the TOML config via `--config`; run claims, lines, and summary; log row counts

10. [pending] Create data validation - `code/ocs_claims/data_validation/data_val_ocs_claims.py`
    - 10.1. No null `clm_from_dt`, `pmt_amt`, or `mbr_sk`; `clm_type` is 2 characters and its values exist in `metadata_db.reference.clm_type_cd`
    - 10.2. Claims: one row per 4-part key; lines: one row per 5-part key; summary: one row per `mbr_sk`
    - 10.3. Authored here, run in the destination after transfer — nothing in this task executes

11. [pending] Check locally before transfer - `code/ocs_claims/unit_tests/test_compiles.py`
    - 11.1. Assert `ibis.to_sql(expr, dialect="snowflake")` renders for every published expression — an operation Ibis cannot translate fails here rather than after transfer
    - 11.2. Run the unit tests against the local backend with the synthetic fixtures
    - 11.3. These prove the expressions translate and the logic holds on fixtures; they say nothing about Snowflake's semantics on real data

12. [pending] Validate the ocs_claims interface documentation against the built code - `docs/activities/sas_conversion/ocs_claims/data_catalog/sources/demo_proj/int_ocs_claims_sas/` and `docs/activities/sas_conversion/ocs_claims/data_catalog/sources/demo_proj/int_ocs_claims_converted/`
    - 12.1. Every column `summary.py` writes is documented under `demo_proj.int_ocs_claims_converted`, with the same name, type, and nullability
    - 12.2. Every documented column exists in what the code writes — no column documented that the code does not produce
    - 12.3. Every variable in `output_schema.jsonl` carries a mapping into `demo_proj.int_ocs_claims_converted`, a no-equivalent one where the converted output drops or reshapes it — including `person_key`, the documented drop in Key Data Decisions 2
    - 12.4. A missing or unreadable YAML file fails this task — report it rather than treating the documentation as absent by design

## Key Data Decisions and Considerations

1. EDW holds several rows per claim — one per processing version — so every read filters `clm_fnl_act_ind = 'Y'`. The SAS process dropped non-final rows via its `fa_drop` flag, which maps to this indicator (source: the `final_action_indicator` concept)
2. `person_key` — chose `ocs.non_institutional.clm_sgmt.person_key` over the `clm_line` column of the same name; SRCLIB.OCS_CLAIMS_* is claim-segment grain — its other variables (`clm_type`, `pmt_amt`, `fa_drop`) are claim-level, and the line-grain dataset is inventoried separately as SRCLIB.OCS_LINES_* with its scope narrowed to `clm_line` — so the `clm_line` copy would import line grain. Both are documented no-equivalents: EDW identifies the beneficiary by `mbr_sk`, which corresponds closely but not always to Person Key, so `mbr_sk` replaces it throughout and any join keyed on exact OCS↔EDW beneficiary identity is unsafe (source: the mapping notes and the `beneficiary_external_ids` / `claim_family_and_effective_key` concepts)
3. `clm_type` is a 2-character string in OCS and a NUMBER in EDW, so it is cast and left-padded back to 2 characters; its literals check against `ref.codes.clm_type_cd` (source: the `claim_type_code` concept)
4. The dest join is one-to-many on the 4-part key plus `clm_from_dt` as a required 5th condition — the join's own notes warn that omitting it mis-associates line services (source: the `four_part_claim_key` concept and the `dest_join` notes)
5. The resolution carries no `origin_join` — the catalog documents no relationship between `clm_sgmt` and `clm_line` — so the origin grain comes from the parents' own `primary_key_columns` (`clm_sgmt`: `claimno`/`person_key`/`recno`/`sgmt_num`, segment grain; `clm_line` adds `lineitem`, line grain) read together with the SAS documentation's own join step. The reproduction sits at claim grain (the dest 4-part key) and line grain (the 5-part key) per the dest tables' `primary_key_columns`
6. SAS missing (.) sorts before all values; Ibis NULLs sort last by default — apply `nulls_first=True` wherever sort order over a nullable column matters
7. Destination run — after transfer, run `uv run code/ocs_claims/main.py --config code/ocs_claims/config/ocs_claims.toml` with a small `sample_limit`. Passing looks like non-zero row counts on all three outputs and `data_val_ocs_claims.py` reporting no errors. Then rerun with `sample_limit` unset for the full data and rerun the validation
