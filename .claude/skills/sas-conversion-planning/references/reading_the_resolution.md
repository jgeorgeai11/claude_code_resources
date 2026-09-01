# Reading the resolution

What `input_schema_resolution.jsonl` is, what each record carries, and what planning is expected to do with it. Written for the planner consuming the file, not the resolver producing it.

The format itself is owned by the skill that writes it: `sas-data-resolution`'s [readme.md](../../sas-data-resolution/references/readme.md) is authoritative on what the resolver emits and when. This page is the planner's view of the same file — what each record is *for* — and when the two disagree, that one is right and this one needs updating.

## What the file is

A complete, catalog-backed account of one SAS process's input. For every SAS variable it states which documented column the variable came from, what that column means, and what to read instead in the destination.

Two properties follow from how it is produced, and both matter when planning:

- **It is complete or it does not exist.** A variable the catalog cannot account for, a mapping it cannot supply, or a table not deployed where the process needs it fails the run — the gaps are written to `input_schema_catalog_gaps.jsonl` as a catalog work order and no resolution is published. A resolution in hand is therefore a complete account of everything it can prove. One gap is beyond its sight: whether two tables the plan must combine share a documented relationship — a fact about what the code needs, which only planning knows. The skill's join rule says what to do when a table is unreachable.
- **It makes no decisions.** The resolver never picks between two name matches, never invents a mapping, and never judges whether a documented no-equivalent is acceptable. Those judgments are handed forward deliberately. Making them is planning's job.

## The one axis: origin and destination

`dest_*` records describe the world the emitted code reads — the file's resident subject, always fully described. An `origin_*` catalog record appears exactly where the origin world differed. The `origin_sas_*` records are the SAS inventory itself and are always present.

So absence is meaningful: no `origin_table` for a given dest table means the converted code reads the same table at the same address the SAS process did.

## Vocabulary

- **Catalog** — the documented metadata (systems, data sources, schemas, tables, columns, deployments, relationships, mappings, concepts). Every fact in a resolution is drawn from it. It lives in the `catalog` schema of `metadata_db`; the sibling `reference` schema holds the code-set *data* that `ref_table` addresses point at. Both are reachable read-only through the metadata database's MCP server, exposed as the `mcp__metadata_db__*` tools — that is how anything the resolution does not carry gets looked up.
- **System** — a physical place data lives and compute runs. Catalog names never mention a system, so the same table can exist in several systems at different physical addresses.
- **Catalog id vs physical address** — a `table_id` is the catalog's system-free name for a table; the physical address is `physical_database_name.physical_schema_name.physical_table_name`, resolved for one system. The same table has one id and as many addresses as systems hosting it. Only addresses can appear in emitted code.
- **Grain** — the row identity of a table, carried mechanically in `primary_key_columns` and stated in prose in the table's `description`. When `primary_key_columns` is empty the catalog's key flags are still being authored, and the description is the fallback.
- **Data scope** — a list of catalog prefixes, each 1 to 3 segments (data source, schema, or table). Prefixes match at segment boundaries: `a.b` covers `a.b` and `a.b.c`, but not `a.bc`.
- **Data transition** — the destination is a different part of the catalog than the origin. Declared per process in `meta` and resolved per dataset; a null `dest_data_scope` means no transition.
- **Candidate** — one documented way to produce a column's value in the destination, nested under the origin column it maps. A column can carry several. Fields are the catalog's own: `mapping_name`, `target_expression`, `target_tables_referenced`, `use_when`, `notes`, `validated`.
- **Join** — one documented way to relate two tables: `relationship_name`, the condition, `cardinality`, `use_when`, `notes`, and `validated`. A table pair can hold several — usually different grains or different filters — and `use_when` is required on every row once it does, stating what each is for.
- **No-equivalent mapping** — a mapping with no target expression: the catalog stating there is deliberately no equivalent, with what the destination uses instead named in its `notes`. Produces `mapping_status: no_equivalent`.
- **Concept** — authored prose about something the structure cannot express: entity meaning, code values, cross-system correspondence, grain. Anchored to exactly one catalog object. Concepts arrive already scoped to the conversion — each hangs on an object the resolution actually reads — so there is no filtering to do and no wider set to go looking for.
- **Copy switch** — the SAS process read one physical copy of a table and the converted code will read another. It appears as an id-matched `origin_table`/`dest_table` pair: same `table_id`, same prose, same `primary_key_columns`, two different addresses. Deployment guarantees the columns match; nothing guarantees the rows do.

## The records

In write order. "Read it for" is what planning takes from each.

| record_type | What it is | Read it for |
|---|---|---|
| `meta` | The process declaration: `process_name` and the four coordinates. Both systems are always present; only the data scopes may be null | The process being converted, and whether a transition was declared at all |
| `origin_system` / `dest_system` | A declared system's catalog prose. `dest_system` always publishes; `origin_system` only when the systems differ | What each system is, behind the labels |
| `origin_data_source` / `dest_data_source` | A data source's description and notes | Orientation before reading its columns |
| `origin_schema` / `dest_schema` | A schema's description and notes | What the schema covers, and any convention its descriptions follow |
| `origin_table` | A SAS parent table, addressed in the origin system. Emitted for parents that are not dest tables — plus, when the systems differ, as the id-matched pair member for each dest table the SAS process read | The grain to reproduce (`primary_key_columns` and the description), which table a name came from, and the origin address of a copy switch |
| `dest_table` | A table the converted code reads, addressed in the destination system. Derived from the surviving candidates plus the origin tables of any non-transitioning dataset | The physical address to emit, `primary_key_columns` for the GROUP BY grain, and what the code reads |
| `ref_table` | A code set a coded column points at, with the physical address of the instance hosting it | Checking an expression's literals against real values |
| `dest_column` | A column the emitted code reads — named by a surviving expression, a `dest_join` condition, or a dest table's key | `data_type` for casts and comparisons, `is_nullable` for missing-value handling, `ref_table_id` for literal checking, plus prose |
| `origin_join` | How the SAS input was assembled from its parents. Emitted only in transition, and only where the same relationship is not already a `dest_join` — dest takes precedence | Confirming the reproduction sits at the same grain; `notes` carries fan-out caveats |
| `dest_join` | What the converted code emits, over the dest tables | The joins to write; `use_when` where a pair holds more than one; `notes` for fan-out and filtering |
| `origin_concept` / `dest_concept` | A concept's `label`, definition, `notes`, and `related_object_ids` | Meaning the structure cannot carry (see below) |
| `origin_sas_dataset` | One SAS dataset the process read, with its **resolved** scopes. `meta` carries what was declared; this states what actually applied | `dest_data_scope` — present means this dataset transitions |
| `origin_sas_variable` | One SAS variable with `origin_columns[]` — the catalog columns its name matched — each carrying its own prose, grain and code-set facts, `mapping_status`, and `candidates[]` | Everything needed to resolve the variable |

## How a variable resolves

Ownership runs variable → origin columns → candidates, and everything below happens per column, not per variable.

**`origin_columns[]` is a list because the match may be ambiguous.** The variable's name matched more than one documented column and the catalog cannot tell them apart. Each column carries its own prose, its own `mapping_status`, and its own `candidates[]`, so a variable with two origin columns has two independent resolutions. What usually separates two same-named columns is their `description` and `notes`, which state the file setting or grain each belongs to.

**`candidates[]` is a list because a column can be mapped more than one way.** Each candidate's `use_when` states what that mapping is for; `validated` records whether it has been confirmed. A candidate survives into the file only if every table it references falls inside the dataset's `dest_data_scope`. A no-equivalent mapping references none and survives deliberately.

**`mapping_status` sits on the origin column, not the variable**, so a variable with two origin columns can carry two different statuses. Three values are the whole vocabulary:

| Status | Means | What the converted code reads |
|---|---|---|
| `mapped` | The column transitions and the catalog documents at least one way to produce it | The chosen candidate's `target_expression` |
| `no_equivalent` | The column transitions but the catalog affirmatively documents that the destination has no equivalent | Nothing directly — the substitute named in the mapping's `notes`, described by a concept |
| `not_applicable` | The dataset has no data transition | The same column the SAS process read, under its existing name |

**The transition is per dataset.** Each `origin_sas_dataset` carries its own resolved `dest_data_scope`, so one process can mix transitioning and non-transitioning datasets. `meta` states what was declared; the dataset records state what actually applied.

**`related_object_ids` is a pointer, not content.** It names the catalog objects a concept's mechanism lives in, and most are deliberately *not* in the resolution — a concept exists precisely because its mechanism sits in columns the conversion does not read. An empty or non-overlapping list is not a sign the concept is irrelevant.

## The three layouts

Which records appear depends on the conversion's shape. Prose, joins, and concepts key on the **data transition**; the address-bearing table records key on the **systems** as well.

| Record type | A: language only | B: system only | C: data (+ system) |
|---|---|---|---|
| `origin_system` / `dest_system` | dest only | both | both (dest only if the system holds) |
| `origin_data_source` / `origin_schema` | — | — | ✓ |
| `dest_data_source` / `dest_schema` | ✓ | ✓ | ✓ |
| `origin_table` | — | ✓ (paired with dest) | ✓ (the parents) |
| `dest_table` | ✓ | ✓ | ✓ (from the candidates) |
| `ref_table` / `dest_column` | ✓ (joins + grain) | ✓ (joins + grain) | ✓ (expressions + joins + grain) |
| `origin_join` | — | — | ✓ |
| `dest_join` | ✓ | ✓ | ✓ |
| `origin_concept` | — | — | ✓ |
| `dest_concept` | ✓ | ✓ | ✓ |
| `origin_sas_dataset` / `origin_sas_variable` | ✓ (all `not_applicable`) | ✓ (all `not_applicable`) | ✓ (`mapped` / `no_equivalent`) |

A ✓ says the layout admits that record, not that a file will carry one. The system, data-source, schema, table and SAS rows are structural — the resolver emits them for that shape. `ref_table`, `dest_column`, the joins and the concepts appear only where the catalog documents one: a layout-C conversion whose parents have no documented relationship carries no `origin_join` at all, which is the case in the Python example. Absence of a conditional record says the catalog is silent, not that the conversion is simple.

**A — no system change, no data transition** (e.g. SAS→dbt in place). The thinnest file: dest-side everything, no mapping machinery, every variable `not_applicable`. No `origin_*` catalog records at all, because nothing about the origin world differs.

**B — system change, no data transition** (a system-only migration). Layout A plus an `origin_system` record, and every dest table emitted as an **id-matched pair**: an `origin_table` at the address the SAS process read beside the `dest_table` at the address the converted code will read, same prose, same `primary_key_columns`. The pair is the copy-switch signal — same columns guaranteed, same rows not.

**C — data transition.** The full two-sided file; origin and destination name different catalog objects. A transition without a system change is layout C with both addresses resolving in the same system.

## What the resolution leaves to planning

The resolver hands these forward rather than deciding them:

- Which origin column a variable actually came from, when the name matched more than one.
- Which candidate to use, when a column carries more than one.
- Which join to use, when a table pair holds more than one.
- Whether a documented no-equivalent substitute is acceptable for this conversion.

Everything else in the file is a catalog fact. These four are decisions, and the plan must record each one with its rationale.
