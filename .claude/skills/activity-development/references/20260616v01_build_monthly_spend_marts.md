---
name: 20260616v01_build_monthly_spend_marts
goal: Build a marts model exposing member monthly spend by aggregating medical claims. Demonstrates the dbt task pattern from staging through marts, including params, data tests, and unit tests.
created: 2026-06-16 10:00:00
updated: 2026-06-16 10:00:00
---

## Implementation Plan

1. [pending] Create sources YAML - `models/claims_analysis/staging/_stg_claims_analysis__sources.yml`
   - 1.1. Define source `claims_analysis` with table `medical_claims`
   - 1.2. Add column descriptions for claim_id, bene_id, service_date, paid_amount
   - 1.3. Tests: not_null + unique on claim_id; not_null on bene_id, service_date, paid_amount

2. [pending] Create params macro - `macros/claims_analysis/macro_claims_analysis__params.sql`
   - 2.1. Read `start_date` and `end_date` via `macro_common__require_var` (no defaults; raises if missing)
   - 2.2. Return dict `{'start_date': start_date, 'end_date': end_date}`

3. [pending] Create staging model - `models/claims_analysis/staging/stg_claims_analysis__medical.sql`
   - 3.1. Select from `source('claims_analysis', 'medical_claims')`; rename to snake_case; cast types
   - 3.2. Filter `service_date` between the params `start_date` and `end_date` (from `macro_claims_analysis__params`)

4. [pending] Create staging models YAML - `models/claims_analysis/staging/_stg_claims_analysis__models.yml`
   - 4.1. Document `stg_claims_analysis__medical` with description ending `Level = claim_id`
   - 4.2. Column descriptions and tests: not_null + unique on claim_id; not_null on bene_id, service_date, paid_amount

5. [pending] Create marts model - `models/claims_analysis/marts/mart_claims_analysis__monthly_spend.sql`
   - 5.1. Import CTE `claims` from `ref('stg_claims_analysis__medical')`
   - 5.2. Logical CTE `aggregated` — group by bene_id and the year-month of service_date; compute total_paid, claim_count
   - 5.3. End with `select * from aggregated`

6. [pending] Create marts models YAML with data and unit tests - `models/claims_analysis/marts/_mart_claims_analysis__models.yml`
   - 6.1. Document `mart_claims_analysis__monthly_spend` with description ending `Level = bene_id - month`
   - 6.2. Data tests: `dbt_utils.unique_combination_of_columns` on (bene_id, month); not_null on bene_id, month, total_paid, claim_count; `dbt_utils.expression_is_true` for `total_paid >= 0`
   - 6.3. Unit test: 3 mocked claims for one bene_id across two months → expected per-month total_paid and claim_count

7. [pending] Run dbt build on a sample window
   - 7.1. Run `dbt build --vars '{claims_analysis_start_date: 2024-01-01, claims_analysis_end_date: 2024-01-31}' --select +mart_claims_analysis__monthly_spend` (one month, to verify quickly)
   - 7.2. Verify model materialized and all tests pass; run the full window (`claims_analysis_end_date: 2024-12-31`) manually afterward

## Key Data Decisions and Considerations

1. Study window filter applied at staging — keeps downstream models simple and consistent across the area
2. Monthly grain at marts — aggregates daily claims to month for consumer use; daily detail retained in staging
3. Unique combination of (bene_id, month) enforced via test — formalizes the marts grain contract
4. Source `medical_claims` already exists in the raw schema
5. Verify on a one-month window first (via the study-window params); run the full window manually once tests pass
