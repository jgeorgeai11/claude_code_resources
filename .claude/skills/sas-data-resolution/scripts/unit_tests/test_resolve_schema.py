"""Unit tests for resolve_schema.py.

Every test drives the resolver through the fake MCP client in catalog_fixtures.py,
which answers the catalog queries by filtering an in-memory fixture_ocs ->
fixture_edw catalog. No catalog data is mocked, so the SQL the resolver builds is exercised for
real: a scope the resolver renders wrongly returns the wrong rows. The only exceptions are the
main() tests, which patch resolve_schema.validate_schema_resolution,
resolve_schema.validate_catalog_gaps, resolve_schema.write_jsonl, resolve_schema.load_dotenv,
and client.run_sql to reach failure paths the fake catalog cannot produce.
"""

import json
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from catalog_fixtures import (
    OCS_CLM,
    OCS_CLM_LINE,
    EDW_BENE,
    EDW_CLM,
    EDW_CLM_LINE,
    FakeMCPClient,
    concept,
    deployment,
    mapping,
    relationship,
    table_prose,
    variable,
)

from resolve_schema import (
    CatalogGapError,
    CoordinateError,
    InventoryError,
    QueryRunner,
    build_dataset_records,
    build_meta_record,
    build_dest_tables,
    partition_variable_names,
    concept_scope,
    derive_mapping_status,
    dest_columns_referenced,
    filter_candidates,
    load_inventory,
    log_summary,
    main,
    match_origin_columns,
    origin_table_set,
    parse_config,
    resolve,
    resolve_coordinates,
    validate_scope_entry,
    write_jsonl,
)

Records = list[dict[str, Any]]
RunQuery = Callable[[str], list[dict[str, Any]]]


def of_type(records: Records, record_type: str) -> Records:
    """Select the records of one type.

    Args:
        records: The full output record list.
        record_type: The record_type to select.

    Returns:
        The matching records, in file order.
    """
    return [r for r in records if r["record_type"] == record_type]


def one_variable(records: Records, dataset: str, name: str) -> dict[str, Any]:
    """Select a single variable record.

    Args:
        records: The full output record list.
        dataset: The dataset the variable belongs to.
        name: The variable name.

    Returns:
        The variable record.
    """
    return next(r for r in of_type(records, "origin_sas_variable") if r["dataset"] == dataset and r["variable"] == name)


# --- Coordinate precedence ---


def test_resolve_coordinates_dataset_override_replaces_meta_default(meta: dict[str, Any]) -> None:
    """A dataset-level scope replaces the meta default outright rather than merging.

    Only the data scopes resolve per dataset: the venues are process-wide, so they
    never appear among a dataset's effective coordinates.
    """
    dataset = {"dataset": "SRCLIB.OCS_MEMBERS", "origin_data_scope": [OCS_CLM]}

    coords = resolve_coordinates(meta, dataset)

    assert coords == {"origin_data_scope": [OCS_CLM], "dest_data_scope": ["fixture_edw"]}


def test_resolve_coordinates_absent_everywhere_is_none(meta: dict[str, Any]) -> None:
    """A coordinate absent from both the dataset and meta resolves to None."""
    del meta["dest_data_scope"]

    assert resolve_coordinates(meta, {"dataset": "SRCLIB.X"})["dest_data_scope"] is None


def test_resolve_narrowed_dataset_sees_only_its_own_scope(
    meta: dict[str, Any], datasets: Records, variables: Records, run_query: RunQuery
) -> None:
    """A dataset narrowed to one table resolves against that table alone, not the union."""
    records = resolve(meta, datasets, variables, run_query)

    dataset_records = {r["dataset"]: r for r in of_type(records, "origin_sas_dataset")}
    assert dataset_records["SRCLIB.OCS_MEMBERS"]["origin_data_scope"] == [OCS_CLM]
    assert dataset_records["SRCLIB.OCS_CLAIMS"]["origin_data_scope"] == ["fixture_ocs.general"]
    # bene_sex_cd exists only on clm, so both datasets match it; a claims-only variable
    # would differ. Narrowing is visible on claim_no, which lives on both tables.
    claims = one_variable(records, "SRCLIB.OCS_CLAIMS", "claim_no")
    assert [c["table_id"] for c in claims["origin_columns"]] == [OCS_CLM, OCS_CLM_LINE]


# --- Scope lists are sets ---


def test_resolve_scope_orderings_produce_one_query(
    meta: dict[str, Any], client: FakeMCPClient, run_query: RunQuery
) -> None:
    """Two datasets whose origin_data_scope differs only in order share a single query."""
    meta["origin_data_scope"] = ["fixture_ocs.general", "fixture_edw"]
    datasets = [
        {"record_type": "origin_sas_dataset", "dataset": "SRCLIB.A", "filepath": "a.sas7bdat"},
        {
            "record_type": "origin_sas_dataset", "dataset": "SRCLIB.B", "filepath": "b.sas7bdat",
            "origin_data_scope": ["fixture_edw", "fixture_ocs.general"],
        },
    ]
    variables = [variable("SRCLIB.A", "claim_no"), variable("SRCLIB.B", "claim_no")]

    resolve(meta, datasets, variables, run_query)

    assert client.kinds().count("columns") == 1


def test_resolve_duplicate_scope_entries_collapse(
    meta: dict[str, Any], client: FakeMCPClient, run_query: RunQuery
) -> None:
    """A repeated scope entry changes nothing: scope lists compare as sets."""
    meta["origin_data_scope"] = ["fixture_ocs.general", "fixture_ocs.general"]
    datasets = [{"record_type": "origin_sas_dataset", "dataset": "SRCLIB.A", "filepath": "a.sas7bdat"}]

    resolve(meta, datasets, [variable("SRCLIB.A", "claim_no")], run_query)

    columns_sql = next(sql for kind, sql in client.calls if kind == "columns")
    assert columns_sql.count("'fixture_ocs.general'") == 1


# --- Source column matching ---


def test_match_origin_columns_is_case_insensitive(catalog: dict[str, list[Any]]) -> None:
    """SAS names are case-insensitive against catalog column names."""
    matches = match_origin_columns(catalog["columns"], "CLAIM_NO")

    assert [m["column_id"] for m in matches] == [f"{OCS_CLM}.claim_no", f"{OCS_CLM_LINE}.claim_no"]


def test_resolve_ambiguous_variable_carries_every_source_column(
    meta: dict[str, Any], datasets: Records, variables: Records, run_query: RunQuery
) -> None:
    """A variable matching two tables carries both columns; the script never chooses."""
    record = one_variable(resolve(meta, datasets, variables, run_query), "SRCLIB.OCS_CLAIMS", "claim_no")

    assert [c["origin_column_id"] for c in record["origin_columns"]] == [
        f"{OCS_CLM}.claim_no", f"{OCS_CLM_LINE}.claim_no",
    ]


def test_resolve_unmatched_variable_is_a_catalog_gap(
    meta: dict[str, Any], datasets: Records, run_query: RunQuery
) -> None:
    """A variable matching nothing is a catalog gap, never a legal outcome.

    A source dataset's variables are all documented, so zero matches means missing
    catalog documentation or a wrong origin_data_scope.
    """
    variables = [variable("SRCLIB.OCS_CLAIMS", "derived_flag")]

    with pytest.raises(CatalogGapError, match="derived_flag") as excinfo:
        resolve(meta, datasets[:1], variables, run_query)

    assert excinfo.value.gaps == [{
        "record_type": "missing_variable",
        "origin_sas_dataset": "SRCLIB.OCS_CLAIMS",
        "origin_sas_variable": "derived_flag",
        "origin_data_scope": ["fixture_ocs.general"],
    }]


def test_resolve_accumulates_unmatched_variables_across_datasets(
    meta: dict[str, Any], datasets: Records, run_query: RunQuery
) -> None:
    """Unmatched variables in two datasets raise once, naming all of them.

    Failing on the first gap would make fixing a mis-scoped process an iterative
    grind; a single run names the complete catalog work order.
    """
    variables = [
        variable("SRCLIB.OCS_CLAIMS", "derived_flag"),
        variable("SRCLIB.OCS_MEMBERS", "invented_cd"),
    ]

    with pytest.raises(CatalogGapError) as excinfo:
        resolve(meta, datasets, variables, run_query)

    assert "derived_flag" in str(excinfo.value) and "invented_cd" in str(excinfo.value)
    assert [(g["origin_sas_dataset"], g["origin_sas_variable"]) for g in excinfo.value.gaps] == [
        ("SRCLIB.OCS_CLAIMS", "derived_flag"), ("SRCLIB.OCS_MEMBERS", "invented_cd"),
    ]
    assert all(g["record_type"] == "missing_variable" for g in excinfo.value.gaps)


def test_resolve_carries_sas_metadata_verbatim(
    meta: dict[str, Any], datasets: Records, variables: Records, run_query: RunQuery
) -> None:
    """Nothing from the inventory is lost: type, format, length, and label pass through."""
    record = one_variable(resolve(meta, datasets, variables, run_query), "SRCLIB.OCS_CLAIMS", "claim_no")

    assert (record["type"], record["format"], record["length"], record["label"]) == (
        "char", "$CHAR8.", 8, "Claim No",
    )


# --- Candidate filtering ---


def test_filter_candidates_drops_candidate_outside_dest_data_scope() -> None:
    """A candidate reading a table outside dest_data_scope is filtered out."""
    rows = [
        mapping("c", "in_scope", f"{EDW_CLM}.x", [EDW_CLM]),
        mapping("c", "out_of_scope", "other_db.public.t.x", ["other_db.public.t"]),
    ]

    assert [c["mapping_name"] for c in filter_candidates(rows, ["fixture_edw"])] == ["in_scope"]


def test_filter_candidates_keeps_no_equivalent_mappings_regardless_of_scope() -> None:
    """A no-equivalent mapping survives any filter: it describes the source, not a target."""
    rows = [mapping("c", "default", None, [], notes="No equivalent.")]

    assert filter_candidates(rows, ["fixture_edw"]) == rows


def test_filter_candidates_requires_every_referenced_table_in_scope() -> None:
    """A cross-scope candidate is dropped: every table it reads must be under dest_data_scope."""
    rows = [mapping("c", "straddle", "f(a,b)", [EDW_CLM, "other_db.public.t"])]

    assert filter_candidates(rows, ["fixture_edw"]) == []


def test_filter_candidates_sorts_by_mapping_name() -> None:
    """Candidates are ordered by mapping_name so a rerun is byte-identical."""
    rows = [
        mapping("c", "line_rollup", "sum(x)", [EDW_CLM_LINE], use_when="b"),
        mapping("c", "header", "x", [EDW_CLM], use_when="a"),
    ]

    assert [c["mapping_name"] for c in filter_candidates(rows, ["fixture_edw"])] == ["header", "line_rollup"]


def test_resolve_ignores_mappings_without_dest_data_scope(
    meta: dict[str, Any], datasets: Records, variables: Records, client: FakeMCPClient, run_query: RunQuery
) -> None:
    """Without dest_data_scope the data source is unchanged, so column_mappings is never read.

    The columns publish as not_applicable with empty candidates -- the mapping question
    was never asked -- and nothing raises: zero candidates is only a gap in transition.
    """
    del meta["dest_data_scope"]
    meta["dest_system"] = "warehouse"
    datasets = datasets[:1]

    records = resolve(meta, datasets, variables[:3], run_query)

    assert "mappings" not in client.kinds()
    for record in of_type(records, "origin_sas_variable"):
        assert all(c["candidates"] == [] for c in record["origin_columns"])
        assert all(c["mapping_status"] == "not_applicable" for c in record["origin_columns"])


def test_resolve_mappings_do_not_leak_between_datasets_sharing_a_scope(
    meta: dict[str, Any], run_query: RunQuery
) -> None:
    """A dataset without dest_data_scope gets no candidates, even when another shares its scope.

    The mappings fetch is pooled on matched column id alone, so two datasets matching
    the same column draw from one result; without a per-dataset gate the
    non-transitioning dataset would inherit the other's no-equivalent mappings, which
    survive any filter, and read as `no_equivalent` instead of `not_applicable`.
    """
    del meta["dest_data_scope"]
    meta["dest_system"] = "warehouse"
    datasets = [
        {"record_type": "origin_sas_dataset", "dataset": "SRCLIB.STAY", "filepath": "stay.sas7bdat"},
        {
            "record_type": "origin_sas_dataset", "dataset": "SRCLIB.MOVE", "filepath": "move.sas7bdat",
            "dest_data_scope": ["fixture_edw"],
        },
    ]
    variables = [variable("SRCLIB.STAY", "claim_no"), variable("SRCLIB.MOVE", "claim_no")]

    records = resolve(meta, datasets, variables, run_query)

    stay = one_variable(records, "SRCLIB.STAY", "claim_no")["origin_columns"]
    assert all(c["candidates"] == [] and c["mapping_status"] == "not_applicable" for c in stay)
    # The transitioning dataset still sees its no-equivalent mapping, so the gate is
    # per dataset, not global.
    move = one_variable(records, "SRCLIB.MOVE", "claim_no")["origin_columns"]
    assert move[0]["mapping_status"] == "no_equivalent"


# --- Mapping status ---


@pytest.mark.parametrize(
    "candidates, in_transition, expected",
    [
        ([], True, None),
        ([{"target_expression": "x"}], True, "mapped"),
        ([{"target_expression": None}], True, "no_equivalent"),
        ([{"target_expression": None}, {"target_expression": "x"}], True, "mapped"),
        ([], False, "not_applicable"),
    ],
)
def test_derive_mapping_status_reports_what_the_catalog_knows(
    candidates: Records, in_transition: bool, expected: str | None
) -> None:
    """Status states what the catalog knows; a silent in-transition column has none.

    Any expression maps, all-null is a documented no-equivalent, no transition means
    the question was never asked, and None marks the gap the caller must record.
    """
    assert derive_mapping_status(candidates, in_transition) == expected


def test_resolve_status_is_per_source_column_not_per_variable(
    meta: dict[str, Any], datasets: Records, catalog: dict[str, list[Any]], run_query: RunQuery
) -> None:
    """Two source columns behind one variable can carry two different statuses."""
    catalog["mappings"] = [
        m for m in catalog["mappings"]
        if m["source_column_id"] != f"{OCS_CLM_LINE}.claim_no"
    ] + [mapping(f"{OCS_CLM_LINE}.claim_no", "default", f"{EDW_CLM_LINE}.clm_line_hcpcs_cd", [EDW_CLM_LINE])]
    variables = [variable("SRCLIB.OCS_CLAIMS", "claim_no")]

    record = one_variable(resolve(meta, datasets[:1], variables, run_query), "SRCLIB.OCS_CLAIMS", "claim_no")

    assert [(c["table_id"], c["mapping_status"]) for c in record["origin_columns"]] == [
        (OCS_CLM, "no_equivalent"), (OCS_CLM_LINE, "mapped"),
    ]


def test_resolve_column_filtered_to_nothing_is_a_catalog_gap(
    meta: dict[str, Any], datasets: Records, catalog: dict[str, list[Any]], run_query: RunQuery
) -> None:
    """An in-transition column whose only mapping targets outside dest_data_scope is a gap.

    From the converted code's point of view there is no mapping to use, whether the
    catalog documented nothing or only mappings that target elsewhere -- the
    conversion as declared cannot proceed from the catalog.
    """
    catalog["mappings"] = [mapping(f"{OCS_CLM}.bene_sex_cd", "default", "other_db.public.t.x", ["other_db.public.t"])]
    variables = [variable("SRCLIB.OCS_CLAIMS", "bene_sex_cd")]

    with pytest.raises(CatalogGapError, match="bene_sex_cd") as excinfo:
        resolve(meta, datasets[:1], variables, run_query)

    assert excinfo.value.gaps == [{
        "record_type": "missing_candidate",
        "origin_sas_dataset": "SRCLIB.OCS_CLAIMS",
        "origin_sas_variable": "bene_sex_cd",
        "origin_column_id": f"{OCS_CLM}.bene_sex_cd",
        "dest_data_scope": ["fixture_edw"],
    }]


def test_resolve_reports_missing_variables_and_candidates_in_one_error(
    meta: dict[str, Any], datasets: Records, catalog: dict[str, list[Any]], run_query: RunQuery
) -> None:
    """A run with both gap kinds reports both lists in the one failure."""
    catalog["mappings"] = [m for m in catalog["mappings"] if m["source_column_id"] != f"{OCS_CLM}.bene_sex_cd"]
    variables = [
        variable("SRCLIB.OCS_CLAIMS", "bene_sex_cd"),
        variable("SRCLIB.OCS_CLAIMS", "derived_flag"),
    ]

    with pytest.raises(CatalogGapError) as excinfo:
        resolve(meta, datasets[:1], variables, run_query)

    assert "derived_flag" in str(excinfo.value) and "bene_sex_cd" in str(excinfo.value)
    assert [g["record_type"] for g in excinfo.value.gaps] == ["missing_variable", "missing_candidate"]


def test_resolve_variable_gap_failure_issues_no_query_past_the_mappings(
    meta: dict[str, Any], datasets: Records, client: FakeMCPClient, run_query: RunQuery
) -> None:
    """A run failing on variable-resolution gaps spends nothing on the later queries.

    The raise sits between variable resolution and everything else, so joins,
    deployment, scope, dest-column, and concept queries never run. The system prose
    (queries table row 2) is fetched before the gap check, so it is the one extra
    query a failing run still spends.
    """
    variables = [variable("SRCLIB.OCS_CLAIMS", "derived_flag")]

    with pytest.raises(CatalogGapError):
        resolve(meta, datasets[:1], variables, run_query)

    assert set(client.kinds()) <= {"coordinates", "system_prose", "columns", "mappings"}
    assert "system_prose" in client.kinds()


def test_resolve_carries_every_candidate_with_its_metadata(
    meta: dict[str, Any], datasets: Records, run_query: RunQuery
) -> None:
    """A multi-candidate column keeps every candidate, with use_when and notes intact."""
    variables = [variable("SRCLIB.OCS_CLAIMS", "sbmt_chrg_amt")]

    record = one_variable(resolve(meta, datasets[:1], variables, run_query), "SRCLIB.OCS_CLAIMS", "sbmt_chrg_amt")

    candidates = record["origin_columns"][0]["candidates"]
    assert [c["mapping_name"] for c in candidates] == ["header", "line_rollup"]
    assert all(c["use_when"] for c in candidates)
    assert candidates[0]["validated"] is True


# --- The read set ---


def test_resolve_read_set_is_dest_tables_when_transitioning(
    meta: dict[str, Any], datasets: Records, variables: Records, run_query: RunQuery
) -> None:
    """With dest_data_scope the read set is the target tables the candidates reference."""
    records = resolve(meta, datasets, variables, run_query)

    assert [r["table_id"] for r in of_type(records, "dest_table")] == [EDW_BENE, EDW_CLM_LINE]


def test_resolve_read_set_is_origin_tables_without_dest_data_scope(
    meta: dict[str, Any], datasets: Records, variables: Records, run_query: RunQuery
) -> None:
    """Without dest_data_scope the converted code reads the source tables themselves."""
    del meta["dest_data_scope"]
    meta["dest_system"] = "warehouse"

    records = resolve(meta, datasets[:1], variables[:3], run_query)

    assert [r["table_id"] for r in of_type(records, "dest_table")] == [OCS_CLM, OCS_CLM_LINE]


def test_resolve_read_set_excludes_no_equivalent_mappings(
    meta: dict[str, Any], datasets: Records, run_query: RunQuery
) -> None:
    """A no-equivalent mapping references no target, so it adds nothing to the read set."""
    variables = [variable("SRCLIB.OCS_CLAIMS", "person_key")]

    records = resolve(meta, datasets[:1], variables, run_query)

    assert of_type(records, "dest_table") == []


def test_resolve_no_equivalent_column_publishes_with_its_notes(
    meta: dict[str, Any], datasets: Records, run_query: RunQuery
) -> None:
    """A column whose candidates are all no-equivalent mappings is complete documentation.

    The catalog affirmatively states there is no equivalent and names the substitute
    in the mapping's notes; that is an answer, not silence, so nothing raises.
    """
    variables = [variable("SRCLIB.OCS_CLAIMS", "person_key")]

    record = one_variable(resolve(meta, datasets[:1], variables, run_query), "SRCLIB.OCS_CLAIMS", "person_key")

    assert [c["mapping_status"] for c in record["origin_columns"]] == ["no_equivalent", "no_equivalent"]
    for column in record["origin_columns"]:
        assert len(column["candidates"]) == 1
        assert column["candidates"][0]["target_expression"] is None
        assert column["candidates"][0]["notes"]


# --- Joins ---


def test_resolve_emits_source_and_dest_joins(
    meta: dict[str, Any], datasets: Records, variables: Records, run_query: RunQuery
) -> None:
    """Target joins come from the read set, source joins from the SAS input's parents."""
    records = resolve(meta, datasets, variables, run_query)

    assert [(r["table_a_id"], r["table_b_id"]) for r in of_type(records, "origin_join")] == [
        (OCS_CLM_LINE, OCS_CLM)
    ]
    assert of_type(records, "dest_join") == []


def test_resolve_relationship_qualifying_as_both_is_only_a_dest_join(
    meta: dict[str, Any], catalog: dict[str, list[Any]], run_query: RunQuery
) -> None:
    """Target takes precedence, so an overlapping relationship is not emitted twice."""
    # A mapping may target a table in its own data source, which puts the same
    # relationship in both the read set and the source tables. The read set stays in
    # fixture_ocs, so the target venue must be the one that deploys it.
    meta["dest_data_scope"] = ["fixture_ocs"]
    meta["dest_system"] = "warehouse"
    catalog["mappings"] = [
        mapping(f"{OCS_CLM}.claim_no", "default", f"{OCS_CLM}.claim_no", [OCS_CLM]),
        mapping(f"{OCS_CLM_LINE}.claim_no", "default", f"{OCS_CLM_LINE}.claim_no", [OCS_CLM_LINE]),
        mapping(f"{OCS_CLM_LINE}.hcpcs_cd", "default", f"{OCS_CLM_LINE}.hcpcs_cd", [OCS_CLM_LINE]),
    ]
    datasets = [{"record_type": "origin_sas_dataset", "dataset": "SRCLIB.A", "filepath": "a.sas7bdat"}]
    variables = [variable("SRCLIB.A", "claim_no"), variable("SRCLIB.A", "hcpcs_cd")]

    records = resolve(meta, datasets, variables, run_query)

    assert [(r["table_a_id"], r["table_b_id"]) for r in of_type(records, "dest_join")] == [
        (OCS_CLM_LINE, OCS_CLM)
    ]
    assert of_type(records, "origin_join") == []


def test_resolve_emits_no_origin_joins_without_a_transition(
    meta: dict[str, Any], datasets: Records, variables: Records, client: FakeMCPClient, run_query: RunQuery
) -> None:
    """With no transition the source tables ARE the read set, so dest_join covers them."""
    del meta["dest_data_scope"]
    meta["dest_system"] = "warehouse"

    records = resolve(meta, datasets[:1], variables[:3], run_query)

    assert of_type(records, "origin_join") == []
    assert [(r["table_a_id"], r["table_b_id"]) for r in of_type(records, "dest_join")] == [
        (OCS_CLM_LINE, OCS_CLM)
    ]
    assert client.kinds().count("joins") == 1


# --- Concepts ---


def test_concept_scope_is_the_prefixes_of_the_tables_in_play() -> None:
    """The scope is the 1- and 2-segment prefixes of the tables actually resolved."""
    assert concept_scope([OCS_CLM, EDW_CLM]) == [
        "fixture_edw", "fixture_edw.claims_vw", "fixture_ocs", "fixture_ocs.general",
    ]


def test_resolve_redundant_scope_entry_changes_nothing(
    meta: dict[str, Any], datasets: Records, catalog: dict[str, list[Any]], variables: Records,
    run_query: RunQuery,
) -> None:
    """A origin_data_scope entry nothing matches under is redundant, so it changes no output.

    This is why the DECLARED origin_data_scope is not itself a concept scope source: scoping
    concepts by it would emit that namespace's concepts, so a redundant scope entry
    would stop being a no-op.
    """
    # A real schema that holds a concept but no column any variable matches.
    catalog["schemas"].append("spare_db.public")
    catalog["concepts"].append(concept("spare_db.public.concept.unused", []))
    baseline = resolve(meta, datasets[:1], variables[:3], run_query)
    meta["origin_data_scope"] = ["fixture_ocs.general", "spare_db.public"]

    records = resolve(meta, datasets[:1], variables[:3], run_query)

    # meta and dataset echo what was declared, so only the resolution itself is compared.
    def resolution(all_records: Records) -> Records:
        return [r for r in all_records if r["record_type"] not in ("meta", "origin_sas_dataset")]

    assert resolution(records) == resolution(baseline)
    assert "spare_db.public.concept.unused" not in [r["concept_id"] for r in of_type(records, "concept")]


def test_resolve_emits_concepts_from_both_sides(
    meta: dict[str, Any], datasets: Records, variables: Records, run_query: RunQuery
) -> None:
    """Explanations are split by side and carried at every anchor depth.

    Database-, schema-, table-, and column-anchored explanations all reach the output,
    and a sibling schema of an in-play database stays out however the scope is matched.
    The target table-anchored explanation is reached through clm_from_dt: its translation
    is the only one in the fixture that reads the EDW claim table.
    """
    variables = variables + [variable("SRCLIB.OCS_CLAIMS", "clm_from_dt")]

    records = resolve(meta, datasets, variables, run_query)

    assert [r["concept_id"] for r in of_type(records, "origin_concept")] == [
        "fixture_ocs.concept.claim",
        "fixture_ocs.general.clm.claim_no.concept.claim_number",
        "fixture_ocs.general.clm.concept.claim_grain",
    ]
    assert [r["concept_id"] for r in of_type(records, "dest_concept")] == [
        "fixture_edw.claims_vw.bene.bene_sex_cd.concept.sex_coding",
        "fixture_edw.claims_vw.clm.concept.final_action",
        "fixture_edw.claims_vw.concept.claim",
    ]
    every_id = [r.get("concept_id") for r in records]
    assert "fixture_edw.other_schema.concept.unrelated" not in every_id


def test_resolve_omits_concepts_anchored_to_untouched_objects(
    meta: dict[str, Any], datasets: Records, variables: Records, run_query: RunQuery
) -> None:
    """An explanation on a table no translation reads stays out, even in an in-play schema.

    Without clm_from_dt nothing reads the EDW claim table, so its table-anchored
    explanation drops while the schema-anchored one above it -- reached through the
    beneficiary table -- is still carried. Matching everything beneath the schema instead
    would pull in explanations for every column of views hundreds of columns wide.
    """
    records = resolve(meta, datasets, variables, run_query)

    ids = [r["concept_id"] for r in of_type(records, "dest_concept")]
    assert "fixture_edw.claims_vw.clm.concept.final_action" not in ids
    assert "fixture_edw.claims_vw.concept.claim" in ids


# --- Deployment ---


def test_build_dest_tables_carries_prose_grain_and_the_dest_system_address() -> None:
    """A dest_table record is prose, grain, and the destination system's address.

    No `deployed_venues` publishes: reachability is the gate's job, and the
    copy-switch fact is carried by the id-matched origin_table/dest_table pair.
    """
    records = build_dest_tables(
        [OCS_CLM_LINE],
        [deployment(OCS_CLM_LINE, "warehouse")],
        [table_prose(OCS_CLM_LINE)],
        {OCS_CLM_LINE: ["claim_no", "lineitem", "person_key"]},
        "warehouse",
    )

    assert records[0]["primary_key_columns"] == ["claim_no", "lineitem", "person_key"]
    assert records[0]["physical_database_name"] == "fixture_ocs_warehouse"
    assert records[0]["physical_table_name"] == "clm_line"
    assert "deployed_venues" not in records[0]


def test_resolve_undeployed_read_set_table_is_a_catalog_gap(
    meta: dict[str, Any], datasets: Records, catalog: dict[str, list[Any]], run_query: RunQuery
) -> None:
    """A read-set table the target venue does not deploy fails the run.

    A conversion toward a table the target venue does not serve cannot be planned,
    only blocked, so it never ships as a flagged candidate.
    """
    catalog["deployment"] = [r for r in catalog["deployment"] if r["table_id"] != EDW_BENE]
    variables = [variable("SRCLIB.OCS_CLAIMS", "bene_sex_cd")]

    with pytest.raises(CatalogGapError, match="edw") as excinfo:
        resolve(meta, datasets[:1], variables, run_query)

    assert excinfo.value.gaps == [
        {"record_type": "missing_deployment", "table_id": EDW_BENE, "system": "edw"}
    ]


def test_resolve_nowhere_deployed_code_set_is_a_catalog_gap(
    meta: dict[str, Any], datasets: Records, catalog: dict[str, list[Any]], run_query: RunQuery
) -> None:
    """A referenced code set deployed nowhere fails the run with a null venue.

    Its ref_table record exists to make the column's pointer followable, and an
    address-less pointer is not followed.
    """
    catalog["deployment"] = [r for r in catalog["deployment"] if r["table_id"] != "ref.codes.hcpcs_cd"]
    variables = [variable("SRCLIB.OCS_CLAIMS", "hcpcs_cd")]

    with pytest.raises(CatalogGapError, match="deployed nowhere") as excinfo:
        resolve(meta, datasets[:1], variables, run_query)

    assert excinfo.value.gaps == [
        {"record_type": "missing_deployment", "table_id": "ref.codes.hcpcs_cd", "system": None}
    ]


def test_resolve_accumulates_deployment_gaps_on_all_three_sides(
    meta: dict[str, Any], datasets: Records, catalog: dict[str, list[Any]], run_query: RunQuery
) -> None:
    """A source parent, a read-set table, and a nowhere-deployed code set fail together.

    All three sides of the deployment gate accumulate into one raise, so a single run
    names the complete work order.
    """
    gone = {(OCS_CLM, "warehouse"), (EDW_BENE, "edw")}
    catalog["deployment"] = [
        r for r in catalog["deployment"]
        if (r["table_id"], r["system"]) not in gone and r["table_id"] != "ref.codes.hcpcs_cd"
    ]
    variables = [variable("SRCLIB.OCS_CLAIMS", "bene_sex_cd"), variable("SRCLIB.OCS_CLAIMS", "hcpcs_cd")]

    with pytest.raises(CatalogGapError) as excinfo:
        resolve(meta, datasets[:1], variables, run_query)

    assert excinfo.value.gaps == [
        {"record_type": "missing_deployment", "table_id": EDW_BENE, "system": "edw"},
        {"record_type": "missing_deployment", "table_id": OCS_CLM, "system": "warehouse"},
        {"record_type": "missing_deployment", "table_id": "ref.codes.hcpcs_cd", "system": None},
    ]


def test_resolve_published_candidates_carry_no_deployment_flag(
    meta: dict[str, Any], datasets: Records, variables: Records, run_query: RunQuery
) -> None:
    """No per-candidate deployability flag publishes: the gate makes it a constant.

    In a published file every candidate's tables are already known reachable, so the
    flag would always be true, which the payload rule excludes.
    """
    records = resolve(meta, datasets, variables, run_query)

    for record in of_type(records, "origin_sas_variable"):
        for column in record["origin_columns"]:
            for candidate in column["candidates"]:
                assert "deployed_in_dest_system" not in candidate


def test_resolve_system_only_conversion_emits_the_id_matched_pair(
    meta: dict[str, Any], datasets: Records, run_query: RunQuery
) -> None:
    """A system-only conversion pairs each dest table with an origin_table (layout B).

    The pair IS the copy-switch signal: the SAS process read one physical copy and
    the converted code will read the other -- same columns guaranteed by deployment,
    same rows not -- and its two addresses are exactly what a reconciliation task
    compares. No `deployed_venues` field publishes anywhere.
    """
    del meta["dest_data_scope"]
    # The members dataset is narrowed to clm, which is deployed in both systems, so
    # the no-transition dest table stays reachable in edw.
    variables = [variable("SRCLIB.OCS_MEMBERS", "claim_no")]

    records = resolve(meta, datasets, variables, run_query)

    dest = {r["table_id"]: r for r in of_type(records, "dest_table")}
    origin = {r["table_id"]: r for r in of_type(records, "origin_table")}
    # The id-matched pair: one table, two addresses.
    assert set(dest) == set(origin) == {OCS_CLM}
    assert origin[OCS_CLM]["physical_database_name"] == "fixture_ocs_warehouse"
    assert dest[OCS_CLM]["physical_database_name"] == "fixture_ocs"
    assert origin[OCS_CLM]["primary_key_columns"] == dest[OCS_CLM]["primary_key_columns"]
    assert all("deployed_venues" not in r for r in records)


# --- Record order and shape ---


def test_resolve_records_are_grouped_in_write_order(
    meta: dict[str, Any], datasets: Records, variables: Records, run_query: RunQuery
) -> None:
    """Every record_type appears in one contiguous group, in the documented write order."""
    records = resolve(meta, datasets, variables, run_query)

    order = (
        "meta",
        "origin_system", "dest_system",
        "origin_data_source", "dest_data_source",
        "origin_schema", "dest_schema",
        "origin_table", "dest_table", "ref_table",
        "dest_column",
        "origin_join", "dest_join",
        "origin_concept", "dest_concept",
        "origin_sas_dataset", "origin_sas_variable",
    )
    rank = {name: i for i, name in enumerate(order)}
    ranks = [rank[r["record_type"]] for r in records]
    assert ranks == sorted(ranks)


def test_resolve_records_are_sorted_within_each_group(
    meta: dict[str, Any], datasets: Records, variables: Records, run_query: RunQuery
) -> None:
    """Each group of records is sorted on its own identifying key, so the file is stable."""
    records = resolve(meta, datasets, variables, run_query)

    assert [r["dataset"] for r in of_type(records, "origin_sas_dataset")] == ["SRCLIB.OCS_CLAIMS", "SRCLIB.OCS_MEMBERS"]
    assert [r["table_id"] for r in of_type(records, "dest_table")] == sorted(
        r["table_id"] for r in of_type(records, "dest_table")
    )
    assert [r["column_id"] for r in of_type(records, "dest_column")] == sorted(
        r["column_id"] for r in of_type(records, "dest_column")
    )
    assert [(r["dataset"], r["variable"]) for r in of_type(records, "origin_sas_variable")] == [
        ("SRCLIB.OCS_CLAIMS", "bene_sex_cd"),
        ("SRCLIB.OCS_CLAIMS", "claim_no"),
        ("SRCLIB.OCS_CLAIMS", "hcpcs_cd"),
        ("SRCLIB.OCS_MEMBERS", "bene_sex_cd"),
    ]
    for kind in ("origin_concept", "dest_concept", "origin_table"):
        assert [r.get("concept_id") or r["table_id"] for r in of_type(records, kind)] == sorted(
            r.get("concept_id") or r["table_id"] for r in of_type(records, kind)
        )


def test_build_meta_record_writes_absent_coordinates_as_null(meta: dict[str, Any]) -> None:
    """meta carries what was declared, with an absent dest_data_scope stated as null."""
    del meta["dest_data_scope"]

    record = build_meta_record(meta)

    assert record["dest_data_scope"] is None
    assert set(record) == {
        "record_type", "process_name", "origin_system", "dest_system", "origin_data_scope", "dest_data_scope",
    }


def test_build_dataset_records_state_resolved_coordinates(meta: dict[str, Any], datasets: Records) -> None:
    """dataset records state what applied, which may differ from the declared meta."""
    coordinates = {r["dataset"]: resolve_coordinates(meta, r) for r in datasets}

    records = build_dataset_records(datasets, coordinates)

    assert [r["dataset"] for r in records] == ["SRCLIB.OCS_CLAIMS", "SRCLIB.OCS_MEMBERS"]
    assert records[1]["origin_data_scope"] == [OCS_CLM]
    assert records[1]["filepath"] == "data/sas/ocs_members.sas7bdat"


def test_resolve_output_field_sets_are_exact(
    meta: dict[str, Any], datasets: Records, variables: Records, run_query: RunQuery
) -> None:
    """Every record carries exactly the fields the output contract names -- no extras."""
    records = resolve(meta, datasets, variables, run_query)

    column = one_variable(records, "SRCLIB.OCS_CLAIMS", "bene_sex_cd")["origin_columns"][0]
    assert set(column) == {
        "table_id", "origin_column_id", "data_type", "is_nullable", "is_primary_key",
        "ref_table_id", "description", "notes", "mapping_status", "candidates",
    }
    assert set(column["candidates"][0]) == {
        "mapping_name", "target_expression", "target_tables_referenced",
        "use_when", "notes", "validated",
    }
    # All three table forms share one shape: prose, grain, and a physical address --
    # no deployed_venues anywhere (the pair carries the copy-switch fact).
    table_fields = {
        "record_type", "table_id", "description", "notes", "primary_key_columns",
        "physical_database_name", "physical_schema_name", "physical_table_name",
    }
    assert set(of_type(records, "dest_table")[0]) == table_fields
    assert set(of_type(records, "origin_table")[0]) == table_fields
    assert set(of_type(records, "ref_table")[0]) == table_fields
    # A dest column is the origin-column shape minus the mapping machinery.
    assert set(of_type(records, "dest_column")[0]) == {
        "record_type", "table_id", "column_id", "data_type", "is_nullable",
        "is_primary_key", "ref_table_id", "description", "notes",
    }
    assert set(of_type(records, "dest_system")[0]) == {
        "record_type", "system", "description", "notes",
    }
    assert set(of_type(records, "origin_schema")[0]) == {
        "record_type", "schema_id", "description", "notes",
    }
    assert set(of_type(records, "origin_data_source")[0]) == {
        "record_type", "data_source_id", "description", "notes",
    }
    assert set(of_type(records, "origin_join")[0]) == {
        "record_type", "table_a_id", "table_b_id", "relationship_name",
        "join_condition", "cardinality", "use_when", "notes", "validated",
    }
    assert set(of_type(records, "dest_concept")[0]) == {
        "record_type", "concept_id", "label", "definition", "notes", "related_object_ids",
    }
    # Audit columns and anything derivable from an id the record already holds are never
    # carried, whatever the record type.
    banned = {"insert_ts", "update_ts", "validated_ts", "update_reason", "owner", "schema_id"}
    for record in of_type(records, "dest_table") + of_type(records, "origin_table"):
        assert not (set(record) & banned)


# --- Coordinate checking ---


def test_resolve_unresolvable_coordinate_raises_before_resolving(
    meta: dict[str, Any], datasets: Records, variables: Records, client: FakeMCPClient, run_query: RunQuery
) -> None:
    """A typo that extraction cannot catch stops the run before anything is resolved."""
    meta["origin_data_scope"] = ["fixture_typo.general"]

    with pytest.raises(CoordinateError, match="fixture_typo.general"):
        resolve(meta, datasets[:1], variables, run_query)

    assert client.kinds() == ["coordinates"]


def test_resolve_unresolvable_venue_is_reported(
    meta: dict[str, Any], datasets: Records, variables: Records, run_query: RunQuery
) -> None:
    """A venue that is not a systems row is reported as unresolvable."""
    meta["dest_system"] = "nowhere"

    with pytest.raises(CoordinateError, match="system 'nowhere'"):
        resolve(meta, datasets[:1], variables, run_query)


def test_resolve_reports_every_unresolvable_coordinate_at_once(
    meta: dict[str, Any], datasets: Records, variables: Records, run_query: RunQuery
) -> None:
    """All unresolvable coordinates are listed together rather than one per run."""
    meta["dest_system"] = "nowhere"
    meta["dest_data_scope"] = ["fixture_typo"]

    with pytest.raises(CoordinateError) as excinfo:
        resolve(meta, datasets[:1], variables, run_query)

    assert "nowhere" in str(excinfo.value) and "fixture_typo" in str(excinfo.value)


@pytest.mark.parametrize(
    "entry",
    ["Fixture_OCS", "a.b.c.d", "", "fixture;drop", 7, "fixture_ocs\n", "fixture_ocs.general\n"],
)
def test_validate_scope_entry_rejects_malformed_values(entry: Any) -> None:
    """Coordinates are interpolated as SQL literals, so anything malformed is refused.

    The trailing-newline cases are why the segment pattern anchors with \\A and \\Z: `$`
    also matches immediately before a final newline, so 'fixture_ocs\\n' cleared the
    guard and reached the database as a literal, surfacing as a raw driver error
    instead of the CoordinateError the guard exists to raise.
    """
    with pytest.raises(CoordinateError):
        validate_scope_entry("test coordinate", entry)


def test_resolve_malformed_coordinate_raises_before_any_query(
    meta: dict[str, Any], datasets: Records, variables: Records, client: FakeMCPClient, run_query: RunQuery
) -> None:
    """A malformed scope never reaches the database."""
    meta["origin_data_scope"] = ["Fixture_OCS"]

    with pytest.raises(CoordinateError):
        resolve(meta, datasets[:1], variables, run_query)

    assert client.calls == []


def test_resolve_without_origin_data_scope_is_rejected(
    meta: dict[str, Any], datasets: Records, variables: Records, run_query: RunQuery
) -> None:
    """A dataset with no effective origin_data_scope has nothing to resolve variables against."""
    del meta["origin_data_scope"]

    with pytest.raises(CoordinateError, match="no effective origin_data_scope"):
        resolve(meta, datasets[:1], variables, run_query)


@pytest.mark.parametrize("field", ["origin_system", "dest_system"])
def test_resolve_meta_missing_a_system_fails_early(
    meta: dict[str, Any], datasets: Records, variables: Records,
    client: FakeMCPClient, run_query: RunQuery, field: str,
) -> None:
    """A meta missing either system fails Step 1 with a named error, before any query.

    Deployment cannot be resolved against an undeclared system, and every published
    file carries both -- the old tolerance (skip that side of the gate, fail late at
    output validation) had no legitimate use.
    """
    del meta[field]

    with pytest.raises(CoordinateError, match=f"meta {field} is required"):
        resolve(meta, datasets[:1], variables, run_query)

    assert client.calls == []


def test_resolve_empty_scope_list_is_rejected(
    meta: dict[str, Any], datasets: Records, variables: Records, run_query: RunQuery
) -> None:
    """An empty origin_data_scope list cannot resolve anything and is refused."""
    meta["origin_data_scope"] = []

    with pytest.raises(CoordinateError, match="origin_data_scope"):
        resolve(meta, datasets[:1], variables, run_query)


def test_resolve_variable_without_a_dataset_record_raises(
    meta: dict[str, Any], datasets: Records, run_query: RunQuery
) -> None:
    """A variable naming an unknown dataset has no coordinates, so the run stops."""
    variables = [variable("SRCLIB.MISSING", "claim_no")]

    with pytest.raises(InventoryError, match="SRCLIB.MISSING"):
        resolve(meta, datasets[:1], variables, run_query)


# --- Query memoization ---


def test_query_runner_caches_repeated_statements(client: FakeMCPClient) -> None:
    """An identical statement is issued once, so "once per distinct scope" holds."""
    runner = QueryRunner(client, "metadata_db")
    sql = (
        "select column_id::text as column_id from catalog.columns "
        "where column_id operator(catalog.<@) any(array['fixture_ocs.general']::catalog.ltree[])"
    )

    first = runner(sql)
    second = runner(sql)

    assert first == second
    assert len(client.calls) == 1
    assert runner.calls == [sql]


# --- Config, IO, and entry point ---


def write_config(tmp_path: Path, **overrides: Any) -> Path:
    """Write a resolver TOML config for a test.

    Args:
        tmp_path: The test's temporary directory.
        **overrides: Config keys to set or override.

    Returns:
        Path to the written config.
    """
    settings: dict[str, Any] = {
        "input_schema": str(tmp_path / "input_schema.jsonl"),
        "output_dir": str(tmp_path / "output"),
        "overwrite": True,
    }
    settings.update(overrides)
    lines = [
        f"{key} = {json.dumps(value)}" for key, value in settings.items() if value is not None
    ]
    config_path = tmp_path / "resolve_schema.toml"
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config_path


def write_inventory(tmp_path: Path, records: Records) -> Path:
    """Write an inventory JSONL for a test.

    Args:
        tmp_path: The test's temporary directory.
        records: The inventory records.

    Returns:
        Path to the written inventory.
    """
    path = tmp_path / "input_schema.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def test_parse_config_fills_mcp_defaults(tmp_path: Path) -> None:
    """Only the paths and overwrite flag are required; MCP settings default."""
    config = parse_config(write_config(tmp_path))

    assert config["mcp_url"] == "http://localhost:8002/mcp"
    assert config["mcp_timeout_s"] == 60.0
    assert config["mcp_database"] == "metadata_db"
    assert config["mcp_token_env"] == "MCP_METADATA_DB_TOKEN"


def test_parse_config_missing_field_raises(tmp_path: Path) -> None:
    """A config without input_schema raises ValueError, whose str() carries no quotes."""
    config_path = tmp_path / "bad.toml"
    config_path.write_text('output_dir = "output"\noverwrite = true\n', encoding="utf-8")

    with pytest.raises(ValueError, match="Missing required config field: input_schema"):
        parse_config(config_path)


def test_parse_config_invalid_toml_raises(tmp_path: Path) -> None:
    """Unparseable TOML raises rather than being partially applied."""
    config_path = tmp_path / "bad.toml"
    config_path.write_text("this is not toml\n", encoding="utf-8")

    with pytest.raises(tomllib.TOMLDecodeError):
        parse_config(config_path)


def test_parse_config_unreadable_file_raises(tmp_path: Path) -> None:
    """A config path that is a directory raises OSError."""
    with pytest.raises(OSError):
        parse_config(tmp_path)


def test_load_inventory_reads_the_three_record_types(
    tmp_path: Path, meta: dict[str, Any], datasets: Records, variables: Records
) -> None:
    """The inventory splits into its meta, dataset, and variable records."""
    path = write_inventory(tmp_path, [meta, *datasets, *variables, {}])
    # The trailing {} record carries no record_type, so it is ignored rather than raising,
    # and a trailing blank line is skipped rather than parsed.
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    loaded_meta, loaded_datasets, loaded_variables = load_inventory(path)

    assert loaded_meta["process_name"] == "ocs_claims"
    assert len(loaded_datasets) == 2
    assert len(loaded_variables) == len(variables)


def test_load_inventory_missing_file_raises(tmp_path: Path) -> None:
    """A missing inventory raises InventoryError."""
    with pytest.raises(InventoryError, match="not found"):
        load_inventory(tmp_path / "absent.jsonl")


def test_load_inventory_invalid_json_raises(tmp_path: Path) -> None:
    """A malformed line names the line number."""
    path = tmp_path / "input_schema.jsonl"
    path.write_text('{"record_type": "meta"}\nnot json\n', encoding="utf-8")

    with pytest.raises(InventoryError, match="line 2"):
        load_inventory(path)


def test_load_inventory_without_meta_raises(tmp_path: Path, datasets: Records) -> None:
    """An inventory with no meta record has no process-wide defaults to resolve against."""
    with pytest.raises(InventoryError, match="meta record"):
        load_inventory(write_inventory(tmp_path, datasets))


def test_load_inventory_without_datasets_raises(tmp_path: Path, meta: dict[str, Any]) -> None:
    """An inventory with no dataset records has nothing to resolve."""
    with pytest.raises(InventoryError, match="no origin_sas_dataset records"):
        load_inventory(write_inventory(tmp_path, [meta]))


def test_load_inventory_without_variables_raises(
    tmp_path: Path, meta: dict[str, Any], datasets: Records
) -> None:
    """An inventory of empty datasets accounts for nothing, so it never resolves.

    The path is producible: the extractor logs "No variables extracted from any
    dataset" as a WARNING and publishes anyway. Resolved, it would publish a clean,
    validated file that accounts for nothing -- and there is no partial success.
    """
    with pytest.raises(InventoryError, match="no origin_sas_variable records"):
        load_inventory(write_inventory(tmp_path, [meta, *datasets]))


def test_write_jsonl_writes_one_record_per_line(tmp_path: Path) -> None:
    """Records are written as JSONL, creating the output directory."""
    path = tmp_path / "nested" / "input_schema_resolution.jsonl"

    write_jsonl([{"record_type": "meta"}, {"record_type": "origin_sas_dataset"}], path)

    assert [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] == [
        {"record_type": "meta"}, {"record_type": "origin_sas_dataset"},
    ]


def test_log_summary_reports_every_count(
    meta: dict[str, Any], datasets: Records, variables: Records, run_query: RunQuery,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The summary names statuses, ambiguity, tables, joins, scope, and sides.

    It carries no count of variables without an origin column: a published file
    contains none, so the line would be a constant zero.
    """
    records = resolve(meta, datasets, variables, run_query)

    with caplog.at_level("INFO"):
        log_summary(records)

    text = caplog.text
    assert "mapping status" in text and "mapped=" in text and "no_equivalent=" in text
    assert "Ambiguous variables (1)" in text
    assert "no origin column" not in text
    # Dest columns: the two expressions' columns plus the two dest tables' `id` keys.
    assert "Dest tables: 2" in text and "dest columns: 4" in text
    assert "1 origin, 0 dest" in text
    assert "Concepts: 3 origin, 2 dest" in text
    assert "2 data source(s), 2 schema(s), 2 origin table(s)" in text


def test_log_summary_handles_an_empty_resolution(caplog: pytest.LogCaptureFixture) -> None:
    """A summary over no records still logs completely rather than raising."""
    with caplog.at_level("INFO"):
        log_summary([])

    assert "mapping status: none" in caplog.text
    assert "Dest tables: 0" in caplog.text


def _run_main(monkeypatch: pytest.MonkeyPatch, config_path: Path, client: FakeMCPClient) -> None:
    """Invoke main() with a config path and the fake client patched in.

    Args:
        monkeypatch: The pytest monkeypatch fixture.
        config_path: The config to run with.
        client: The fake MCP client main() should use.
    """
    monkeypatch.setattr("sys.argv", ["resolve_schema.py", "--config", str(config_path)])
    monkeypatch.setattr("resolve_schema.MCPClient", lambda *args, **kwargs: client)
    monkeypatch.setenv("MCP_METADATA_DB_TOKEN", "test-token")
    main()


def test_main_writes_the_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: FakeMCPClient,
    meta: dict[str, Any], datasets: Records, variables: Records,
) -> None:
    """An end-to-end run writes {output_dir}/{process_name}/input_schema_resolution.jsonl."""
    write_inventory(tmp_path, [meta, *datasets, *variables])

    _run_main(monkeypatch, write_config(tmp_path), client)

    output = tmp_path / "output" / "ocs_claims" / "input_schema_resolution.jsonl"
    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert records[0]["record_type"] == "meta"
    assert len(records) == len(
        resolve(meta, datasets, variables, lambda sql: client.run_sql("metadata_db", sql))
    )


def test_main_is_byte_identical_across_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: FakeMCPClient,
    meta: dict[str, Any], datasets: Records, variables: Records,
) -> None:
    """The same inventory always produces the same file, byte for byte."""
    write_inventory(tmp_path, [meta, *datasets, *variables])
    config_path = write_config(tmp_path)
    output = tmp_path / "output" / "ocs_claims" / "input_schema_resolution.jsonl"

    _run_main(monkeypatch, config_path, client)
    first = output.read_bytes()
    _run_main(monkeypatch, config_path, client)

    assert output.read_bytes() == first


def test_main_refuses_to_overwrite_when_disallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: FakeMCPClient,
    meta: dict[str, Any], datasets: Records, variables: Records,
) -> None:
    """overwrite = false protects an existing resolution."""
    write_inventory(tmp_path, [meta, *datasets, *variables])
    output = tmp_path / "output" / "ocs_claims" / "input_schema_resolution.jsonl"
    output.parent.mkdir(parents=True)
    output.write_text("existing\n", encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, write_config(tmp_path, overwrite=False), client)

    assert excinfo.value.code == 1
    assert output.read_text(encoding="utf-8") == "existing\n"


def test_main_gap_failure_clears_a_draft_from_an_earlier_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: FakeMCPClient,
    meta: dict[str, Any], datasets: Records, variables: Records,
) -> None:
    """A draft belongs to the run that wrote it, so an earlier one never survives.

    Only the validation step writes a draft, and a gap failure raises before it. Left
    alone, a draft rejected two runs ago would sit beside this run's fresh work order
    and read as this run's rejected output.
    """
    bad = variables + [variable("SRCLIB.OCS_CLAIMS", "derived_flag")]
    write_inventory(tmp_path, [meta, *datasets, *bad])
    process_dir = tmp_path / "output" / "ocs_claims"
    process_dir.mkdir(parents=True)
    stale_draft = process_dir / "input_schema_resolution.jsonl.draft"
    stale_draft.write_text("a draft rejected by an earlier run", encoding="utf-8")

    with pytest.raises(SystemExit):
        _run_main(monkeypatch, write_config(tmp_path), client)

    assert not stale_draft.exists()
    assert (process_dir / "input_schema_catalog_gaps.jsonl").exists()


def test_main_refusing_to_overwrite_leaves_the_directory_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: FakeMCPClient,
    meta: dict[str, Any], datasets: Records, variables: Records,
) -> None:
    """A run that refuses to overwrite mutates nothing, including a leftover draft.

    The startup draft cleanup sits after the overwrite gate for exactly this reason:
    a refusing run has done no work and must leave the process folder as it found it.
    """
    write_inventory(tmp_path, [meta, *datasets, *variables])
    process_dir = tmp_path / "output" / "ocs_claims"
    process_dir.mkdir(parents=True)
    (process_dir / "input_schema_resolution.jsonl").write_text("existing", encoding="utf-8")
    stale_draft = process_dir / "input_schema_resolution.jsonl.draft"
    stale_draft.write_text("untouched", encoding="utf-8")

    with pytest.raises(SystemExit):
        _run_main(monkeypatch, write_config(tmp_path, overwrite=False), client)

    assert stale_draft.read_text(encoding="utf-8") == "untouched"


def test_main_exits_non_zero_on_unresolvable_coordinate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: FakeMCPClient,
    meta: dict[str, Any], datasets: Records, variables: Records,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unresolvable coordinate exits non-zero and names what did not resolve."""
    meta["origin_data_scope"] = ["fixture_typo"]
    write_inventory(tmp_path, [meta, *datasets[:1], *variables])

    with caplog.at_level("ERROR"), pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, write_config(tmp_path), client)

    assert excinfo.value.code == 1
    assert "fixture_typo" in caplog.text
    assert not (tmp_path / "output").exists()


def test_main_exits_non_zero_on_a_missing_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A config path that does not exist exits non-zero."""
    monkeypatch.setattr("sys.argv", ["resolve_schema.py", "--config", str(tmp_path / "absent.toml")])

    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code == 1


def test_main_exits_non_zero_on_a_bad_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: FakeMCPClient
) -> None:
    """An inventory that cannot be loaded exits non-zero."""
    write_inventory(tmp_path, [{"record_type": "origin_sas_dataset", "dataset": "A"}])

    with pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, write_config(tmp_path), client)

    assert excinfo.value.code == 1


def test_main_exits_non_zero_without_a_process_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: FakeMCPClient,
    meta: dict[str, Any], datasets: Records, variables: Records,
) -> None:
    """Without a process_name there is no output path to write to.

    The inventory is otherwise complete -- variables included -- so the run fails on
    the process name rather than on the load gates ahead of it.
    """
    del meta["process_name"]
    write_inventory(tmp_path, [meta, *datasets, *variables])

    with pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, write_config(tmp_path), client)

    assert excinfo.value.code == 1


def test_main_publishes_only_after_validation_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: FakeMCPClient,
    meta: dict[str, Any], datasets: Records, variables: Records,
) -> None:
    """A clean run leaves the resolution at its final path and no draft beside it."""
    write_inventory(tmp_path, [meta, *datasets, *variables])

    _run_main(monkeypatch, write_config(tmp_path), client)

    process_dir = tmp_path / "output" / "ocs_claims"
    assert (process_dir / "input_schema_resolution.jsonl").exists()
    assert list(process_dir.glob("*.draft")) == []


def test_main_exits_non_zero_and_withholds_the_output_when_validation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: FakeMCPClient,
    meta: dict[str, Any], datasets: Records, variables: Records,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed validation keeps the file off its final path, leaving the draft to debug."""
    write_inventory(tmp_path, [meta, *datasets, *variables])
    monkeypatch.setattr(
        "resolve_schema.validate_schema_resolution",
        lambda data, schema=None: ["fabricated failure"],
    )

    with caplog.at_level("ERROR"), pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, write_config(tmp_path), client)

    assert excinfo.value.code == 1
    assert "fabricated failure" in caplog.text
    process_dir = tmp_path / "output" / "ocs_claims"
    assert not (process_dir / "input_schema_resolution.jsonl").exists()
    assert [q.name for q in process_dir.glob("*.draft")] == ["input_schema_resolution.jsonl.draft"]


def test_main_validation_failure_removes_a_stale_work_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: FakeMCPClient,
    meta: dict[str, Any], datasets: Records, variables: Records,
) -> None:
    """A run that reaches validation has passed every catalog gate, so old gaps are fixed.

    Leaving a prior run's work order beside no resolution would present this failure as
    a catalog gap, which it is not -- the catalog accounted for the whole input and the
    output itself was rejected.
    """
    write_inventory(tmp_path, [meta, *datasets, *variables])
    process_dir = tmp_path / "output" / "ocs_claims"
    process_dir.mkdir(parents=True)
    stale = process_dir / "input_schema_catalog_gaps.jsonl"
    stale.write_text("a work order from an earlier run", encoding="utf-8")
    monkeypatch.setattr(
        "resolve_schema.validate_schema_resolution",
        lambda data, schema=None: ["fabricated failure"],
    )

    with pytest.raises(SystemExit):
        _run_main(monkeypatch, write_config(tmp_path), client)

    assert not stale.exists()


def test_main_warns_when_the_token_is_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: FakeMCPClient,
    meta: dict[str, Any], datasets: Records, variables: Records,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A missing token is called out, since the server answers 401 without one."""
    write_inventory(tmp_path, [meta, *datasets, *variables])
    monkeypatch.delenv("MCP_METADATA_DB_TOKEN", raising=False)
    monkeypatch.setattr("sys.argv", ["resolve_schema.py", "--config", str(write_config(tmp_path))])
    monkeypatch.setattr("resolve_schema.MCPClient", lambda *args, **kwargs: client)
    monkeypatch.setattr("resolve_schema.load_dotenv", lambda *args, **kwargs: False)

    with caplog.at_level("WARNING"):
        main()

    assert "401" in caplog.text


def test_main_exits_non_zero_on_an_unexpected_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: FakeMCPClient,
    meta: dict[str, Any], datasets: Records, variables: Records,
) -> None:
    """A transport failure is logged with its traceback and exits non-zero."""
    write_inventory(tmp_path, [meta, *datasets, *variables])

    def explode(database: str, sql: str) -> list[dict[str, Any]]:
        raise RuntimeError("connection lost")

    monkeypatch.setattr(client, "run_sql", explode)

    with pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, write_config(tmp_path), client)

    assert excinfo.value.code == 1


# --- Widened payload and the source/target split ---


def test_resolve_source_column_carries_the_widened_payload(
    meta: dict[str, Any], datasets: Records, run_query: RunQuery
) -> None:
    """Nullability, the code-set pointer, description, and notes reach the output.

    An absent pointer stays null rather than being dropped, so a reader can tell "no code
    set is documented" from "the field was not carried".
    """
    variables = [variable("SRCLIB.OCS_CLAIMS", "hcpcs_cd"), variable("SRCLIB.OCS_CLAIMS", "bene_sex_cd")]

    records = resolve(meta, datasets[:1], variables, run_query)

    coded = one_variable(records, "SRCLIB.OCS_CLAIMS", "hcpcs_cd")["origin_columns"][0]
    assert coded["is_nullable"] is False
    assert coded["ref_table_id"] == "ref.codes.hcpcs_cd"
    assert "Wide occurrence 1 of 45" in coded["notes"]
    assert coded["description"]

    uncoded = one_variable(records, "SRCLIB.OCS_CLAIMS", "bene_sex_cd")["origin_columns"][0]
    assert uncoded["ref_table_id"] is None
    assert uncoded["is_nullable"] is True


def test_resolve_joins_and_tables_carry_their_prose(
    meta: dict[str, Any], datasets: Records, variables: Records,
    catalog: dict[str, list[Any]], run_query: RunQuery,
) -> None:
    """Join notes and table descriptions reach the output -- where grain is stated."""
    catalog["joins"] = [
        relationship(OCS_CLM_LINE, OCS_CLM, notes="Fans out on multi-segment claims."),
        *[j for j in catalog["joins"] if j["table_a_id"] != OCS_CLM_LINE],
    ]

    records = resolve(meta, datasets, variables, run_query)

    assert of_type(records, "origin_join")[0]["notes"] == "Fans out on multi-segment claims."
    parent = {r["table_id"]: r for r in of_type(records, "origin_table")}[OCS_CLM]
    assert parent["description"] == "One row per claim segment; a claim may span several."


def test_resolve_origin_tables_carry_their_own_venue_address(
    meta: dict[str, Any], datasets: Records, variables: Records, run_query: RunQuery
) -> None:
    """A source table is addressed in the SOURCE venue, not measured against the target."""
    records = resolve(meta, datasets, variables, run_query)

    parent = {r["table_id"]: r for r in of_type(records, "origin_table")}[OCS_CLM_LINE]
    # The fake builds warehouse addresses as '<database>_warehouse'.
    assert parent["physical_database_name"] == "fixture_ocs_warehouse"
    assert parent["physical_schema_name"] == "general"
    assert parent["physical_table_name"] == "clm_line"


def test_resolve_origin_table_missing_from_the_origin_system_is_a_catalog_gap(
    meta: dict[str, Any], datasets: Records, variables: Records,
    catalog: dict[str, list[Any]], run_query: RunQuery,
) -> None:
    """A parent table the source venue does not deploy fails the run as a gap.

    The SAS input's tables must exist where the process says it read them; silence
    means a missing deployment row or a wrong coordinate.
    """
    catalog["deployment"] = [
        r for r in catalog["deployment"]
        if not (r["table_id"] == OCS_CLM_LINE and r["system"] == "warehouse")
    ]

    with pytest.raises(CatalogGapError, match="warehouse") as excinfo:
        resolve(meta, datasets, variables, run_query)

    assert excinfo.value.gaps == [
        {"record_type": "missing_deployment", "table_id": OCS_CLM_LINE, "system": "warehouse"}
    ]


def test_resolve_without_a_data_source_change_emits_only_target_forms(
    meta: dict[str, Any], datasets: Records, run_query: RunQuery
) -> None:
    """With no transition every record takes its target form, and none is duplicated.

    The tables the code reads ARE the SAS input's tables, so emitting both forms would
    list each table twice.
    """
    del meta["dest_data_scope"]
    meta["dest_system"] = "warehouse"
    variables = [variable("SRCLIB.OCS_CLAIMS", "claim_no")]

    records = resolve(meta, datasets[:1], variables, run_query)

    assert of_type(records, "origin_system") == []
    assert of_type(records, "origin_table") == []
    assert of_type(records, "origin_schema") == []
    assert of_type(records, "origin_data_source") == []
    assert of_type(records, "origin_concept") == []
    assert [r["table_id"] for r in of_type(records, "dest_table")] == [OCS_CLM, OCS_CLM_LINE]
    assert [r["data_source_id"] for r in of_type(records, "dest_data_source")] == ["fixture_ocs"]


def test_resolve_scope_records_describe_both_sides(
    meta: dict[str, Any], datasets: Records, variables: Records, run_query: RunQuery
) -> None:
    """Schema and database records are emitted once per object, split by side."""
    records = resolve(meta, datasets, variables, run_query)

    assert [r["data_source_id"] for r in of_type(records, "origin_data_source")] == ["fixture_ocs"]
    assert [r["schema_id"] for r in of_type(records, "origin_schema")] == ["fixture_ocs.general"]
    assert [r["data_source_id"] for r in of_type(records, "dest_data_source")] == ["fixture_edw"]
    assert [r["schema_id"] for r in of_type(records, "dest_schema")] == [
        "fixture_edw.claims_vw"
    ]
    assert all(r["description"] for r in of_type(records, "origin_schema"))


def test_origin_table_set_applies_the_pairing_rule() -> None:
    """A parent that is also a dest table pairs exactly when the systems differ.

    Under equal systems it collapses into its dest_table record; a dest table the SAS
    process never read never takes the origin form.
    """
    assert origin_table_set([OCS_CLM], [OCS_CLM], "warehouse", "warehouse") == []
    assert origin_table_set([OCS_CLM], [OCS_CLM], "warehouse", "edw") == [OCS_CLM]
    assert origin_table_set([OCS_CLM], [OCS_CLM, EDW_CLM], "warehouse", "edw") == [OCS_CLM]
    assert origin_table_set([OCS_CLM_LINE], [EDW_CLM], "warehouse", "warehouse") == [OCS_CLM_LINE]


# --- Meta-only venues ---


@pytest.mark.parametrize("field", ["origin_system", "dest_system"])
def test_resolve_dataset_venue_override_raises(
    meta: dict[str, Any], datasets: Records, variables: Records, run_query: RunQuery, field: str
) -> None:
    """A dataset record carrying a venue is rejected: venues are process-wide.

    Deployment is resolved once over the pooled read set, so a per-dataset venue
    could only contradict the one pair the process has by construction.
    """
    datasets[0][field] = "edw"

    with pytest.raises(InventoryError, match=field):
        resolve(meta, datasets, variables, run_query)


def test_resolve_dataset_records_carry_no_venue_fields(
    meta: dict[str, Any], datasets: Records, variables: Records, run_query: RunQuery
) -> None:
    """Output dataset records carry the data scopes alone; the venues live on meta.

    A dataset venue would be trivially derivable from the header, which the payload
    rule excludes.
    """
    records = resolve(meta, datasets, variables, run_query)

    for record in of_type(records, "origin_sas_dataset"):
        assert set(record) == {"record_type", "dataset", "filepath", "origin_data_scope", "dest_data_scope"}
    meta_record = records[0]
    assert (meta_record["origin_system"], meta_record["dest_system"]) == ("warehouse", "edw")


# --- Target columns ---


def test_resolve_emits_dest_columns_for_expressions_and_dest_joins(
    meta: dict[str, Any], datasets: Records, run_query: RunQuery
) -> None:
    """Every column the emitted code reads gets a dest_column record.

    That covers the surviving expressions' columns and the target joins' condition
    columns -- including a join-condition column no expression mentions -- each with
    type, nullability, prose, and the code-set pointer, at the same grain as the
    source side.
    """
    variables = [variable("SRCLIB.OCS_CLAIMS", "bene_sex_cd"), variable("SRCLIB.OCS_CLAIMS", "clm_from_dt")]

    records = resolve(meta, datasets[:1], variables, run_query)

    # The read set {bene, clm} carries a relationship, so its join condition's `id`
    # columns are referenced although no expression mentions them.
    assert [(r["table_a_id"], r["table_b_id"]) for r in of_type(records, "dest_join")] == [
        (EDW_CLM, EDW_BENE)
    ]
    columns = {r["column_id"]: r for r in of_type(records, "dest_column")}
    assert sorted(columns) == [
        f"{EDW_BENE}.bene_sex_cd", f"{EDW_BENE}.id", f"{EDW_CLM}.clm_from_dt", f"{EDW_CLM}.id",
    ]

    coded = columns[f"{EDW_BENE}.bene_sex_cd"]
    assert coded["data_type"] == "char(1)"
    assert coded["is_nullable"] is True
    assert coded["description"]
    assert coded["ref_table_id"] == "ref.codes.bene_sex_cd"
    join_column = columns[f"{EDW_CLM}.id"]
    assert join_column["is_primary_key"] is True
    assert columns[f"{EDW_CLM}.clm_from_dt"]["is_nullable"] is False

    # A target-side code-set pointer resolves like a source-side one: the code set
    # gets its own ref_table record with a followable address.
    ref_ids = [r["table_id"] for r in of_type(records, "ref_table")]
    assert "ref.codes.bene_sex_cd" in ref_ids


def test_dest_columns_referenced_ignores_a_suffix_match_inside_a_longer_identifier() -> None:
    """A table id embedded as the dotted suffix of a longer identifier is not a reference.

    Without a left boundary the known id would match mid-identifier and yield a
    phantom dest column id from a table the expression never reads.
    """
    expression = (
        "select old_fixture_edw.general.clm.claim_no, fixture_edw.general.clm.person_key from t"
    )

    found = dest_columns_referenced(expression, ["fixture_edw.general.clm"])

    assert found == {"fixture_edw.general.clm.person_key"}


# --- The catalog-gaps work order ---


def test_main_gap_failure_writes_the_work_order_and_no_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: FakeMCPClient,
    meta: dict[str, Any], datasets: Records, variables: Records,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A gap failure writes input_schema_catalog_gaps.jsonl and never a resolution or draft.

    The work order is data, not log prose, and its differently named file cannot be
    mistaken for a publishable output.
    """
    bad = variables + [variable("SRCLIB.OCS_CLAIMS", "derived_flag")]
    write_inventory(tmp_path, [meta, *datasets, *bad])
    config_path = write_config(tmp_path)
    process_dir = tmp_path / "output" / "ocs_claims"

    with caplog.at_level("ERROR"), pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, config_path, client)

    assert excinfo.value.code == 1
    assert "CATALOG GAP: variable 'SRCLIB.OCS_CLAIMS.derived_flag'" in caplog.text
    assert "1 missing variable(s)" in caplog.text
    gaps_path = process_dir / "input_schema_catalog_gaps.jsonl"
    gaps = [json.loads(line) for line in gaps_path.read_text(encoding="utf-8").splitlines()]
    assert gaps == [{
        "record_type": "missing_variable",
        "origin_sas_dataset": "SRCLIB.OCS_CLAIMS",
        "origin_sas_variable": "derived_flag",
        "origin_data_scope": ["fixture_ocs.general"],
    }]
    assert not (process_dir / "input_schema_resolution.jsonl").exists()
    assert list(process_dir.glob("*.draft")) == []


def test_main_multi_gap_work_order_passes_its_own_validator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: FakeMCPClient,
    catalog: dict[str, list[Any]],
    meta: dict[str, Any], datasets: Records, variables: Records,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A work order carrying both gap kinds satisfies the validator that checks it.

    The two artifacts are checked against each other, with `validate_catalog_gaps`
    unpatched: the work order has exactly one legal serialization -- grouped by type,
    sorted within a group -- so a resolver whose write order drifted from the
    documented one fails here rather than in a consumer's diff.
    """
    catalog["mappings"] = [
        m for m in catalog["mappings"] if m["source_column_id"] != f"{OCS_CLM}.bene_sex_cd"
    ]
    bad = variables + [
        variable("SRCLIB.OCS_CLAIMS", "derived_flag"), variable("SRCLIB.OCS_MEMBERS", "invented_cd"),
    ]
    write_inventory(tmp_path, [meta, *datasets, *bad])

    with caplog.at_level("ERROR"), pytest.raises(SystemExit):
        _run_main(monkeypatch, write_config(tmp_path), client)

    assert "VALIDATION FAILED" not in caplog.text
    gaps_path = tmp_path / "output" / "ocs_claims" / "input_schema_catalog_gaps.jsonl"
    gaps = [json.loads(line) for line in gaps_path.read_text(encoding="utf-8").splitlines()]
    assert [g["record_type"] for g in gaps] == [
        "missing_variable", "missing_variable", "missing_candidate", "missing_candidate",
    ]


def test_main_successful_rerun_removes_a_stale_work_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: FakeMCPClient,
    meta: dict[str, Any], datasets: Records, variables: Records,
) -> None:
    """A run that succeeds after a gap failure publishes and removes the work order.

    The mirror of the gap failure's cleanup: a stale work order outliving its fix
    would send its reader to close gaps the catalog no longer has.
    """
    write_inventory(
        tmp_path, [meta, *datasets, *variables, variable("SRCLIB.OCS_CLAIMS", "derived_flag")]
    )
    config_path = write_config(tmp_path)
    process_dir = tmp_path / "output" / "ocs_claims"
    gaps_path = process_dir / "input_schema_catalog_gaps.jsonl"
    with pytest.raises(SystemExit):
        _run_main(monkeypatch, config_path, client)
    assert gaps_path.exists()

    write_inventory(tmp_path, [meta, *datasets, *variables])
    _run_main(monkeypatch, config_path, client)

    assert (process_dir / "input_schema_resolution.jsonl").exists()
    assert not gaps_path.exists()


def test_main_gap_failure_removes_a_prior_runs_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: FakeMCPClient,
    meta: dict[str, Any], datasets: Records, variables: Records,
) -> None:
    """A gap failure removes the resolution an earlier successful run published.

    The two artifacts are mutually exclusive: leaving the old resolution beside a
    fresh work order would let sas-conversion-planning read an outdated file as
    current.
    """
    write_inventory(tmp_path, [meta, *datasets, *variables])
    config_path = write_config(tmp_path)
    output_path = tmp_path / "output" / "ocs_claims" / "input_schema_resolution.jsonl"
    _run_main(monkeypatch, config_path, client)
    assert output_path.exists()

    write_inventory(
        tmp_path, [meta, *datasets, *variables, variable("SRCLIB.OCS_CLAIMS", "derived_flag")]
    )
    with pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, config_path, client)

    assert excinfo.value.code == 1
    assert not output_path.exists()
    assert (tmp_path / "output" / "ocs_claims" / "input_schema_catalog_gaps.jsonl").exists()


def test_main_deployment_gap_failure_writes_missing_deployment_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: FakeMCPClient,
    catalog: dict[str, list[Any]],
    meta: dict[str, Any], datasets: Records, variables: Records,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A deployment gap writes its missing_deployment records to the same work order."""
    catalog["deployment"] = [r for r in catalog["deployment"] if r["table_id"] != EDW_BENE]
    write_inventory(tmp_path, [meta, *datasets, *variables])

    with caplog.at_level("ERROR"), pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, write_config(tmp_path), client)

    assert excinfo.value.code == 1
    assert "1 missing deployment(s)" in caplog.text
    gaps_path = tmp_path / "output" / "ocs_claims" / "input_schema_catalog_gaps.jsonl"
    gaps = [json.loads(line) for line in gaps_path.read_text(encoding="utf-8").splitlines()]
    assert gaps == [
        {"record_type": "missing_deployment", "table_id": EDW_BENE, "system": "edw"}
    ]


# --- System records ---


def test_resolve_emits_system_records_per_the_collapse_rule(
    meta: dict[str, Any], datasets: Records, variables: Records, run_query: RunQuery
) -> None:
    """dest_system always publishes; origin_system exactly when the systems differ.

    Both carry the catalog prose, origin before dest, right after meta -- the
    systems are coordinates of every conversion, so their meaning travels with the
    file rather than living only in skill documentation.
    """
    records = resolve(meta, datasets, variables, run_query)

    assert [(r["record_type"], r["system"]) for r in records[1:3]] == [
        ("origin_system", "warehouse"), ("dest_system", "edw"),
    ]
    assert all(r["description"] for r in records[1:3])


def test_resolve_system_records_carry_the_prose_the_catalog_holds(
    meta: dict[str, Any], datasets: Records, variables: Records,
    catalog: dict[str, list[Any]], run_query: RunQuery,
) -> None:
    """The system records publish the catalog's own prose, and null where it has none.

    The fixture synthesizes a default row per declared system, so supplying the rows
    explicitly is what proves the prose travels from the catalog rather than from the
    system name -- and a declared system the catalog documents nowhere still publishes
    its record, with the prose stated as null.
    """
    catalog["system_prose"] = [
        {"system": "edw", "description": "The enterprise data warehouse.", "notes": "Catalog-native."},
    ]

    records = resolve(meta, datasets, variables, run_query)

    prose = {r["record_type"]: (r["description"], r["notes"]) for r in records[1:3]}
    assert prose == {
        "origin_system": (None, None),
        "dest_system": ("The enterprise data warehouse.", "Catalog-native."),
    }


def test_resolve_equal_systems_publish_only_the_dest_system_record(
    meta: dict[str, Any], datasets: Records, run_query: RunQuery
) -> None:
    """With equal systems the origin_system record collapses away."""
    meta["dest_system"] = "warehouse"
    del meta["dest_data_scope"]
    variables = [variable("SRCLIB.OCS_CLAIMS", "claim_no")]

    records = resolve(meta, datasets[:1], variables, run_query)

    assert of_type(records, "origin_system") == []
    assert [r["system"] for r in of_type(records, "dest_system")] == ["warehouse"]


# --- primary_key_columns and the grain fold ---


def test_resolve_table_records_carry_sorted_primary_key_leaf_names(
    meta: dict[str, Any], datasets: Records, variables: Records, run_query: RunQuery
) -> None:
    """Every table record carries its grain as sorted leaf names; unflagged is [].

    Leaf names, not full ids -- the record's table_id supplies the prefix. The code
    sets carry no flagged columns in the fixture catalog, so they publish the empty
    list honestly rather than failing the run.
    """
    records = resolve(meta, datasets, variables, run_query)

    dest = {r["table_id"]: r for r in of_type(records, "dest_table")}
    assert dest[EDW_BENE]["primary_key_columns"] == ["id"]
    origin = {r["table_id"]: r for r in of_type(records, "origin_table")}
    assert origin[OCS_CLM]["primary_key_columns"] == ["claim_no", "person_key"]
    assert origin[OCS_CLM_LINE]["primary_key_columns"] == ["claim_no", "lineitem", "person_key"]
    for record in of_type(records, "ref_table"):
        assert record["primary_key_columns"] == []


def test_resolve_primary_key_query_reissues_for_code_sets_the_dest_columns_reveal(
    meta: dict[str, Any], datasets: Records, client: FakeMCPClient, run_query: RunQuery
) -> None:
    """A code set only a dest column points at arrives after the first key fetch.

    The tables in play are not complete until the dest-column fetch reveals its
    ref_table_id, so the primary-key query re-issues once over the late arrivals
    (queries table row 9's "plus once more").
    """
    # bene_sex_cd's origin column carries no pointer; its dest column points at
    # ref.codes.bene_sex_cd, which nothing else references.
    variables = [variable("SRCLIB.OCS_CLAIMS", "bene_sex_cd")]

    resolve(meta, datasets[:1], variables, run_query)

    key_queries = [sql for kind, sql in client.calls if kind == "primary_keys"]
    assert len(key_queries) == 2
    assert "ref.codes.bene_sex_cd" not in key_queries[0]
    assert "ref.codes.bene_sex_cd" in key_queries[1]


def test_resolve_dest_table_keys_gain_dest_column_records(
    meta: dict[str, Any], datasets: Records, run_query: RunQuery
) -> None:
    """A dest table's flagged keys are read columns, even when nothing references them.

    Grain columns get read in practice -- join keys, partition filters, GROUP BY --
    so they join the dest-column collection. The keys of transition-case SAS parents
    and of code sets stay out: for those tables grain is metadata, not something the
    emitted code reads.
    """
    variables = [variable("SRCLIB.OCS_CLAIMS", "bene_sex_cd")]

    records = resolve(meta, datasets[:1], variables, run_query)

    columns = {r["column_id"]: r for r in of_type(records, "dest_column")}
    assert sorted(columns) == [f"{EDW_BENE}.bene_sex_cd", f"{EDW_BENE}.id"]
    assert columns[f"{EDW_BENE}.id"]["is_primary_key"] is True
    # The SAS parent's keys earned no dest_column record.
    assert f"{OCS_CLM}.claim_no" not in columns


# --- Concept side and scope boundaries ---


def test_resolve_concepts_on_parent_keys_and_code_set_keys_stay_out(
    meta: dict[str, Any], datasets: Records, catalog: dict[str, list[Any]], run_query: RunQuery
) -> None:
    """A concept anchored to an untouched key column never enters the file.

    A transition-case parent's key column no variable matched, and a code set's own
    key column, are not objects in play -- grain widens the scope only through the
    dest tables, whose keys are dest columns. The code set's table-anchored concept
    still arrives, as a dest_concept (the anchor is not origin-only).
    """
    catalog["concepts"] += [
        concept(f"{OCS_CLM}.person_key.concept.link_key", []),
        concept("ref.codes.hcpcs_cd.hcpcs_cd.concept.code_meaning", []),
        concept("ref.codes.hcpcs_cd.concept.hcpcs", []),
    ]
    variables = [variable("SRCLIB.OCS_CLAIMS", "hcpcs_cd")]

    records = resolve(meta, datasets[:1], variables, run_query)

    every_id = [r.get("concept_id") for r in records if "concept_id" in r]
    assert f"{OCS_CLM}.person_key.concept.link_key" not in every_id
    assert "ref.codes.hcpcs_cd.hcpcs_cd.concept.code_meaning" not in every_id
    assert "ref.codes.hcpcs_cd.concept.hcpcs" in [
        r["concept_id"] for r in of_type(records, "dest_concept")
    ]


# --- Deferred table prose ---


def test_resolve_deployment_gap_failure_spares_table_prose_and_concepts(
    meta: dict[str, Any], datasets: Records, catalog: dict[str, list[Any]], run_query: RunQuery,
    client: FakeMCPClient,
) -> None:
    """A deployment-gap failure never spends the table-prose or concepts queries.

    Table prose (queries table row 12) is deferred past the deployment gate, and the
    concepts query follows it, so only rows 1-11 are spent on a run that fails there.
    """
    catalog["deployment"] = [r for r in catalog["deployment"] if r["table_id"] != EDW_BENE]
    variables = [variable("SRCLIB.OCS_CLAIMS", "bene_sex_cd")]

    with pytest.raises(CatalogGapError):
        resolve(meta, datasets[:1], variables, run_query)

    assert "tables" not in client.kinds()
    assert "concepts" not in client.kinds()
    assert "deployment" in client.kinds()


# --- Inventory-filtered fetches and the variable-name guard ---


def test_partition_variable_names_pools_the_union_across_datasets_sharing_a_scope() -> None:
    """Datasets sharing an origin_data_scope contribute their names to one pooled list."""
    coordinates = {
        "SRCLIB.A": {"origin_data_scope": ["fixture_ocs.general"], "dest_data_scope": None},
        "SRCLIB.B": {"origin_data_scope": ["fixture_ocs.general"], "dest_data_scope": None},
    }
    variables = [variable("SRCLIB.A", "bene_sex_cd"), variable("SRCLIB.B", "clm_from_dt")]

    pooled, excluded = partition_variable_names(variables, coordinates)

    assert pooled == {("fixture_ocs.general",): ["bene_sex_cd", "clm_from_dt"]}
    assert excluded == []


def test_partition_variable_names_lowercases_deduplicates_and_sorts() -> None:
    """The pooled list is byte-stable: one entry per distinct name, lowercased and sorted."""
    coordinates = {"SRCLIB.A": {"origin_data_scope": ["fixture_ocs.general"], "dest_data_scope": None}}
    variables = [
        variable("SRCLIB.A", "PERSON_KEY"),
        variable("SRCLIB.A", "person_key"),
        variable("SRCLIB.A", "claim_no"),
    ]

    pooled, _ = partition_variable_names(variables, coordinates)

    assert pooled == {("fixture_ocs.general",): ["claim_no", "person_key"]}


@pytest.mark.parametrize(
    "name",
    ["odd name", "quote'name", "semi;colon", "star*", "dotted.name", "", "claim_no\n"],
)
def test_partition_variable_names_excludes_a_name_outside_the_id_charset(name: str) -> None:
    """A name a catalog column could never carry is excluded rather than interpolated.

    The trailing-newline case is the anchoring one: an unanchored `$` admitted
    'claim_no\\n' to the columns predicate the docstring says only charset-clean names
    enter.
    """
    coordinates = {"SRCLIB.A": {"origin_data_scope": ["fixture_ocs.general"], "dest_data_scope": None}}

    pooled, excluded = partition_variable_names([variable("SRCLIB.A", name)], coordinates)

    assert pooled == {}
    assert excluded == [name]


def test_partition_variable_names_skips_a_variable_with_no_dataset_record() -> None:
    """An orphaned variable contributes nothing; the matching loop is what raises on it."""
    coordinates = {"SRCLIB.A": {"origin_data_scope": ["fixture_ocs.general"], "dest_data_scope": None}}

    pooled, excluded = partition_variable_names([variable("SRCLIB.GHOST", "claim_no")], coordinates)

    assert pooled == {}
    assert excluded == []


def test_resolve_columns_query_is_filtered_to_the_inventorys_names(
    meta: dict[str, Any], datasets: Records, client: FakeMCPClient, run_query: RunQuery
) -> None:
    """The columns fetch asks only about the names the inventory carries.

    The scope holds nine columns; an inventory of one name must not pull the other
    eight, which is what keeps the result inside the MCP server's row cap on a real
    table thousands of columns wide.
    """
    records = resolve(meta, datasets[:1], [variable("SRCLIB.OCS_CLAIMS", "bene_sex_cd")], run_query)

    columns_sql = next(sql for kind, sql in client.calls if kind == "columns")
    assert "'bene_sex_cd'" in columns_sql
    assert "'claim_no'" not in columns_sql
    assert "'hcpcs_cd'" not in columns_sql
    resolved = one_variable(records, "SRCLIB.OCS_CLAIMS", "bene_sex_cd")["origin_columns"]
    assert [c["origin_column_id"] for c in resolved] == [f"{OCS_CLM}.bene_sex_cd"]


def test_resolve_pooled_names_produce_one_columns_query(
    meta: dict[str, Any], client: FakeMCPClient, run_query: RunQuery
) -> None:
    """Two datasets sharing a scope share one columns query carrying the union of their names.

    Pooling is safe because the query only builds the search space: match_origin_columns
    still decides per variable, so a name pooled in on the other dataset's behalf cannot
    appear in this one's resolution.
    """
    datasets = [
        {"record_type": "origin_sas_dataset", "dataset": "SRCLIB.A", "filepath": "a.sas7bdat"},
        {"record_type": "origin_sas_dataset", "dataset": "SRCLIB.B", "filepath": "b.sas7bdat"},
    ]
    variables = [variable("SRCLIB.A", "bene_sex_cd"), variable("SRCLIB.B", "clm_from_dt")]

    records = resolve(meta, datasets, variables, run_query)

    assert client.kinds().count("columns") == 1
    columns_sql = next(sql for kind, sql in client.calls if kind == "columns")
    assert "'bene_sex_cd'" in columns_sql and "'clm_from_dt'" in columns_sql
    assert [
        c["origin_column_id"] for c in one_variable(records, "SRCLIB.A", "bene_sex_cd")["origin_columns"]
    ] == [f"{OCS_CLM}.bene_sex_cd"]
    assert [
        c["origin_column_id"] for c in one_variable(records, "SRCLIB.B", "clm_from_dt")["origin_columns"]
    ] == [f"{OCS_CLM}.clm_from_dt"]


def test_resolve_ineligible_variable_name_never_reaches_sql(
    meta: dict[str, Any], datasets: Records, client: FakeMCPClient, run_query: RunQuery,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A name outside the catalog id charset is kept out of every statement, and logged.

    The two artifacts the exclusion produces -- the SQL and the gap record -- are
    deliberately indistinguishable from an ordinary zero-match run, so the warning is
    the only observable that separates the guard from missing catalog documentation.
    """
    hostile = "x'; drop table catalog.columns; --"
    variables = [variable("SRCLIB.OCS_CLAIMS", "bene_sex_cd"), variable("SRCLIB.OCS_CLAIMS", hostile)]

    with caplog.at_level("WARNING"), pytest.raises(CatalogGapError):
        resolve(meta, datasets[:1], variables, run_query)

    assert client.calls, "the eligible name should still have produced a columns query"
    for _, sql in client.calls:
        assert "drop table" not in sql
    assert "cannot enter a SQL predicate" in caplog.text
    assert hostile in caplog.text


def test_resolve_ineligible_variable_name_gaps_like_an_unmatched_one(
    meta: dict[str, Any], datasets: Records, run_query: RunQuery
) -> None:
    """The guard changes no outcome: an excluded name gaps exactly as an unmatched name does.

    Excluding the name from the predicate resolves it to no origin column, which is the
    ordinary zero-match path -- so the work order records it identically to a name that
    reached the catalog and found nothing.
    """
    with pytest.raises(CatalogGapError) as excluded_run:
        resolve(meta, datasets[:1], [variable("SRCLIB.OCS_CLAIMS", "odd name")], run_query)
    with pytest.raises(CatalogGapError) as unmatched_run:
        resolve(meta, datasets[:1], [variable("SRCLIB.OCS_CLAIMS", "derived_flag")], run_query)

    excluded_gap = excluded_run.value.gaps[0]
    unmatched_gap = unmatched_run.value.gaps[0]
    assert excluded_gap == {**unmatched_gap, "origin_sas_variable": "odd name"}


def test_resolve_columns_query_is_skipped_when_no_name_is_eligible(
    meta: dict[str, Any], datasets: Records, client: FakeMCPClient, run_query: RunQuery
) -> None:
    """Nothing can match, so no columns query is issued at all."""
    with pytest.raises(CatalogGapError):
        resolve(meta, datasets[:1], [variable("SRCLIB.OCS_CLAIMS", "odd name")], run_query)

    assert "columns" not in client.kinds()


def test_resolve_mappings_fetch_is_one_exact_id_query_over_matched_columns(
    meta: dict[str, Any], datasets: Records, client: FakeMCPClient, run_query: RunQuery
) -> None:
    """The mappings fetch names the matched columns, not their scope."""
    resolve(meta, datasets[:1], [variable("SRCLIB.OCS_CLAIMS", "bene_sex_cd")], run_query)

    assert client.kinds().count("mappings") == 1
    mappings_sql = next(sql for kind, sql in client.calls if kind == "mappings")
    assert f"'{OCS_CLM}.bene_sex_cd'" in mappings_sql
    assert f"'{OCS_CLM}.claim_no'" not in mappings_sql
    assert "operator(catalog.<@)" not in mappings_sql


def test_resolve_pools_the_mappings_fetch_across_scopes(
    meta: dict[str, Any], client: FakeMCPClient, run_query: RunQuery
) -> None:
    """Two scopes need two columns queries but only one mappings query.

    A column id is globally unique, so once matching has resolved the columns the scope
    adds nothing to the mappings fetch and every transitioning dataset pools into one.
    """
    datasets = [
        {"record_type": "origin_sas_dataset", "dataset": "SRCLIB.A", "filepath": "a.sas7bdat"},
        {
            "record_type": "origin_sas_dataset", "dataset": "SRCLIB.B", "filepath": "b.sas7bdat",
            "origin_data_scope": [OCS_CLM_LINE],
        },
    ]
    variables = [variable("SRCLIB.A", "bene_sex_cd"), variable("SRCLIB.B", "hcpcs_cd")]

    resolve(meta, datasets, variables, run_query)

    assert client.kinds().count("columns") == 2
    assert client.kinds().count("mappings") == 1
    mappings_sql = next(sql for kind, sql in client.calls if kind == "mappings")
    assert f"'{OCS_CLM}.bene_sex_cd'" in mappings_sql
    assert f"'{OCS_CLM_LINE}.hcpcs_cd'" in mappings_sql


def test_resolve_mappings_query_is_skipped_when_nothing_matched(
    meta: dict[str, Any], datasets: Records, client: FakeMCPClient, run_query: RunQuery
) -> None:
    """A run headed for a missing-variable failure spends nothing on mappings.

    The fetch is keyed on the matched columns, so with no match there is nothing to ask
    about -- one query fewer than a scope-keyed fetch would have spent.
    """
    with pytest.raises(CatalogGapError):
        resolve(meta, datasets[:1], [variable("SRCLIB.OCS_CLAIMS", "derived_flag")], run_query)

    assert "mappings" not in client.kinds()


# --- Ambiguous deployment and work-order write failures ---


def test_resolve_code_set_deployed_to_several_systems_warns_and_picks_deterministically(
    meta: dict[str, Any], datasets: Records, catalog: dict[str, list[Any]],
    run_query: RunQuery, caplog: pytest.LogCaptureFixture,
) -> None:
    """A code set is meant to have one instance; a second one resolves but says so.

    The address has to be deterministic for reruns to stay byte-identical, so the
    systems are sorted and the first taken -- and the warning is what keeps that
    silent pick from passing as a documented fact.
    """
    catalog["deployment"].append(deployment("ref.codes.hcpcs_cd", "warehouse"))

    with caplog.at_level("WARNING"):
        records = resolve(meta, datasets[:1], [variable("SRCLIB.OCS_CLAIMS", "hcpcs_cd")], run_query)

    assert "is deployed to 2 systems" in caplog.text
    ref_table = next(r for r in records if r["record_type"] == "ref_table")
    # 'metadata_db' sorts before 'warehouse', so the metadata_db address is the one taken.
    assert ref_table["physical_database_name"] == "ref_metadata_db"


def test_main_unwritable_work_order_exits_without_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: FakeMCPClient,
    meta: dict[str, Any], datasets: Records, variables: Records,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A gap failure whose work order cannot be written still exits 1, reporting why.

    The write sits inside the CatalogGapError handler, so an OSError raised there would
    escape the statement's other handlers and surface as a raw traceback.
    """
    bad = variables + [variable("SRCLIB.OCS_CLAIMS", "derived_flag")]
    write_inventory(tmp_path, [meta, *datasets, *bad])
    config_path = write_config(tmp_path)

    def refuse(records: Records, output_path: Path) -> None:
        """Stand in for write_jsonl, failing the way a read-only directory would.

        Args:
            records: Ignored.
            output_path: Ignored.

        Raises:
            OSError: Always.
        """
        raise OSError("read-only file system")

    monkeypatch.setattr("resolve_schema.write_jsonl", refuse)

    with caplog.at_level("ERROR"), pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, config_path, client)

    assert excinfo.value.code == 1
    assert "Failed to write the catalog work order" in caplog.text


def test_main_invalid_work_order_is_reported_with_the_gaps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: FakeMCPClient,
    meta: dict[str, Any], datasets: Records, variables: Records,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The work order is validated as it is written, and a failure is logged beside the gaps.

    Other tooling consumes this file, so a malformed one must announce itself rather
    than silently misdirect the catalog fix it exists to order.
    """
    bad = variables + [variable("SRCLIB.OCS_CLAIMS", "derived_flag")]
    write_inventory(tmp_path, [meta, *datasets, *bad])
    config_path = write_config(tmp_path)
    monkeypatch.setattr(
        "resolve_schema.validate_catalog_gaps", lambda path: ["work order is malformed"]
    )

    with caplog.at_level("ERROR"), pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, config_path, client)

    assert excinfo.value.code == 1
    assert "VALIDATION FAILED: work order is malformed" in caplog.text
    assert "CATALOG GAP: variable 'SRCLIB.OCS_CLAIMS.derived_flag'" in caplog.text


# --- The fake client's own guard ---


def test_fake_client_refuses_a_statement_it_cannot_classify(catalog: dict[str, list[Any]]) -> None:
    """An unclassifiable statement fails loudly instead of being answered by a branch.

    The guard is what makes every other test in this file trustworthy: a resolver change
    that alters a statement past the fake's branches has to surface as an error here,
    not as an empty result from whichever branch still happens to match.
    """
    with pytest.raises(AssertionError, match="Unclassifiable query"):
        FakeMCPClient(catalog).run_sql("metadata_db", "select 1")
