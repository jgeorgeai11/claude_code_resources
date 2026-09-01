---
name: 20260323v01_convert_claims_analysis_to_dbt
goal: Convert the claims_analysis SAS process to dbt models. No data transition — the resolution carries no dest_data_scope, so the models read the origin tables under their own names in the warehouse system.
source_dir: docs/activities/sas_conversion/claims_analysis
target_language: dbt
created: 2026-03-23 10:00:00
updated: 2026-06-16 10:00:00
---

## Implementation Plan

### Phase 1 — Project setup

1. [pending] Configure project defaults - `dbt_project.yml` *(if not already configured)*
   - 1.1. Set per-layer materialization defaults: staging → view, intermediate → table or ephemeral, marts → table, audits → view

2. [pending] Configure connection profile - `~/.dbt/profiles.yml` *(if not already configured)*
   - 2.1. Two targets: the local DuckDB target — `dbt parse` loads it to register the adapter but never connects; the unit tests execute against it — and the Snowflake destination (lives in `~/.dbt/`, not the repo)

3. [pending] Configure packages - `packages.yml` *(if not already configured)*
   - 3.1. Add `dbt_utils`, `dbt_date`, and `dbt_expectations` packages

4. [pending] Create claims_analysis params macro - `macros/claims_analysis/macro_claims_analysis__params.sql`
   - 4.1. Read `year`, `month`, `readmit_window_days`, and `high_pay_threshold` via `macro_common__require_var('claims_analysis_year')`, `..._month`, `..._readmit_window_days`, `..._high_pay_threshold` (no defaults; raises if missing)
   - 4.2. Read `sample_limit` via `var('claims_analysis_sample_limit', none)` — a row cap for the verify run; `none` = full data
   - 4.3. Return dict `{'year': year, 'month': month, 'readmit_window_days': readmit_window_days, 'high_pay_threshold': high_pay_threshold, 'sample_limit': sample_limit}`

### Phase 2 — Staging models

5. [pending] Create sources YAML - `models/claims_analysis/staging/_stg_claims_analysis__sources.yml`
   - 5.1. Define source `claims_analysis` with tables: `medical_claims`, `pharmacy_claims`, `dental_claims`, `member_enrollment`, `provider_roster`
   - 5.2. Set the source's `database` and `schema` from the resolution's `dest_table` physical names — the documented table names above are not necessarily the physical ones
   - 5.3. Column descriptions and built-in tests (`not_null` on key columns; `unique` where applicable)

6. [pending] Create staging model: medical claims - `models/claims_analysis/staging/stg_claims_analysis__medical.sql`
   - 6.1. Import CTE from `source('claims_analysis', 'medical_claims')`; filter to configured service year via params macro
   - 6.2. Select `member_id`, `claim_id`, `service_date`, `provider_id`, `dx_code`, `proc_code`; rename `amount` → `paid_amount`; add `claim_source = 'MEDICAL'`

7. [pending] Create staging model: pharmacy claims - `models/claims_analysis/staging/stg_claims_analysis__pharmacy.sql`
   - 7.1. Import CTE from `source('claims_analysis', 'pharmacy_claims')`; filter to configured service year via params macro
   - 7.2. Select `member_id`, `claim_id`, `provider_id`, `ndc_code`; rename `fill_date` → `service_date`, `amount` → `paid_amount`; add `claim_source = 'PHARMACY'`

8. [pending] Create staging model: dental claims - `models/claims_analysis/staging/stg_claims_analysis__dental.sql`
   - 8.1. Import CTE from `source('claims_analysis', 'dental_claims')`; filter to configured service year via params macro
   - 8.2. Select `member_id`, `claim_id`, `service_date`, `provider_id`, `tooth_code`; rename `amount` → `paid_amount`; add `claim_source = 'DENTAL'`

9. [pending] Create staging model: member enrollment - `models/claims_analysis/staging/stg_claims_analysis__member_enrollment.sql`
   - 9.1. Pass through all columns from `member_enrollment` source with clean names

10. [pending] Create staging model: provider roster - `models/claims_analysis/staging/stg_claims_analysis__provider_roster.sql`
    - 10.1. Pass through all columns from `provider_roster` source with clean names

11. [pending] Create staging models YAML - `models/claims_analysis/staging/_stg_claims_analysis__models.yml`
    - 11.1. Document each staging model; description ends with `Level = <col>`
    - 11.2. Built-in tests: `unique` + `not_null` on `claim_id` in each claim staging model; `not_null` on `member_id`, `service_date`, `provider_id`, `paid_amount`

### Phase 3 — Intermediate models

12. [pending] Create intermediate model: union claim types - `models/claims_analysis/intermediate/int_claims_analysis__claims_combined.sql`
    - 12.1. Import CTEs from `ref('stg_claims_analysis__medical')`, `ref('stg_claims_analysis__pharmacy')`, `ref('stg_claims_analysis__dental')`
    - 12.2. UNION ALL the three staging models on their common columns (`member_id`, `claim_id`, `service_date`, `provider_id`, `paid_amount`, `claim_source`); drop source-specific columns (`dx_code`, `proc_code`, `ndc_code`, `tooth_code`) not used downstream
    - 12.3. If `sample_limit` (from the params macro) is set, apply `limit {{ p.sample_limit }}` — a row cap for the verify run; unset = full data

13. [pending] Create generic test macro: unmatched claims - `tests/generic/test_common__claims_unmatched.sql`
    - 13.1. One `{% test %}` block; return claims with no matching enrollment record; test passes if result is empty

14. [pending] Create intermediate model: join claims to member enrollment - `models/claims_analysis/intermediate/int_claims_analysis__with_members.sql`
    - 14.1. Import CTEs from `ref('int_claims_analysis__claims_combined')` and `ref('stg_claims_analysis__member_enrollment')`
    - 14.2. JOIN on `member_id`; filter to members with `enrollment_start` on or before the first day of `{{ p.year }}/{{ p.month }}` (from params macro)
    - 14.3. Add per-member window aggregates: `claim_count = COUNT(*)`, `total_paid = SUM(paid_amount)`, `avg_paid = AVG(paid_amount)`

15. [pending] Create intermediate models YAML - `models/claims_analysis/intermediate/_int_claims_analysis__models.yml`
    - 15.1. Document each intermediate model; description ends with `Level = <col>`
    - 15.2. `unique` + `not_null` on `claim_id` in `int_claims_analysis__claims_combined`; `not_null` on `member_id`, `service_date`, `provider_id`, `paid_amount`
    - 15.3. `accepted_values` on `claim_source`: `['MEDICAL', 'PHARMACY', 'DENTAL']` in `int_claims_analysis__claims_combined`
    - 15.4. Apply `test_common__claims_unmatched` on `int_claims_analysis__claims_combined` with `enrollment_model = stg_claims_analysis__member_enrollment`

### Phase 4 — Marts and tests

16. [pending] Create marts model: enriched claim detail - `models/claims_analysis/marts/mart_claims_analysis__claim_detail.sql`
    - 16.1. Import CTEs from `ref('int_claims_analysis__with_members')` and `ref('stg_claims_analysis__provider_roster')`
    - 16.2. LEFT JOIN to provider roster ON `provider_id`; bring in `provider_name`, `specialty` (as `provider_specialty`), `npi`; claims with no matching provider retain NULL
    - 16.3. Compute `days_since_last` from previous `service_date` per member; set `readmit_flag = 1` when `days_since_last <= {{ p.readmit_window_days }}`; compute `paid_change` from previous `paid_amount`
    - 16.4. Add `validation_flag`: `'NEG_PAY'` if `paid_amount < 0`, `'ZERO_PAY'` if `= 0`, `'HIGH_PAY'` if `> {{ p.high_pay_threshold }}`, `'OK'` otherwise

17. [pending] Create marts model: episode summaries - `models/claims_analysis/marts/mart_claims_analysis__episodes.sql`
    - 17.1. Import CTE from `ref('int_claims_analysis__with_members')`
    - 17.2. Detect gaps greater than `{{ p.readmit_window_days }}` days between `service_date` per member; set `new_episode_flag = 1` at each gap
    - 17.3. Assign `episode_id` by cumulative sum of `new_episode_flag` per member ordered by `service_date`
    - 17.4. Aggregate per `member_id` + `episode_id`: `episode_start = MIN(service_date)`, `episode_end = MAX(service_date)`, `episode_claims = COUNT(*)`, `episode_total_paid = SUM(paid_amount)`

18. [pending] Create generic test macro: no overlapping date ranges - `tests/generic/test_common__no_overlapping_date_ranges.sql`
    - 18.1. One `{% test %}` block; return rows where a member has overlapping date ranges; test passes if result is empty

19. [pending] Create marts model: monthly spend per member - `models/claims_analysis/marts/mart_claims_analysis__monthly_spend.sql`
    - 19.1. Import CTE from `ref('int_claims_analysis__with_members')`
    - 19.2. Pivot `paid_amount` by month using conditional aggregation: `month_1` through `month_12`
    - 19.3. Compute `annual_total = SUM(paid_amount)`, `active_months = COUNT(DISTINCT month_idx)`, `pmpm = annual_total / NULLIF(active_months, 0)`; GROUP BY `member_id`

20. [pending] Create generic test macro: monthly spend reconciliation - `tests/generic/test_common__monthly_spend_reconcile.sql`
    - 20.1. One `{% test %}` block; verify `annual_total` equals sum of `month_1` through `month_12`; verify `pmpm` equals `annual_total / active_months` within floating-point tolerance; test passes if no rows fail

21. [pending] Create marts models YAML with data and unit tests - `models/claims_analysis/marts/_mart_claims_analysis__models.yml`
    - 21.1. Document each mart; description ends with `Level = <col>` (`__claim_detail` → `Level = claim_id`; `__episodes` → `Level = member_id - episode_id`; `__monthly_spend` → `Level = member_id`)
    - 21.2. `mart_claims_analysis__claim_detail`: `unique` + `not_null` on `claim_id`; `accepted_values` on `validation_flag` (`['NEG_PAY', 'ZERO_PAY', 'HIGH_PAY', 'OK']`) and `readmit_flag` (`[0, 1]`)
    - 21.3. `mart_claims_analysis__episodes`: `not_null` on `member_id`, `episode_id`, `episode_start`, `episode_end`, `episode_claims`, `episode_total_paid`; `dbt_utils.expression_is_true` for `episode_end >= episode_start` and `episode_claims > 0`; apply `test_common__no_overlapping_date_ranges` with `partition_by = member_id`, `start_date = episode_start`, `end_date = episode_end`
    - 21.4. `mart_claims_analysis__monthly_spend`: `unique` + `not_null` on `member_id`; `not_null` on `annual_total`, `active_months`, `pmpm`; `dbt_utils.expression_is_true` for `active_months >= 1`; apply `test_common__monthly_spend_reconcile`
    - 21.5. Unit test on `mart_claims_analysis__monthly_spend`: mocked rows for one `member_id` across two months; expected per-month and annual totals

### Phase 5 — Pre-transfer checks

22. [pending] Check locally before transfer
    - 22.1. `dbt parse` — Jinja, `ref()`/`source()` resolution, and the DAG; loads the profile but opens no warehouse connection
    - 22.2. `dbt build --select test_type:unit --target duckdb` — the unit tests against the local DuckDB target
    - 22.3. These prove the project parses and the logic holds on fixtures; the SQL is validated as DuckDB, not as Snowflake

23. [pending] Validate the claims_analysis interface documentation against the built models - `docs/activities/sas_conversion/claims_analysis/data_catalog/sources/demo_proj/int_claims_analysis_sas/` and `docs/activities/sas_conversion/claims_analysis/data_catalog/sources/demo_proj/int_claims_analysis_converted/`
    - 23.1. Every column the three marts materialize is documented under `demo_proj.int_claims_analysis_converted`, with the same name, type, and nullability
    - 23.2. Every documented column exists in the built models — no column documented that no model produces
    - 23.3. Every variable in `output_schema.jsonl` carries a mapping into `demo_proj.int_claims_analysis_converted`; with no data transition the models keep the SAS names, so these are direct mappings — a no-equivalent one only where a mart drops or reshapes a variable
    - 23.4. A missing or unreadable YAML file fails this task — report it rather than treating the documentation as absent by design

## Key Data Decisions and Considerations

1. Omitted chronological timeline step — the interleaved claim timeline (WORK.CLAIMS_TIMELINE) has no downstream consumer in the target (source: doc_20260321v01_p01_load_raw_data.md, step 1.3)
2. Omitted random sample step — exploratory/QA only; no equivalent output needed in target (source: doc_20260321v01_p01_load_raw_data.md, step 1.4)
3. Omitted keyed lookup step — duplicates the enrollment join already covered in task 14 (source: doc_20260321v01_p01_load_raw_data.md, step 1.9)
4. Omitted double-pass daily summary step — the per-day/member grouping (WORK.MEMBER_DAILY_SUMMARY) is not a target output (source: doc_20260321v01_p01_load_raw_data.md, step 1.10)
5. SAS missing (.) sorts before all numeric values; target platform NULLs sort last by default — use NULLS FIRST where sort order matters
6. No data transition: every dataset in the resolution resolves without dest_data_scope, so the models keep the SAS column names and no mapping candidates were consulted. The origin and destination systems are both warehouse, so no id-matched table pair (copy switch) appears
7. Destination run — after transfer, run `dbt build --vars '{claims_analysis_year: 2024, claims_analysis_month: 1, claims_analysis_readmit_window_days: 30, claims_analysis_high_pay_threshold: 100000, claims_analysis_sample_limit: 1000}' --select +mart_claims_analysis__monthly_spend +mart_claims_analysis__claim_detail +mart_claims_analysis__episodes`. Passing looks like all three marts materialized and every data test green. Then rerun against the full data (omit `claims_analysis_sample_limit`)
