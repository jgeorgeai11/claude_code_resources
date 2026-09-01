# Common SAS macros

Shared utility macros Warehouse teams use but do not write. They are not part of any process, so no process documentation file defines them — the SAS documentation records their calls as-is, arguments and all, and this file is what those documented calls *mean* to the planner: the pattern behind the call, and how the pattern converts.

Telling a shared macro from a component macro takes the whole-process view planning has: a documented call whose `%MACRO` definition appears in one of the process's documentation files is a component macro — its own documentation covers it. A call defined in no process documentation file is shared; this file covers it. A call that is neither is settled by the skill's no-guessing rule — ask the user — and is a candidate entry here.

One entry per macro, growing as macros are encountered. Every entry answers the same three questions:

| Question | For |
|---|---|
| **What it does** | Reading the documented call |
| **What a call implies** | Facts about the data or process the call betrays |
| **Converting the pattern** | What the plan does with it |

## macarray

**What it does.** Builds an indexed family of macro variables from a list — elements supplied directly or read from a dataset — plus a count, so a `%DO` loop can apply the same code to each element. The loop body runs once per element with the current element substituted in.

**What a call implies.** The same logic repeats over a list: columns, datasets, years, file suffixes. The list is the meaningful input; the indexed macro variables and the loop are machinery. When the list is read from a dataset, the repetition is data-driven — the elements are not knowable from the code alone.

**Converting the pattern.** The iteration machinery vanishes. A loop over columns usually becomes a set-wise operation; a loop over datasets usually means those datasets union into one table or the logic parameterizes over a real list in the host language. When the list was data-driven, the converted code derives it from data too — never a hardcoded copy of the list the SAS run happened to see.

## supertask

**What it does.** Applies a piece of code to several datasets in parallel. The canonical use: one logical dataset physically split into many files on a key — claims split into 100 files on the last two characters of the beneficiary id — with the same logic run per file concurrently, and the per-file results combined by a separate later step.

**What a call implies.** The input is **one logical dataset, physically partitioned**. The partition key is a storage fact worth recording; the file count is incidental. A supertask call travels with a partner: some later documented step combines the per-file results, and that step is the rollup completing this pattern, not independent logic. Partition-indexed names (`CLM_00`–`CLM_99`) are one input, not a hundred. (The extraction step already treats such a split as one dataset: its config and both inventories carry a `*` in the dataset name and path — `SRCLIB.CLM_*`, `clm_*.sas7bdat` — so the split is a fact you read off the inventory rather than infer.)

**Converting the pattern.** Collapse it: the converted code reads the full table, and per-file logic plus rollup usually becomes a single set-based operation — per-file provider summaries re-summarized later are one `GROUP BY` over the whole table. Two checks before collapsing:

- **The rollup must be a mechanical re-aggregation** of the per-file results (sums of sums, min of mins). If the combine step carries its own logic beyond recombining — filtering partitions, treating one file specially — that logic survives into the plan even though the fan-out does not.
- **Per-file outputs must be intermediates.** A `*` on a kept output's `dataset` / `filepath` in `output_schema.jsonl` says the process delivers the split itself, so collapsing changes the interface its consumers read — flag it as a decision for the user rather than deciding it.
