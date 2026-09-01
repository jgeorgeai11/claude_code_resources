# How sas data resolution works

## The job

A SAS process reads SAS datasets and produces results. To rewrite it against a different data source — or to run it in a different system — you need to know, for every SAS variable: which documented column it came from, what that column means, and what to read instead.

The script answers those questions from the catalog — the documented metadata in the `catalog` schema of `metadata_db`, the metadata database, queried through its MCP server — and writes the answers to one file. It makes no decisions and generates no code. The planning skill that runs next does the deciding.

One contract underpins everything: **the catalog fully accounts for every origin SAS dataset.** The inventory only ever describes datasets the SAS process read, and an origin SAS dataset's variables are all documented — including fields the source system itself derived and added, which are catalogued like any other. So the script never meets a variable that legitimately has no origin column. Either it accounts for the entire input, or it fails naming every gap; there is no partial success.

## The records

Every `record_type` in `input_schema_resolution.jsonl`, in write order; an origin/dest pair shares one entry.

**Payload rule.** Carry every catalog value except timestamps, update reasons, a data source's owner, and anything derivable from one record plus the `meta` header. A column added to the catalog later is carried by default.

- **`meta`** — the process declaration: `process_name` and the four coordinates (`origin_system`, `dest_system`, `origin_data_scope`, `dest_data_scope`). The systems are always present — Step 1 fails a `meta` missing either. Either data scope may be null: a null `dest_data_scope` states "no data transition", and a null `origin_data_scope` states that nothing was declared process-wide, which is legal when every dataset overrides it. The dataset records still carry the scope that actually applied.
- **`origin_system` / `dest_system`** — a declared system's catalog prose. A system is a physical place data lives and compute runs (the catalog's `systems` registry); catalog names never mention one, so the same table can exist in several systems at different physical addresses. A `dest_system` record always publishes; an `origin_system` exactly when the systems differ.
- **`origin_data_source` / `dest_data_source`** — a data source's description and notes. `owner` is not carried — it routes review, not meaning.
- **`origin_schema` / `dest_schema`** — a schema's description and notes.
- **`origin_table` / `dest_table`** — a table's prose, its grain (`primary_key_columns` — sorted, dotless leaf names, and empty while the catalog's `is_primary_key` flags are unauthored), and a physical address: `dest_table` addressed in the destination system, `origin_table` in the origin system. The **dest tables** — the tables the converted code will actually read — are derived, never declared: the target tables the surviving candidates reference, plus the origin tables of any non-transitioning dataset; one `dest_table` record per member. An `origin_table` exists for every SAS parent that is not a dest table, plus — when the systems differ — every parent that is (the id-matched pair carrying both addresses).
- **`ref_table`** — a code set: the catalog table a coded column's `ref_table_id` points at, enumerating its valid values, resolved like any table in play — prose, grain, and the physical address of the instance hosting it (the metadata database's `reference` schema). Takes no origin/dest form: a lookup a reader consults, not something the SAS process read or the converted code writes.
- **`dest_column`** — a column the emitted code reads (a surviving candidate's `target_expression`, a dest join condition, or a dest table's key names it): type, nullability, grain flag, prose, and code-set pointer — the origin-column shape minus the mapping machinery.
- **`origin_join` / `dest_join`** — a join's `relationship_name`, condition, cardinality, `use_when`, notes (where grain caveats live), and `validated` flag. A `dest_join` is what the converted code emits, over the dest tables; an `origin_join` records how the SAS input was assembled from its parents, is emitted only in transition, and is dropped when the same relationship is already a `dest_join` — dest takes precedence, so one relationship never publishes twice.
- **`origin_concept` / `dest_concept`** — a concept: authored prose (an explanation) about something the structure cannot express — `label`, definition, notes, and `related_object_ids`. Anchored to exactly one catalog object, named inside its `concept_id`; the side follows the anchor — a concept anchored to an object appearing only on the origin side publishes as `origin_concept`, every other anchor (dest-side or shared) as `dest_concept`, per the collapse rule.
- **`origin_sas_dataset`** — one SAS dataset the process read: name, filepath, and its **resolved** data scopes — no system fields, which are the `meta` header's alone — the effective `origin_data_scope` (always present and non-empty; Step 1 fails a dataset without one) and `dest_data_scope` (null when not transitioning). `meta` carries what was declared; each dataset record states what actually applied.
- **`origin_sas_variable`** — one SAS variable with its resolution: the SAS metadata carried verbatim, and `origin_columns[]` — the catalog columns the name matched — each with its own prose, grain and code-set facts, `mapping_status`, and `candidates[]`.

## Other vocabulary

- **Catalog** — the documented metadata: systems, data sources, schemas, tables, columns, deployments, relationships, mappings, and concepts. It is one schema (`catalog`) of `metadata_db`, the metadata database, which also hosts the `reference` schema — the curated code-set *data* the catalog documents, where `ref_table` records' physical addresses point. The resolver queries only the catalog, through the metadata database's MCP server; every fact in a resolution is drawn from it.
- **Origin / destination** — the file's one axis. `dest_*` records describe the world the emitted code reads — the file's resident subject, always fully described. An `origin_*` catalog record appears exactly where the origin world differed, and a pair's two forms carry identical fields; the `origin_sas_*` records (the inventory itself) are always the origin's.
- **Collapse rule** — what decides whether a record publishes in both forms or only its dest form. System-free content (prose, joins, concepts) collapses into its dest form on data identity alone: without a data source change the tables the code reads are the SAS input's own, so emitting both forms would list every object twice. Address-bearing table records collapse only when data *and* system are both unchanged — so a system-only conversion carries every dest table as an id-matched `origin_table` / `dest_table` pair, one address per side. Read `dest_table` for the converted code's addresses either way.
- **Data scope** — a list of prefixes naming regions of the catalog, each 1 to 3 segments: a data source, a schema, or a table. Declared as `origin_data_scope` / `dest_data_scope`; a scope is handled whole — one columns or mappings query covers all of its prefixes. Prefixes match at segment boundaries only, mirroring ltree `<@`: `a.b` covers `a.b` and `a.b.c`, but not `a.bc`.
- **Candidate** — one documented way to produce a column's value in the destination data source, nested under the origin column it maps (`origin_columns[].candidates[]`). A column can have several. Candidate fields keep the catalog's own `column_mappings` names (`mapping_name`, `target_expression`, `target_tables_referenced`, `use_when`, `notes`, `validated`) — they are carried catalog rows, not resolution inventions.
- **No-equivalent mapping** — a mapping with no target expression: the catalog stating there is deliberately no equivalent, and, in its `notes`, what the destination uses instead. It produces the column status `no_equivalent`.
- **Catalog gap** — a variable matching no documented column, an in-transition column with no usable mapping, or a table in play not deployed where the process needs it. Gaps fail the run, written as `missing_variable` / `missing_candidate` / `missing_deployment` records in `input_schema_catalog_gaps.jsonl`; the remedy is always to fix the catalog or a wrong coordinate, never to remove the variable from the inventory. A missing **join** is never among them: every gap the script raises is provable from the catalog and the inventory alone, and whether two tables must be combined is a fact about the SAS code — which the script never reads. It publishes every documented join among the tables in play and leaves spotting a needed-but-undocumented relationship to the planning skill.

## The queries it issues

| # | Fetches | Feeds | Issued |
|---|---|---|---|
| 1 | every declared system and data scope that exists — checked four ways at once (`systems`, `data_sources`, `schemas`, `tables`) | the Step 2 existence check; nothing else runs until it passes | once, first |
| 2 | the declared systems' prose (`systems`) | `origin_system` / `dest_system` records | once |
| 3 | the documented columns under a dataset's `origin_data_scope` whose name the inventory carries (`columns`) | the search space variables match against (Step 3) | once per distinct `origin_data_scope` |
| 4 | every mapping from the origin columns the variables matched — wherever its target points (`column_mappings`) | the raw candidate pool; Step 4 filters it to each dataset's `dest_data_scope` | once per run, after matching; skipped when nothing transitions or nothing matched |
| 5 | joins with both endpoints among the dest tables (`table_relationships`) | `dest_join` records | once |
| 6 | joins among the origin tables (`table_relationships`) | `origin_join` records | once; skipped when no dataset transitions |
| 7 | prose for the schemas in play (`schemas`) | `origin_schema` / `dest_schema` records | once |
| 8 | prose for the data sources in play (`data_sources`) | `origin_data_source` / `dest_data_source` records | once |
| 9 | the `is_primary_key` columns of every table in play (`columns`) | `primary_key_columns` on the table records; the dest tables' keys also join the dest-column collection | once, plus once more for code sets the dest columns reveal (row 10) |
| 10 | the destination columns the expressions, the dest joins, and the dest tables' grain read, by exact id (`columns`) | `dest_column` records | once |
| 11 | where every table in play is deployed, with physical names (`deployment_tables`) | the addresses and the deployment gate (Step 9) | once |
| 12 | prose for every table in play (`tables`) | `origin_table` / `dest_table` / `ref_table` descriptions | once |
| 13 | explanations anchored to the objects in play (Step 10) (`concepts`) | `origin_concept` / `dest_concept` records | once |

The rows are numbered in the order the queries run. Results are memoized per statement, so datasets sharing a scope share the result.

**Rows 3 and 4 are bounded by the inventory, not by the catalog.** A cataloged table can run to thousands of columns — several times the MCP server's row cap — while an inventory asks about a handful of names, so neither query pulls a scope wholesale: row 3 carries the variable names as a predicate, and row 4 names the matched origin columns by id. The two pool differently, because their keys differ in what they need to stay unambiguous. A name means different columns under different scopes, so row 3 pools the names of the datasets *sharing a scope* and stays one query per distinct scope. A column id is globally unique, so row 4 pools every transitioning dataset's matched ids into one query for the whole run. Pooling is safe either way because a query only builds the search space — Step 3's matching decides, per variable, what that variable matched.

That bounding is what makes the row cap (`MCP_MAX_ROWS`) a meaningful signal. A truncated result fails the run, since a short column list would silently empty a variable's `origin_columns`; and because the fetches are inventory-sized, a truncation says the run genuinely needs more rows than the cap allows — one common name matching across a very wide scope, say. Raising the cap, together with the statement timeout that becomes binding next, is the remedy.

Two derived sets appear above. The **tables in play** are every table the file publishes a table record for: the dest tables, the origin tables, and the ref tables (Step 8). The **objects in play** widen that for the concepts query: the tables in play plus their schemas and data sources, the origin columns the variables matched, and the dest columns (Step 10).

A run that fails on variable-resolution gaps (missing variables or mappings) issues only rows 1–4 — the coordinate check, the system prose, and the columns and mappings fetches: that gap check sits between variable resolution and everything else, so a run that cannot publish spends nothing on joins, deployment, or explanations. A run where *no* variable matched anything is cheaper still: row 4 has no column ids to ask about, so it is skipped. One unmatched variable among matches does not skip it — the matched columns' mappings are still fetched before the gate fires. A deployment gap necessarily fires later, because the query that decides it is the one that answers it: it fails at row 11 (Step 9), sparing only the table-prose and concepts queries (rows 12–13).

## Step 1 — Resolve each origin SAS dataset's effective coordinates

Four coordinates govern everything: `origin_system`, `dest_system`, `origin_data_scope`, `dest_data_scope`. All four are declared in the inventory's `meta` record, but they are not uniform — only the two data scopes may be overridden per dataset.

**A data-scope override replaces the default; it does not merge.** A dataset can therefore narrow the process-wide scope — from a whole schema down to one table — and its variables are then searched only there.

**The systems are meta-only.** Deployment is resolved once over the pooled dest tables (Step 9), so a process has exactly one system pair by construction — a per-dataset system could only ever contradict it, and an inventory whose dataset records carry one is rejected. A process genuinely reading from two systems would need per-dataset deployment resolution, which this design does not attempt.

**Gate.** Each value must be 1–3 lowercase `[a-z0-9_-]` segments, checked *before* any SQL is built, because these values are interpolated into SQL literals:

```
uppercase segment  → invalid segment: catalog id segments are lowercase [a-z0-9_-]
four segments      → at most 3 are allowed ({data_source}[.{schema}[.{table}]])
empty string       → must be a non-empty string
trailing newline   → invalid segment (the pattern anchors on \A and \Z, since Python's
                     $ also matches before a trailing newline and `ocs\n` would
                     otherwise reach Postgres as a raw driver error)
```

A dataset with no effective `origin_data_scope` also raises — there would be nothing to resolve against. So does a `meta` missing either system: deployment cannot be resolved against an undeclared system, and every published file carries both.

## Step 2 — Prove every coordinate is real, before anything else

The script collects the distinct systems and scopes, then issues a single four-way `union all` — systems against `systems`, and each coordinate against `data_sources`, `schemas`, and `tables` simultaneously, because a coordinate may legitimately be any of those three depths. Anything that fails to resolve is named in the error.

Nothing else runs until this passes. A coordinate can be perfectly well-formed and still name nothing, so this is the check that separates a typo from a real region of the catalog, and it fails loudly rather than producing a half-empty file that looks successful.

This check asks only whether a coordinate *exists* — a real table that is not the one the SAS process read passes it. That mistake is caught later, by the gap check in Step 6, where it surfaces as a list of unmatched variables.

**Also here.** The two systems are read off `meta` — the only place they may be declared (Step 1) — fixing the single system pair that deployment is later resolved against. Their catalog prose (`systems.description` and `notes`) is fetched and publishes as the `origin_system` / `dest_system` records: the systems are coordinates of every conversion, so their meaning travels with the file rather than living only in skill documentation. Per the collapse rule, a `dest_system` record always publishes; an `origin_system` record exactly when the systems differ.

## Step 3 — Fetch the columns and match each variable by name

One query per distinct `origin_data_scope` fetches the documented columns under any of its prefixes whose name the inventory carries: the scope bounds where a column may live, the names bound which columns are worth fetching. Each SAS variable's name is then matched, case-insensitively, against the columns of its own dataset's scope — every documented column the variable could have come from.

**The predicate is an optimization; the match is the rule.** The names go into SQL to keep the result inventory-sized, and the SQL comparison is the looser of the two (`lower()` against a casefolded name), so a row it returns that the match rejects is simply dropped. The reverse must never happen — the predicate hiding a row the match would accept — which is why both sides fold case rather than trusting catalog names to be lowercase. Matching also stays the one thing that keeps a variable's matches its own, since the rows arrive pooled across the datasets sharing the scope.

**A name that cannot enter SQL is excluded, not fatal.** SAS permits a variable name to contain anything under `validvarname=any`, and the inventory is not the script's to police, so a name outside the catalog id charset (lowercase `[a-z0-9_-]`) is left out of the predicate rather than interpolated or raised on. No catalog column name can be drawn from outside that charset, so leaving the name out resolves it to zero matches — the same gap a name that reached the catalog and found nothing produces. The exclusion is logged, so the gap is traceable to the guard rather than read as missing documentation. A scope where no name is eligible issues no query at all.

**One match.** The ordinary case.

**Several matches.** Every match is kept — but several matches is first a statement about the scope, not the variable. The variable came from exactly one column; matching more than one means the dataset's `origin_data_scope` was not narrow enough to say which. A dataset left at the whole schema lets a variable match in every table of that schema; the same dataset narrowed to one table matches only there. So the first remedy is to tighten `origin_data_scope` in the inventory and rerun — declare the dataset's actual parent tables, not the schema they sit in.

Some ambiguity survives even the narrowest honest scope: a derived dataset built by joining several parents legitimately has all of them in scope, and the parents can share a name — a beneficiary key present on both a claim table and its line table, for instance. That residue is real ambiguity, not loose scoping. The script cannot tell which match the SAS process read, so it refuses to guess and emits all of them; the planner resolves the residue from the columns' descriptions and the join grain.

**Zero matches is a catalog gap.** Under the contract in the job there is no such thing as a variable without an origin: origin SAS datasets are fully documented, so a variable matching nothing means the catalog is missing a column, the dataset's `origin_data_scope` points somewhere the SAS process did not read, or the variable's name fell outside the eligible charset above. The variable is recorded on the missing-variables list as `(origin_sas_dataset, origin_sas_variable)` and resolution continues to the next variable — the run fails later, with the complete list (Step 6).

## Step 4 — Fetch the mappings and filter each origin column's candidates

One query fetches every mapping whose origin column is one the variables matched — wherever the mapping's target points. It is an exact-id fetch, issued after Step 3 because that is when the ids exist, and pooled across every transitioning dataset into a single query: a column id is globally unique, so the scope adds nothing once matching has resolved which columns are in play, and only the matched columns' mappings are ever read. It is skipped entirely when no dataset transitions, and equally when nothing matched. Whether a dataset uses the results is its own transition: a dataset without `dest_data_scope` is handed no mappings, even when another dataset matched the same column and pulled its mappings into the pool.

Ownership runs variable → columns → candidates. A variable carries `origin_columns[]` — the catalog columns its name matched in Step 3. Each origin column carries its own `candidates[]`, drawn from the mapping rows keyed on that column's id. A variable with several origin columns therefore has several independent candidate lists, and everything below happens per column, not per variable.

A candidate survives only if **every** table its expression references falls inside `dest_data_scope` — a mapping pointing at a region this conversion is not targeting is not usable. A no-equivalent mapping references no tables and always survives, deliberately: it describes the *origin* column, not a target.

## Step 5 — Derive the status

Per origin column — never per variable; two columns behind one variable can carry different statuses — from the candidates that **survived** the filter:

- **`mapped`** — an equivalent is documented: at least one candidate has a target expression
- **`no_equivalent`** — the catalog affirmatively documents that none exists: candidates exist, all no-equivalent mappings, each naming the substitute in its notes
- **`not_applicable`** — the question was never asked: the dataset has no `dest_data_scope`, so mappings were never consulted and the converted code reads the same column the SAS process did

**An in-transition column with no surviving candidate has no status — it is a catalog gap**, recorded as `(origin_sas_dataset, origin_sas_variable, origin_column_id)` on the missing-candidates list; the run fails in Step 6. Why nothing survived does not matter — no mapping documented, or every mapping targeting outside `dest_data_scope` — the fix is catalog-side either way: document the mapping, or correct `dest_data_scope`. One record per column covers every cause, so a column cannot be listed twice. The check holds per column even for an ambiguous variable: whichever match the planner later picks must already be answered, and a silent match the SAS process never actually read is the loose-scope symptom (Step 3) — narrowing `origin_data_scope` removes it from the search, not from the check.

Each published status is therefore a guarantee: `mapped` and `no_equivalent` always carry at least one candidate, and `not_applicable` appears exactly when the dataset is not transitioning, always with none. A published file never contains a column the catalog was silent about — silence failed the run instead.

## Step 6 — Fail on catalog gaps, all of them at once

After the last variable resolves — and before any later query is spent — the two gap lists are checked. If either is non-empty the run fails, logging every gap and writing the lists as `input_schema_catalog_gaps.jsonl` beside the would-be output. Gaps accumulate across every dataset rather than failing on the first, so one run names the complete work order — fixing a mis-scoped or under-documented process is one edit-and-rerun cycle — and no draft is written, because nothing was assembled:

```
CATALOG GAP: variable '<origin_sas_dataset>.<origin_sas_variable>' matches no column under origin_data_scope [...]
CATALOG GAP: column '<origin_column_id>' ('<origin_sas_dataset>.<origin_sas_variable>') has no usable mapping into dest_data_scope [...]
<n> missing variable(s), <m> missing mapping(s);
work order written to <...>/input_schema_catalog_gaps.jsonl -- document the gaps in the catalog
or fix the coordinates; never trim the inventory to pass
```

The work order is data, not log prose. `input_schema_catalog_gaps.jsonl` carries one `missing_variable` record per unmatched variable (`origin_sas_dataset`, `origin_sas_variable`, the `origin_data_scope` searched) and one `missing_candidate` record per silent column (`origin_sas_dataset`, `origin_sas_variable`, `origin_column_id`, and the `dest_data_scope` nothing reached — it carries `dest_data_scope` in place of `origin_data_scope`, not alongside it); deployment gaps write `missing_deployment` records to the same file from their own later gate (Step 9).

It is deliberately not a resolution — everything in `input_schema_resolution.jsonl` is truth the planner may act on, while "nobody has documented this yet" is false the moment the gap is fixed — so it lives in a differently named file, validated as it is written (`data_val_catalog_gaps.py`; the same checks run standalone via `--input-data`) and deleted by the next run that gets past every catalog gate -- whether it publishes or is rejected by output validation -- so a stale work order never outlives its fix. The staleness rule runs the other way too: a gap failure removes any resolution a prior run published at the output path, so planning never reads an outdated resolution beside a fresh work order.

Its serialization is as fixed as the resolution's, and for the same reason — a diff against the last run only means something if one work order has one form. Records are grouped by type in the order `missing_variable`, `missing_candidate`, `missing_deployment` and sorted within each group by their identifying fields: `(origin_sas_dataset, origin_sas_variable)`, then `origin_column_id`; `(table_id, system)` for a deployment gap.

## Step 7 — Derive the dest tables

The dest tables come from the mappings, never from the declared scopes: `dest_data_scope` decides which candidates survive (Step 4) but contributes no tables itself, so a table inside the scope that no surviving candidate references stays out. The set is the records entry's derivation — the surviving candidates' `target_tables_referenced`, plus the origin tables of any non-transitioning dataset — and having one set is what makes the rest of the run identical whether or not the data source changes.

## Step 8 — Look up what the tables in play imply

Fixing the dest tables all but completes the tables in play — code sets the dest columns reveal below still join it — and the lookups run over them: dest joins over the dest tables, origin joins over the origin tables (skipped without a transition — the SAS parents are then dest tables, and the dest joins already cover them), and prose for the schemas and data sources of the dest tables and SAS parents (code sets are excluded: a `ref_table` publishes without a schema or data-source record of its own). Table prose is deferred past the deployment gate (Step 9), so a run that fails there never spends it.

**Dest columns.** The emitted code's read columns are collected three ways:

1. **the columns the surviving candidates' `target_expression`s read** — found by scanning each expression's text for a known table id followed by a column name; this catches the columns a condition tests as well as the one it yields, and nothing that is not a documented table can match. The loader resolves every reference in a `target_expression` against the corpus and rejects an unknown one, and catalog ids are lowercase, so a loaded expression's references are lowercase too
2. **the columns the dest joins' conditions compare**
3. **the dest tables' `primary_key_columns`** — grain columns get read in practice, as join keys, partition filters, and GROUP BY columns

The set is fetched by exact id and published as `dest_column` records. A `ref_table_id` on any of them pulls its code set into the tables in play — earning a `ref_table` record — exactly as a pointer on an origin column does.

## Step 9 — Resolve the physical addresses and gate deployment

The deployment rows are fetched once over every table in play — the dest, origin, and ref tables; they supply the addresses the table records publish and the facts the gate checks.

**Addresses.** A catalog id may not match the physical name — a code set documented as `ref.codes.<name>` may physically live somewhere else entirely — so every table record carries its resolved address, and generated code uses only those, never a catalog id.

**Gate: every table in play must be deployed where the process needs it** — origin tables in the origin system (otherwise the file would assert the SAS process read a table that exists nowhere it ran), dest tables in the destination system (otherwise planning is handed an unreachable option), and every code set somewhere (`system: null` when deployed nowhere). Every hole is collected and the run fails once, writing `missing_deployment` records to the same `input_schema_catalog_gaps.jsonl` with the same validation and cleanup as Step 6 — a run that reaches this gate passed Step 6, so a gaps file never mixes the two failures' records:

```
{"record_type": "missing_deployment", "table_id": "ocs.non_institutional.clm_line",              "system": "warehouse"}
{"record_type": "missing_deployment", "table_id": "edwc_prd.claims_vw_prd.v_clm_line", "system": "edw"}
```

The remedy adds one option to the usual pair: document the missing deployment row, stand the table up (or defer the conversion), or fix a wrong coordinate.

Because the gate holds, a published resolution's tables are always reachable where the process needs them — which is why no per-candidate deployability flag is carried (it would be a constant) and no venue list publishes: reachability is the gate's job, and the copy-switch fact is carried by the id-matched table pair.

With the gate passed, the deferred table prose (query row 12) is fetched and the table records assemble — prose, grain, address.

## Step 10 — Attach the concepts

A concept is anchored to exactly one catalog object — a data source, a schema, a table, or a column — and is carried when the conversion touches **that exact object**. The objects in play are the tables in play with their schemas and data sources, the origin columns the variables matched, and the dest columns (Step 8's set).

Two boundary rules keep the scope honest. Anchoring is matched exactly, never as a subtree — accepting anything beneath an in-scope schema would return a concept for every column of views hundreds of columns wide, most of which the conversion never reads. And grain widens the scope only through the dest tables, whose keys are read columns; the keys of transition-case SAS parents and of code sets stay out — for those tables, grain is metadata, not something the emitted code reads — and a concept about a table's key *structure* belongs anchored to the table, which is already in scope.

## Step 11 — Order, validate, publish

Records are written in a fixed order, sorted by their identifying ids within each group — descend the catalog hierarchy, origin before dest within each level, the SAS input last:

```
meta
origin_system │ dest_system
origin_data_source │ dest_data_source
origin_schema │ dest_schema
origin_table │ dest_table │ ref_table
dest_column
origin_join │ dest_join
origin_concept │ dest_concept
origin_sas_dataset │ origin_sas_variable
```

The order is chosen for a reader. By the time the variable bulk arrives, every pointer a variable carries — table, ref table, dest column, dataset, substitute concept — is already defined, so each variable is fully interpretable on first read. One soft spot is accepted: a concept anchored to an *origin* column precedes the variable records that define origin columns — the cheap kind of forward reference, since the anchor is a self-describing dotted id and the definition is self-contained prose. Within a record, fields lead with the container id before the leaf (`table_id` before a column id), everywhere a column appears. The enforced fixedness itself is what matters mechanically: byte-identical reruns and stable diffs rest on there being exactly one legal serialization; the particular arrangement is for comprehension.

The file is written to a `.draft` path first, validated, and only then moved into place. If validation fails the draft is left for debugging and the run exits non-zero — the planning skill reads this file, so an invalid resolution must never be mistakable for a usable one. A rejected run also clears both artifacts a previous run may have left: an earlier resolution, which planning must not read as current, and an earlier work order, since reaching validation means every catalog gate passed and the gaps it recorded are fixed. The draft itself is cleared at startup rather than on the way out — only this step writes one, so a run that fails earlier would otherwise leave an older run's rejected output beside its own fresh artifacts. A `.draft` on disk therefore always belongs to the run that just ran.

The output checks enforce the completeness contract independently of the resolver:

1. An empty `origin_columns` list is rejected, and so is a file carrying no `origin_sas_variable` record at all: every rule below is per variable, so a resolution that accounts for nothing would satisfy them all vacuously.
2. An in-transition column must be `mapped` or `no_equivalent` with at least one candidate; `not_applicable` is required exactly when the dataset has no `dest_data_scope`, with no candidates.
3. Every `dest_table`, `origin_table`, and `ref_table` must carry a full physical address.
4. Every column a surviving candidate's `target_expression`, a dest join, or a dest table's key list references must have exactly one `dest_column` record.
5. Every non-null `ref_table_id` — on an origin column or a dest column — has exactly one `ref_table` record.
6. A `dest_system` record always exists; an `origin_system` record exactly when the systems differ.
7. Every table record carries `primary_key_columns`.
8. Every `origin_sas_dataset` carries its resolved data scopes: `origin_data_scope` non-empty and `dest_data_scope` null exactly when the dataset does not transition, each agreeing with the inventory's declaration-plus-overrides.
9. A `dest_table` exists exactly for the tables the surviving candidates reference plus the origin tables of the non-transitioning datasets.
10. An `origin_table` exists exactly for the SAS parents that are not dest tables plus — when the systems differ — every parent that is.
11. A concept publishes on the side its anchor decides: `dest_concept` when the anchor is a dest-side object (a dest table, a code set, one of their prefixes, or a referenced dest column), `origin_concept` only when it appears on the origin side alone.

A resolver regression that stops raising on gaps therefore fails validation rather than publishing.

## The three layouts, by conversion shape

System-free record types (prose, joins, concepts) are keyed on the **data transition**; the address-bearing table records key on the systems as well — an `origin_table` appears wherever the SAS process's address differs from the converted code's. That yields three layouts. (Records below are elided to structure; the `ocs_claims` example cast throughout.)

**A — no system change, no data transition** (e.g. SAS→dbt in place). The thinnest file: dest-side everything, no mapping machinery.

```jsonl
{"record_type": "meta", "origin_system": "warehouse", "dest_system": "warehouse", "origin_data_scope": ["ocs.non_institutional"], "dest_data_scope": null, …}
{"record_type": "dest_system", "system": "warehouse", "description": "…"}
{"record_type": "dest_data_source", "data_source_id": "ocs", …}
{"record_type": "dest_schema", "schema_id": "ocs.non_institutional", …}
{"record_type": "dest_table", "table_id": "ocs.non_institutional.clm_sgmt", "primary_key_columns": ["claimno", "person_key", "recno", "sgmt_num"], "physical_database_name": "ocs", …}
{"record_type": "ref_table", …}
{"record_type": "dest_column", …}              ← join-condition and grain columns: no expressions exist
{"record_type": "dest_join", …}
{"record_type": "dest_concept", …}
{"record_type": "origin_sas_dataset", …}
{"record_type": "origin_sas_variable", …, "origin_columns": [{…, "mapping_status": "not_applicable", "candidates": []}]}
```

No `origin_*` catalog records at all — nothing about the origin world differs from the destination's, and absence is the statement of that; only the SAS inventory (`origin_sas_*`) carries the origin marker, because it always does. Every variable is `not_applicable`.

**B — system change, no data transition** (the system-only migration, warehouse→edw). Layout A plus the system split's two consequences: an `origin_system` record joins `dest_system`, and every dest table is emitted as an **id-matched pair** — an `origin_table` at the address the SAS process read (`ocs.non_institutional.clm_sgmt`, hosted in the `ocs` database) beside the `dest_table` at the address the converted code will read (the same table in whatever database the destination system hosts its copy in), same prose, same `primary_key_columns`. The pair *is* the copy-switch signal: the SAS process read one physical copy and the converted code will read the other — same columns guaranteed by deployment, same rows not — and its two addresses are exactly what a reconciliation task compares. Prose records stay dest-side only: descriptions do not vary by system.

**C — data transition** (ocs → edwc_prd; the committed example, which also changes system). The full two-sided file — origin and destination now name *different* catalog objects:

```jsonl
{"record_type": "meta", …, "origin_system": "warehouse", "dest_system": "edw", "dest_data_scope": ["edwc_prd.claims_vw_prd"]}
{"record_type": "origin_system", "system": "warehouse", "description": "…"}
{"record_type": "dest_system", "system": "edw", "description": "…"}
{"record_type": "origin_data_source", "data_source_id": "ocs", …}
{"record_type": "dest_data_source", "data_source_id": "edwc_prd", …}
{"record_type": "origin_schema", …}
{"record_type": "dest_schema", …}
{"record_type": "origin_table", "table_id": "ocs.non_institutional.clm_sgmt", "primary_key_columns": [...], "physical_database_name": "ocs", …}   ← the SAS parents
{"record_type": "dest_table", "table_id": "edwc_prd.….v_clm", "primary_key_columns": [...], …}                  ← the dest tables
{"record_type": "ref_table", …}
{"record_type": "dest_column", …}              ← expression, join-condition, and grain columns
{"record_type": "dest_join", …}                ← what the converted code emits
{"record_type": "origin_concept", …}
{"record_type": "dest_concept", …}
{"record_type": "origin_sas_dataset", …}
{"record_type": "origin_sas_variable", …, "origin_columns": [{…, "mapping_status": "mapped", "candidates": [{"target_expression": "edwc_prd.…", …}]}]}
```

(The fourth combination — transition without a system change — is layout C with both sides' addresses resolving in the same system.)

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

A ✓ says the layout admits that form, not that a file will carry one. The system, data-source, schema, table and SAS rows are structural — the code emits them for that shape. `ref_table`, `dest_column`, the joins and the concepts are conditional on the catalog documenting one: the committed layout-C example carries no `origin_join` at all, because the catalog documents no relationship among the `ocs` tables.

## Failure modes

Every one of these exits non-zero rather than publishing. The first three are **catalog gaps**: the run writes every gap to `input_schema_catalog_gaps.jsonl` beside the never-written resolution. **Removing the variable from the inventory is never a remedy** — the inventory records the source dataset, and trimming it to pass defeats the check.

| Exit cause | Fix |
|------------|-----|
| Missing variable — a SAS variable matches no column under its dataset's `origin_data_scope`, or carries a name outside the catalog id charset (`missing_variable` records) | Document the column in `data_catalog/` and rebuild, or fix a wrong `origin_data_scope`. When that scope names another process's interface schema (`{team}.int_{process}_sas`), the gap usually means that process has not published its interface documentation yet: finish its interface-validation task rather than editing this inventory or redirecting the scope at the interface's grandparents |
| Missing mapping — an in-transition column has no usable candidate into `dest_data_scope` (`missing_candidate` records) | Document the mapping (or the no-equivalent mapping with its rationale) and rebuild, or fix a wrong `dest_data_scope` |
| Missing deployment — an origin table not deployed in the origin system, a dest table not deployed in the destination system, or a code set deployed nowhere (`missing_deployment` records; `system: null` is the nowhere case) | Document the deployment row and rebuild; stand the table up, or defer the conversion; or fix a wrong coordinate |
| Undeclared or unresolvable coordinate | A meta missing either system, or a system or data scope that is not a catalog row; fix the inventory and rerun (both systems are always required — deployment cannot be resolved against an undeclared system) |
| Dataset system override | A dataset record carries `origin_system` / `dest_system`; systems are process-wide — declare them once on the meta record (regenerate the inventory) |
| Inventory with nothing to resolve | The inventory carries no `origin_sas_dataset` or no `origin_sas_variable` record. Extraction publishes an inventory of empty datasets with only a WARNING, and resolving one would publish a file that accounts for nothing; fix the extraction (usually an unreadable or wrongly pathed dataset) and rerun both steps |
| Truncated query result | The MCP server's row cap (`MCP_MAX_ROWS`) cut a result short. For the two inventory-bounded fetches (rows 3 and 4) that means the run genuinely needs those rows; any other query truncating means the catalog region itself outgrew the cap. Either way, raise the cap and the statement timeout server-side together, then rerun |
| Transport or auth failure | The MCP server is unreachable, or the bearer token is missing or rejected |
| Validation failure | The resolution did not satisfy the validator. The rejected draft is at `<output>.draft` — always this run's — and any resolution a prior run published has been removed, so nothing stale reads as current. Read the logged errors, fix the cause, and rerun |

## What it refuses to do

- **Never picks between two name matches.** An ambiguous variable ships as several origin columns.
- **Never invents a mapping, and never publishes past silence.** A variable or mapping the catalog cannot account for fails the run with the complete gap list — a catalog work order for a human, not a blank for the planner to fill.
- **Never treats the inventory as negotiable.** The gap remedy is catalog-side or coordinate-side; the origin dataset's variable list is a record, not an input to tune until the run passes.
- **Never judges whether a documented no-equivalent is acceptable here.** The catalog says what the destination uses instead; whether that is good enough is a judgment sent to the user.
- **Never puts a catalog id anywhere near generated code.** Only physical addresses.
- **Never publishes an invalid file.**

The consistent principle: a published resolution is a complete, catalog-backed account of the SAS input, and every judgment the catalog cannot make is handed forward rather than made quietly.
