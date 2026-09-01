---
name: dbt
description: dbt project conventions: structure, naming, models, macros, tests, seeds, snapshots, and analyses. Use when working with a dbt project.
---

# dbt

## Guidelines

1. **Project structure** — Follow the folder structure in the Reference section
   - 1.1. *New project* — create any missing core files: `dbt_project.yml` and `packages.yml` in the project, `profiles.yml` in `~/.dbt/` (never the repo).
   - 1.2. **Layout** — *area-first*: one top-level folder per area of work (a distinct workstream), each owning its own `staging/` and `marts/` layers, plus `intermediate/` and `audits/` layers when needed; models `ref()` freely across areas. A single area is fine for simple projects.
   - 1.3. **Other folders** — `macros/`, `tests/`, `seeds/`, `snapshots/`, `analyses/`, and similar top-level folders follow the same area-first pattern: cross-area items under `common/` (or `tests/generic/` for tests, per dbt convention); area-specific items under `[area]/`.
2. **Portable SQL** — write SQL compatible with both DuckDB and Snowflake (see Portable SQL in Reference): use standard SQL where both dialects agree, `dbt_utils` and `dbt_date` where they differ, and a custom macro as last resort; flag anything that cannot be made portable
3. **Naming conventions** — files follow `[type]_[area]__[name].[ext]` with `common` as the cross-area marker. Exception: when dbt or installed packages require a specific name (e.g. `generate_schema_name`, adapter dispatch like `default__X`, built-in test overrides), use the required name verbatim — typically at `common/[required_name].ext`.
   - 3.1. **Models** — Staging: `stg_[area]__[entity]`, Intermediate: `int_[area]__[description]`, Marts: `mart_[area]__[concept]`, Audits: `audit_[area]__[description]`
   - 3.2. **Macros** — `macros/[area]/macro_[area]__[name].sql` (area-scoped) or `macros/common/macro_common__[name].sql` (cross-area)
   - 3.3. **Tests** — `tests/[area]/test_[area]__[name].sql` (singular) or `tests/generic/test_common__[name].sql` (generic)
   - 3.4. **Seeds** — `seeds/[area]/seed_[area]__[name].[ext]` (area-scoped) or `seeds/common/seed_common__[name].[ext]` (cross-area)
   - 3.5. **Snapshots** — `snapshots/[area]/snapshot_[area]__[entity].yml` (area-scoped) or `snapshots/common/snapshot_common__[entity].yml` (cross-area)
   - 3.6. **Analyses** — `analyses/[area]/analysis_[area]__[name].sql` (area-scoped) or `analyses/common/analysis_common__[name].sql` (cross-area)
   - 3.7. **YAML companion files** — leading `_` for sort order; layer prefix matches the models documented: `_stg_[area]__sources.yml`, `_stg_[area]__models.yml`, `_int_[area]__models.yml`, `_mart_[area]__models.yml`, `_audit_[area]__models.yml`
4. **Models**
   - 4.1. **Layers** — about purpose, not required progression: not every area needs an intermediate or audits.
     - 4.1.1. **Staging** — one model per source table (or closely related group, e.g. header + line). Handles renames, casts, simple typing, light cleaning (trim/lowercase), and parameterized filters (e.g. a reporting window). No joins to other models, no aggregations, no derived business logic.
     - 4.1.2. **Intermediate** — optional layer between staging and marts. Introduce an intermediate only when the transformation is (a) reused by multiple marts, (b) complex enough that isolating it improves clarity or testability, or (c) materially changes the grain. For a single-consumer thin join, keep the logic in the mart.
     - 4.1.3. **Marts** — business-facing models named for the business concept they expose (e.g. `provider_pairs_summary`). May join staging models, intermediates, and other marts; may do light aggregations and derivations. If a mart is becoming hard to read, extract the messy part to an intermediate.
     - 4.1.4. **Audits** — optional layer for diff models comparing two relations (e.g. current vs. other/prior snapshot). Use `dbt-labs/audit_helper` (`compare_relations`, `compare_queries`) for common diffs; write the SQL directly when the macros don't fit. Use `analyses/` for one-off diffs that don't need materialization.
   - 4.2. **CTE structure** — one import CTE per `ref()`/`source()` (`select *`, named for what it holds, e.g. `claims`); then logical CTEs named for what they do (e.g. `renamed`, `deduplicated`, `joined`)
   - 4.3. **Sources only in staging** — never hardcode raw table names; never use `{{ source() }}` in intermediate or marts. Audits may use `source()` for source-faithfulness checks.
   - 4.4. **Parameters** — never hardcode dates, thresholds, or filter values; define them in area-specific params macros that read runtime values via `var()` with no defaults (forces explicit values per run; prevents silent fallback when a var is missing, misspelled, or stale)
     - 4.4.1. One params macro per area at `macros/[area]/macro_[area]__params.sql` returning a dict
     - 4.4.2. Each parameter uses `var('[area]_[name]', none)` — area-prefixed names prevent one area's override hitting another's
     - 4.4.3. Raise a compiler error if the var is missing, guarded by `{% if execute %}` so parsing and other areas' builds aren't blocked. A shared helper macro (see [dbt-require-var-macro-example.sql](../references/dbt-require-var-macro-example.sql)) keeps area params macros to one line per parameter.
     - 4.4.4. Reference via `{% set p = macro_[area]__params() %}` then `{{ p.param_name }}` in models
     - 4.4.5. Pass at runtime: `dbt build --vars '{[area]_[name]: value, ...}' --select [target]`
     - 4.4.6. Connection config (schema, database, account) → `profiles.yml` targets
5. **Macros** — one `{% macro %}` per file; macro name matches the file name exactly (without `.sql`); e.g., `macro_claims_analysis__params.sql` defines `{% macro macro_claims_analysis__params() %}`
6. **YAML and testing**
   - 6.1. **YAML per subfolder** — all YAML files must include descriptions, column descriptions, and tests
     - 6.1.1. 2-space indent; leading underscore on model/source yml files
     - 6.1.2. Every model's YAML description ends with its level/grain in the form `Level = <col>` (or `Level = <col_a> - <col_b>` for composite grain), naming the columns whose combination is unique — a contract for downstream consumers
   - 6.2. **Tests** — every model needs tests on its grain plus any invariants it relies on. Pick in this order:
     - 6.2.1. built-in: `unique`, `not_null`, `accepted_values`, `relationships` (the only four; everything else is in a package)
     - 6.2.2. `dbt_utils` — covers value ranges, row-level invariants, composite uniqueness, cross-table parity, group-aware variants, and more. Skim the package README for the full list.
     - 6.2.3. `dbt_expectations` — broader library (~50 tests) modelled on Great Expectations: aggregate / distribution checks, schema assertions, group-aware checks, freshness, type enforcement. Skim the package README for the full catalog.
     - 6.2.4. Custom generic tests only when nothing above fits; prefer parameterized generics over singular tests
     - 6.2.5. Singular tests for complex multi-table or one-off checks
     - 6.2.6. One `{% test %}` per file; test name matches the file name exactly (without `.sql`); e.g., `test_common__valid_npi.sql` defines `{% test test_common__valid_npi(model, column_name) %}`
   - 6.3. **Unit tests** — add for non-trivial logic (CASE/derivations, boundaries, edge cases); skip simple passthroughs
     - 6.3.1. Define in a `unit_tests:` block as a sibling of `models:` in the same YAML
     - 6.3.2. Supply `given` (mocked input rows) and `expect` (asserted output) — only the columns relevant to the logic
7. **Configuration**
   - 7.1. **Materialization defaults** — set per-layer defaults in `dbt_project.yml`
     - 7.1.1. Staging: view | Intermediate: table or ephemeral | Marts: table | Audits: view. Only override at the model level when a model differs from its layer default

## Reference

### Project structure

```
dbt/
├── dbt_project.yml                             # profiles.yml lives in ~/.dbt/, not in the repo
├── packages.yml
├── models/
│   └── [area]/                                 # one folder per work area, e.g. claims_analysis/
│       ├── staging/
│       │   ├── _stg_[area]__sources.yml        # e.g. _stg_claims_analysis__sources.yml
│       │   ├── _stg_[area]__models.yml         # e.g. _stg_claims_analysis__models.yml
│       │   └── stg_[area]__[entity].sql        # e.g. stg_claims_analysis__medical.sql
│       ├── intermediate/                       # optional; reusable or isolated transformations between staging and marts
│       │   ├── _int_[area]__models.yml         # e.g. _int_claims_analysis__models.yml
│       │   └── int_[area]__[description].sql   # e.g. int_claims_analysis__categorized.sql
│       ├── marts/
│       │   ├── _mart_[area]__models.yml        # e.g. _mart_claims_analysis__models.yml
│       │   └── mart_[area]__[concept].sql      # e.g. mart_claims_analysis__monthly_spend.sql
│       └── audits/                             # optional; diff models comparing two relations
│           ├── _audit_[area]__models.yml       # e.g. _audit_claims_analysis__models.yml
│           └── audit_[area]__[description].sql # e.g. audit_claims_analysis__carrier_services_diff.sql
├── macros/
│   ├── common/
│   │   └── macro_common__[name].sql            # e.g. macro_common__normalize_date.sql
│   └── [area]/
│       └── macro_[area]__[name].sql            # e.g. macro_claims_analysis__params.sql
├── tests/
│   ├── generic/
│   │   └── test_common__[name].sql             # e.g. test_common__valid_npi.sql (generic test)
│   └── [area]/
│       └── test_[area]__[name].sql             # e.g. test_claims_analysis__totals_match.sql (singular test)
├── analyses/                                   # optional; compiled-but-not-materialized SQL for one-off diffs / queries
│   ├── common/
│   │   └── analysis_common__[name].sql         # e.g. analysis_common__schema_drift.sql
│   └── [area]/
│       └── analysis_[area]__[name].sql         # e.g. analysis_claims_analysis__refactor_diff.sql
├── seeds/
│   ├── common/
│   │   └── seed_common__[name].[ext]           # e.g. seed_common__state_codes.csv
│   └── [area]/
│       └── seed_[area]__[name].[ext]           # e.g. seed_claims_analysis__lookup.csv
└── snapshots/                                  # optional; SCD2 history of any relation
    ├── common/
    │   └── snapshot_common__[entity].yml       # e.g. snapshot_common__npis.yml
    └── [area]/
        └── snapshot_[area]__[entity].yml       # e.g. snapshot_claims_analysis__enrollment.yml
```

### Portable SQL

| Avoid | Use instead |
|-------|-------------|
| `::` cast | `CAST(x AS type)` |
| `DATE_PART()` | `EXTRACT()` |
| `DATEDIFF()`, `DATEADD()` | `dbt_utils.datediff()`, `dbt_utils.dateadd()` |
| `GENERATE_SERIES()` | `dbt_date.get_base_dates()` |
| `STRING_AGG()` / `LISTAGG()` | `dbt_utils.listagg()` |
| `NOW()` | `CURRENT_TIMESTAMP` |
| `~` regex / `REGEXP_LIKE()` | avoid or wrap in a macro |
| `IFF()` | `CASE WHEN` |
| `QUALIFY` | wrap in CTE with window function |
| `NVL()` / `IFNULL()` | `COALESCE()` |
| `TRY_CAST()` | wrap in a macro |

### Example files

| File | Description |
|------|-------------|
| [dbt-int-model-example.sql](../references/dbt-int-model-example.sql) | Intermediate model with CASE logic |
| [dbt-sources-yml-example.yml](../references/dbt-sources-yml-example.yml) | Sources YAML for raw tables |
| [dbt-models-yml-example.yml](../references/dbt-models-yml-example.yml) | Models YAML: docs, data tests, and a unit test |
| [dbt-params-macro-example.sql](../references/dbt-params-macro-example.sql) | Area params macro returning a dict |
| [dbt-require-var-macro-example.sql](../references/dbt-require-var-macro-example.sql) | Shared helper that reads a required var and raises on missing |
| [dbt-project-example.yml](../references/dbt-project-example.yml) | dbt_project.yml with materialization defaults |
| [dbt-profiles-example.yml](../references/dbt-profiles-example.yml) | profiles.yml with local DuckDB and Snowflake targets |
