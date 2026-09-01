# metadata_db example — the two interface schemas

A worked example of the catalog YAML the interface documentation authors: the kept output of one process, documented on both sides of a conversion. The team here is `demo_proj`, the process is `ocs_claims`, and the content matches the committed extraction and resolution examples — `PERM.OCS_CLAIMS_SUMMARY` as `output_schema.jsonl` inventories it. The rules this example instantiates — and the per-field contract — are in [authoring_conventions.md](../authoring_conventions.md).

```
docs/activities/sas_conversion/ocs_claims/data_catalog/    mirrors the catalog repo, so
  sources/                                                  copying it in is a straight merge
    demo_proj/                    the project team's data source — not the conversion's to create
      int_ocs_claims_sas/        the outputs as the SAS process writes them
        schema.yaml              the conversion MINTS this schema — the file creates it
        tables.yaml
        columns/                 folder form — columns split across shard files
          identity.yaml
          metrics.yaml
        mappings/
          int_ocs_claims_converted.yaml   every interface column, mapped into the converted side
      int_ocs_claims_converted/  the outputs the converted code writes
        schema.yaml
        tables.yaml
        columns.yaml             single-file form
```

## What this example shows

- **The data source is the team; the schemas are the conversion's.** `demo_proj` is the project team's own data source, maintained outside the conversion — here it was already in place. The two `int_{process}_{side}` schemas under it are not: each side's `schema.yaml` mints one.
- **Both column forms.** `int_ocs_claims_sas` uses the `columns/` folder form (`identity.yaml` + `metrics.yaml`) and `int_ocs_claims_converted` the single-file form, purely to demonstrate both — either form is valid anywhere, never both for one scope.
- **A no-equivalent mapping.** The converted output drops `person_key` — the summary is re-keyed on `mbr_sk` — so `mappings/int_ocs_claims_converted.yaml` carries a `target_expression: null` entry naming the substitute in its `notes`, alongside three ordinary same-name mappings.
- **`validated: true`, earned.** The mappings were reconciled against the built code by the team that implemented them; that is what permits true here.
- **No `table_relationships.yaml`.** The process keeps one output, so there is nothing to relate — its absence is the correct authoring, not an omission.
- **`ref_table` unused.** None of these columns carries coded values.
