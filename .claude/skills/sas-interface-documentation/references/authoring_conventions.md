# Authoring conventions

The file set and rules for the catalog YAML this skill authors, for any team and process. The Field legends section below carries the per-field contract; a worked instance of all of it is in [metadata_db_example/](metadata_db_example/).

## Where it lands

**The data source is the project team** — `demo_proj`, say — holding everything that team ingests and maintains. A conversion never creates one: it belongs to whoever maintains the team's estate. Usually it is already there, since the team has other data; on a team's first conversion it may not be, and then its `data_source.yaml` is a prerequisite to report rather than something to author here. Without it the interface schemas cannot load at all — the load resolves every reference against the corpus, and a schema whose data source is missing has nothing to hang from.

**The two schemas under it are the conversion's**, one per side of the same process:

| Side | Schema | Holds |
|---|---|---|
| SAS | `{team}.int_{process}_sas` | the kept outputs as the SAS process writes them |
| Converted | `{team}.int_{process}_converted` | the same outputs as the converted code writes them |

The `int_` prefix marks these as **interface** schemas, distinguishing a conversion's published outputs from the team's other estates (ingested reference data and the like) in the same data source. Embedding `{process}` is load-bearing: a consumer's `missing_variable` gap then names the process whose interface documentation has to land first.

**Scope at the schema level, never the data source.** Both sides live under one data source, so a consumer declaring `dest_data_scope` as `{team}` would span the origin schema as well and let a mapping pointing back at the SAS side survive the candidate filter. The correct declaration names the schemas: `origin_data_scope: ["{team}.int_{process}_sas"]`, `dest_data_scope: ["{team}.int_{process}_converted"]`. The two are distinct prefixes — matching is at segment boundaries, so `…_sas` never covers `…_converted`.

## The files

- **`schema.yaml`** — one per side: the required `description` that brings the interface schema into existence. Neither `int_{process}_sas` nor `int_{process}_converted` exists until its own `schema.yaml` creates it; the `{team}` data source a level above is not this file's to create (see Where it lands).
- **`tables.yaml`** — one entry per table, with the grain stated in `notes`.
- **columns** — one entry per column. Two mutually exclusive forms: a single `columns.yaml`, or a `columns/` folder whose shard files group columns by subject area. Never both for the same scope. The shard filename is a label only — a row's identity comes from its `table_name` and `column_name`, exactly as in the single-file form.
- **`mappings/`** — one file per target, named for it (the stem is a grouping label; the target is identified by each expression's own column references). Mappings live on the **source** side and point at the target, so the `int_{process}_sas` columns carry the mappings into `int_{process}_converted`.
- **`table_relationships.yaml`** — authored only when the process's kept outputs relate to one another; each relationship is documented, or the next consumer's planner hits the join gap: a table unreachable through documented relationships. Its absence is correct for a single-output process. This scope is deliberately narrower than the catalog allows (`table_b_id` may name any documented table): relationships from the interface tables outward to other estates are their documenters' work, not this conversion's.

A conversion never authors `data_source.yaml`, `deployments.yaml`, or `systems.yaml`.

## Conventions

- **Ids are positional in the tables and columns files.** `column_id` is `{source}.{schema}.{table_name}.{column_name}` — assembled from the path plus the body fields, never written out. That is why the `{source}/{schema}/` order is load-bearing: `{team}/int_{process}_sas/` yields `{team}.int_{process}_sas.…`, and swapping the two segments silently swaps source and schema. The mappings file is the exception: it crosses scopes, so it writes fully qualified ids — `source_column_id` and every id inside a `target_expression`.
- **`is_primary_key` appears only when true.** Its absence means false.
- **`ref_table` is the code-set pointer.** A column carrying coded values names the enumerating table (3-segment dotted id, e.g. `ref.codes.clm_type_cd`) on every carrier column; it loads into `ref_table_id` and serves context retrieval only — no join path implied.
- **A same-name mapping is still written.** A column keeping its name on the converted side is a real entry; absence, not sameness, is what breaks the next conversion. A rename is an ordinary direct `target_expression` the same way.
- **A dropped or reshaped variable gets a no-equivalent mapping**, not a missing one — `target_expression: null` with the substitute named in `notes` (required on a drop). The reason a mapping is never simply absent: a future consumer's resolution treats an unmapped interface column as a `missing_variable` gap and fails.
- **`validated` means human-verified equivalence and defaults false.** A mapping reconciled against the built code by the team that implemented it may set true; a mapping authored from the plan's intent alone stays false. The loader stamps `validated_ts` on the false→true flip.
- **`target_tables_referenced` is never authored.** The loader derives it from the expression parse (empty array for a drop).
- **`update_reason` is null on a first authoring.** It carries the why on later edits.

## What the YAML depends on downstream

The YAML protects the next conversion only once it is **loaded**: the consumer's resolution queries the catalog database, not these files. The load — and the deployment entries in each source's `deployments.yaml`, without which the new schema's tables fail the consumer's deployment gate — are maintained separately and are not this skill's work.

## Field legends

The per-field contract for every catalog table this skill writes. "Provided by": `[path]` — derived from the file's location (plus the row's name fields); `[yaml]` — authored in the body; `[auto]` — generated by the loader, never in the body. Ids are written generically; a real file's ids come from its own path.

### `schemas` (schema.yaml)

| col | type | provided by | required in YAML? |
|---|---|---|---|
| schema_id | ltree PK | [path] | n/a (`{source}.{schema}`) |
| data_source_id | ltree FK | [path] | n/a (`{source}`) |
| schema_name | text | [path] | n/a (`{schema}`) |
| description | text | [yaml] | yes — what this schema is |
| notes | text | [yaml] | optional (use null if none) |
| update_reason | text | [yaml] | null on insert; required on update |
| insert_ts / update_ts | timestamp | [auto] | no |

### `tables` (tables.yaml)

| col | type | provided by | required in YAML? |
|---|---|---|---|
| table_id | ltree PK | [path] | n/a (`{source}.{schema}.<table_name>`) |
| schema_id | ltree FK | [path] | n/a (`{source}.{schema}`) |
| table_name | text | [yaml] | yes — lowercase `[a-z0-9_-]`; not `concept` |
| description | text | [yaml] | yes — what this table is |
| notes | text | [yaml] | optional (use null if none) |
| update_reason | text | [yaml] | null on insert; required on update |
| insert_ts / update_ts | timestamp | [auto] | no |

### `columns` (columns.yaml, or columns/ shards)

| col | type | provided by | required in YAML? |
|---|---|---|---|
| column_id | ltree PK | [path] | n/a (`{source}.{schema}.<table>.<column>`) |
| table_id | ltree FK | [path] | n/a (`{source}.{schema}.<table>`) |
| table_name | text | [yaml] | yes — must name a documented table |
| column_name | text | [yaml] | yes — lowercase `[a-z0-9_-]` |
| data_type | text | [yaml] | yes — the documented type |
| is_nullable | boolean | [yaml] | yes |
| is_primary_key | boolean | [yaml] | optional (default false). Marks the table's GRAIN (every part of a composite key); informational, not loader-enforced |
| ref_table | text | [yaml] | optional — 3-segment dotted table id of the code set enumerating this column's value domain (loads into `ref_table_id`; context retrieval only, no join path implied). Set it on every carrier column |
| description | text | [yaml] | yes — what this column is |
| notes | text | [yaml] | optional (use null if none) |
| update_reason | text | [yaml] | null on insert; required on update |
| insert_ts / update_ts | timestamp | [auto] | no |

### `column_mappings` (mappings/*.yaml)

| col | type | provided by | required in YAML? |
|---|---|---|---|
| source_column_id | ltree PK | [yaml] | yes — a column in this file's schema folder |
| mapping_name | text PK | [yaml] | yes — says what the mapping is toward; >1 mapping on a source column requires `use_when` on all |
| target_expression | text | [yaml] | Portable Postgres SQL over the TARGET dataset's own columns — 4-segment refs, ≥1 column ref, deterministic, no SELECT/subquery, never the source column's own table. Aggregates/windows allowed (cross-grain). null = intentional drop (then `notes` is required) |
| target_tables_referenced | ltree[] | [auto] | derived from the parse; never NULL (empty array for drops) |
| use_when | text | [yaml] | optional; required per-row when a source column has >1 mapping |
| notes | text | [yaml] | optional; REQUIRED when `target_expression` is null |
| validated | boolean | [yaml] | optional, defaults false — human-verified equivalence? |
| validated_ts | timestamp | [auto] | stamped false→true, cleared true→false |
| update_reason | text | [yaml] | null on insert; required on update |
| insert_ts / update_ts | timestamp | [auto] | no |

### `table_relationships` (table_relationships.yaml)

| col | type | provided by | required in YAML? |
|---|---|---|---|
| table_a_id | ltree PK | [yaml] | yes — a table in this file's schema folder |
| table_b_id | ltree PK | [yaml] | yes — any documented table |
| relationship_name | text PK | [yaml] | yes — the unordered pair is unique per name; >1 relationship on a pair requires `use_when` on all |
| join_condition | text | [yaml] | yes — a boolean ON-style predicate in Postgres SQL; 4-segment refs touching only the two endpoints; deterministic; no SELECT/subquery |
| cardinality | text | [yaml] | optional enum: one_to_one / one_to_many / many_to_one / many_to_many (a→b); null until verified — never guess |
| use_when | text | [yaml] | optional; required per-row when a pair carries multiple relationships |
| notes | text | [yaml] | optional (use null if none) |
| validated | boolean | [yaml] | optional, defaults false — human-verified against real data? |
| validated_ts | timestamp | [auto] | stamped false→true, cleared true→false |
| update_reason | text | [yaml] | null on insert; required on update |
| insert_ts / update_ts | timestamp | [auto] | no |
