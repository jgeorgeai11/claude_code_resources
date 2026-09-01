"""Tests for the resolution output checks.

The checks are a second, independent implementation of the resolver's rules: the
resolver decides which records to emit, and these checks decide which it was
allowed to emit. Testing them against the resolver's fixtures would defeat that, so
these run against the committed example resolution -- a real run against the live
catalog -- and against copies of it mutated one field at a time.
"""

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data_validation"))

import data_val_schema_resolution
from data_val_schema_resolution import main as validator_main
from data_val_schema_resolution import validate_schema_resolution

EXAMPLE = (
    Path(__file__).resolve().parents[2] / "references" / "sas_data_resolution_example.jsonl"
)

# A table in a schema the conversion reads, and a column on a table it reads, that no
# translation in the example resolution touches.
UNREAD_TABLE = "edwc_prd.claims_vw_prd.v_bene"
UNREAD_COLUMN = "edwc_prd.claims_vw_prd.v_clm.clm_phase_cd"

# The id a record type sorts on, most specific first: a dest_column carries both a
# column_id and a table_id, and sorts on the former.
SORT_ID_FIELDS = ("concept_id", "column_id", "table_id")


@pytest.fixture
def records() -> list[dict[str, Any]]:
    """The committed example resolution, parsed.

    Returns:
        Its records, in file order.
    """
    lines = EXAMPLE.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _write(
    tmp_path: Path,
    records: list[dict[str, Any]],
    extra: dict[str, Any] | None = None,
) -> Path:
    """Write records to a JSONL file for validation, adding one in sorted position.

    The checks require each record type to be sorted, so an added record goes into
    its type's block rather than at the end -- otherwise every test fails on ordering
    before reaching the rule it is about.

    Args:
        tmp_path: The pytest temporary directory.
        records: The records to write.
        extra: A record to add, or None.

    Returns:
        The path written.
    """
    if extra is not None:
        kind = extra["record_type"]
        id_field = next(field for field in SORT_ID_FIELDS if field in extra)
        block = sorted(
            [r for r in records if r["record_type"] == kind] + [extra],
            key=lambda r: r[id_field],
        )
        # Splice the rebuilt block back where the group sits, so the write order holds.
        index = records.index(next(r for r in records if r["record_type"] == kind))
        records = [r for r in records if r["record_type"] != kind]
        records[index:index] = block
    path = tmp_path / "resolution.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def _concept(namespace: str) -> dict[str, Any]:
    """Build a well-formed dest_concept record anchored at a namespace.

    Args:
        namespace: The object the concept anchors to.

    Returns:
        The record.
    """
    return {
        "record_type": "dest_concept",
        "concept_id": f"{namespace}.concept.invented",
        "label": "Invented",
        "definition": "A concept anchored somewhere the conversion does not read.",
        "notes": None,
        "related_object_ids": [],
    }


def test_committed_example_validates(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    """The published example passes unchanged.

    This is the accept direction of the whole contract against real data: the example
    carries a column-anchored concept reachable only by deriving dest columns from
    the translation expressions, so a broken derivation fails here.
    """
    path = _write(tmp_path, records)

    errors = validate_schema_resolution(path)

    assert errors == []


@pytest.mark.parametrize(
    ("namespace", "depth"),
    [(UNREAD_TABLE, "table"), (UNREAD_COLUMN, "column")],
)
def test_concept_on_an_untouched_object_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]], namespace: str, depth: str
) -> None:
    """A concept inside an in-play schema is still rejected when nothing reads it.

    Both anchors sit under a schema the conversion reads, so a check that accepted
    anything beneath an in-scope schema would pass them. That is the rule these tests
    exist to prevent regressing to: the EDW claim views are hundreds of columns wide,
    and accepting the subtree would admit a concept for every one of them.
    """
    errors = validate_schema_resolution(_write(tmp_path, records, _concept(namespace)))

    assert any("this conversion touches" in e for e in errors), (
        f"{depth}-anchored concept on an unread object was accepted: {errors}"
    )


def test_dest_concept_on_an_origin_only_anchor_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """The side follows the anchor: an origin-only anchor cannot publish dest-side.

    Retyping the example's one origin_concept -- anchored at `ocs`, a data source only
    the SAS input reads -- and moving it into the dest_concept block leaves a file that
    satisfies every other concept rule, so without the side check a resolver regression
    putting every concept on one side would publish.
    """
    origin_concept = _one(records, "origin_concept")
    records.remove(origin_concept)
    retyped = dict(origin_concept, record_type="dest_concept")

    errors = validate_schema_resolution(_write(tmp_path, records, retyped))

    assert any("appears only on the origin side" in e for e in errors)


def test_origin_concept_on_a_dest_side_anchor_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """The collapse rule runs the other way too: a dest-side anchor publishes dest-side."""
    extra = dict(_concept("edwc_prd"), record_type="origin_concept")

    errors = validate_schema_resolution(_write(tmp_path, records, extra))

    assert any("publishes as dest_concept (the collapse rule)" in e for e in errors)


def test_concept_with_a_null_label_is_accepted(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    """`catalog.concepts.label` is nullable, so a null one is a legal resolution.

    It is the only prose field the DDL leaves nullable, and the resolver passes it
    through verbatim, so requiring it here would reject a file the catalog can produce.
    """
    _one(records, "dest_concept")["label"] = None

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert errors == []


def test_concept_with_an_empty_label_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """Null means no label; an empty string is a malformed one, which the loader refuses."""
    _one(records, "dest_concept")["label"] = ""

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("'label' must be null or a non-empty string" in e for e in errors)


def test_concept_id_without_the_marker_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """A concept_id names its anchor before a '.concept.' marker; without one it names nothing."""
    extra = dict(_concept("edwc_prd"), concept_id="edwc_prd.no_marker_here")

    errors = validate_schema_resolution(_write(tmp_path, records, extra))

    assert any("concept_id must contain '.concept.'" in e for e in errors)


def test_concept_with_non_list_related_object_ids_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """related_object_ids is a list of ids, not the one id a hand edit might leave."""
    extra = dict(_concept("edwc_prd"), related_object_ids="edwc_prd.claims_vw_prd")

    errors = validate_schema_resolution(_write(tmp_path, records, extra))

    assert any("'related_object_ids' must be a list of strings" in e for e in errors)


def test_concept_on_a_read_column_is_accepted(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    """The same check accepts an anchor on a column a translation does read.

    Pairs with the rejection above: together they show the checks discriminate on
    whether the object is touched, rather than rejecting every column anchor
    outright. The column used is one the example already carries a concept for, so
    it is known to be reachable only through the dest-column derivation.
    """
    read_column = "edwc_prd.claims_vw_prd.v_clm.clm_type_cd"
    path = _write(tmp_path, records, _concept(read_column))

    errors = validate_schema_resolution(path)

    assert errors == []


# --- Mutation helpers ---


def _one(records: list[dict[str, Any]], record_type: str, **keys: str) -> dict[str, Any]:
    """Select a single record by type and field values.

    Args:
        records: The parsed example records.
        record_type: The record_type to select.
        **keys: Field values the record must match.

    Returns:
        The matching record, mutated in place by the caller.
    """
    return next(
        r for r in records
        if r["record_type"] == record_type and all(r.get(k) == v for k, v in keys.items())
    )


def _first_mapped_column(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the first origin column with status 'mapped' in the example.

    Args:
        records: The parsed example records.

    Returns:
        The origin column object, mutated in place by the caller.
    """
    return next(
        column
        for record in records if record["record_type"] == "origin_sas_variable"
        for column in record["origin_columns"]
        if column["mapping_status"] == "mapped"
    )


def _first_candidate(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the first candidate on the first mapped origin column.

    Args:
        records: The parsed example records.

    Returns:
        The candidate object, mutated in place by the caller.
    """
    return _first_mapped_column(records)["candidates"][0]


# --- Reading the file ---


def test_missing_input_file_is_reported(tmp_path: Path) -> None:
    """A path that does not exist is reported as an error, not raised."""
    absent = tmp_path / "absent.jsonl"

    errors = validate_schema_resolution(absent)

    assert errors == [f"File not found: {absent}"]


def test_unreadable_input_file_is_reported(tmp_path: Path) -> None:
    """An OSError while reading is reported as an error, not raised.

    A directory stands in for the unreadable file: it exists, so the reader gets past
    the missing-file branch, and opening it for reading raises an OSError.
    """
    directory = tmp_path / "resolution.jsonl"
    directory.mkdir()

    errors = validate_schema_resolution(directory)

    assert any("Failed to read file" in e for e in errors)


def test_malformed_json_line_is_reported(tmp_path: Path) -> None:
    """A truncated line is reported with its line number, not raised as a decode error."""
    path = tmp_path / "resolution.jsonl"
    path.write_text('{"record_type":\n', encoding="utf-8")

    errors = validate_schema_resolution(path)

    assert any("Line 1: Invalid JSON" in e for e in errors)


def test_non_object_line_is_reported(tmp_path: Path) -> None:
    """A JSON array line is reported and dropped: every later check assumes an object.

    The blank line ahead of it is skipped silently -- only the array is an error --
    so the reported line number is the physical one, not a count of parsed records.
    """
    path = tmp_path / "resolution.jsonl"
    path.write_text("\n[1, 2]\n", encoding="utf-8")

    errors = validate_schema_resolution(path)

    assert any("Line 2: Record must be a JSON object, got list" in e for e in errors)


# --- The structural gate ---


def test_empty_file_is_rejected(tmp_path: Path) -> None:
    """A file that parses to no records fails rather than passing vacuously."""
    path = _write(tmp_path, [])

    errors = validate_schema_resolution(path)

    assert errors == ["File contains 0 records"]


def test_unknown_record_type_is_rejected(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    """A record_type outside the write order is rejected, reported by line position."""
    records[-1]["record_type"] = "origin_sas_footnote"

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any(
        f"Record {len(records)}: Invalid or missing record_type 'origin_sas_footnote'" in e
        for e in errors
    )


def test_meta_off_the_first_line_is_rejected(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    """The meta record anchors the file, so it must be line 1."""
    records[0], records[1] = records[1], records[0]

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("The meta record must be the first line" in e for e in errors)


# --- Record order ---


def test_the_old_write_order_is_rejected(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    """The previous order (SAS input before the joins) fails the order check."""
    datasets = [r for r in records if r["record_type"] == "origin_sas_dataset"]
    rest = [r for r in records if r["record_type"] != "origin_sas_dataset"]
    # Move the dataset records to right after meta, the old order's position.
    reordered = rest[:1] + datasets + rest[1:]

    errors = validate_schema_resolution(_write(tmp_path, reordered))

    assert any("grouped by type in write order" in e for e in errors)


def test_records_unsorted_within_their_group_are_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """Groups are sorted on their identifying ids: byte-stable reruns rest on it.

    This is the rule the test helper's splice exists to satisfy, so nothing else in
    the suite ever reaches it.
    """
    columns = [i for i, r in enumerate(records) if r["record_type"] == "dest_column"]
    records[columns[0]], records[columns[1]] = records[columns[1]], records[columns[0]]

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("'dest_column' records must be sorted by column_id" in e for e in errors)


def test_non_scalar_identifying_id_is_reported_rather_than_raising(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """A malformed identifying id is reported, not raised.

    The ids are the file's hash and sort keys, so a list-valued table_id used to raise
    TypeError out of the first sort it reached -- leaving the one record shape this
    module could not report the one shape it exists to report.
    """
    _one(records, "dest_table")["table_id"] = ["a", "b", "c"]

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("identifying id(s) ['table_id'] must be strings" in e for e in errors)


def test_file_with_no_variable_records_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """A resolution accounts for the whole SAS input, so it never carries no variable.

    Every completeness rule is expressed per variable, so a file of meta, systems, and
    datasets alone satisfies them all vacuously -- a byte-stable published file that
    accounts for nothing.
    """
    kept = [r for r in records if r["record_type"] == "origin_sas_dataset"]

    errors = validate_schema_resolution(_write(tmp_path, records[:1] + kept))

    assert any("carries no origin_sas_variable record" in e for e in errors)


# --- Duplicate records ---


@pytest.mark.parametrize(
    ("record_type", "message"),
    [
        ("origin_sas_dataset", "Duplicate origin_sas_dataset record"),
        ("dest_table", "Duplicate dest_table record"),
        ("origin_table", "Duplicate origin_table record"),
        ("ref_table", "Duplicate ref_table record"),
        ("dest_schema", "Duplicate dest_schema record"),
        ("dest_data_source", "Duplicate dest_data_source record"),
        ("dest_column", "Duplicate dest_column record"),
        ("origin_sas_variable", "Duplicate origin_sas_variable record"),
        ("dest_concept", "duplicate concept_id"),
    ],
)
def test_repeated_record_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]], record_type: str, message: str
) -> None:
    """A record emitted twice is caught for every type that carries an identity.

    A resolver bug that emits one record twice produces a file that satisfies every
    other rule -- the copy is well-formed, in scope, and in sorted position -- so
    without these checks the duplicate publishes silently.
    """
    original = next(r for r in records if r["record_type"] == record_type)
    # The copy goes directly after the original, so the equal sort keys stay ordered
    # and the test fails on the duplicate rule alone.
    records.insert(records.index(original) + 1, json.loads(json.dumps(original)))

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any(message in e for e in errors)


# --- The completeness contract (the second implementation of the gap rules) ---


def test_variable_with_no_origin_columns_is_rejected(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    """An empty origin_columns list is rejected: an unmatched variable is a catalog gap."""
    variable = _one(records, "origin_sas_variable", variable="clm_type")
    variable["origin_columns"] = []

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("has no origin columns" in e for e in errors)


def test_variable_referencing_an_unknown_dataset_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """A variable belongs to a dataset the file declares; without one it has no coordinates.

    The dataset name sorts last among the variable records, so the group stays sorted
    and the test fails on the orphan rule alone.
    """
    _one(records, "origin_sas_variable", variable="prf_prvdr")["dataset"] = "SRCLIB.ZZ_PHANTOM"

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any(
        "Variables reference dataset 'SRCLIB.ZZ_PHANTOM', which has no origin_sas_dataset record" in e
        for e in errors
    )


def test_in_transition_column_with_status_not_applicable_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """not_applicable on an in-transition column is rejected: the question WAS asked."""
    _first_mapped_column(records)["mapping_status"] = "not_applicable"

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("'not_applicable' but the dataset has dest_data_scope" in e for e in errors)


def test_in_transition_column_with_no_candidates_is_rejected(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    """An in-transition column with an empty candidate list is a silent column."""
    _first_mapped_column(records)["candidates"] = []

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("silent in-transition column is a catalog gap" in e for e in errors)


def test_no_transition_column_must_be_not_applicable_with_no_candidates(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """A no-transition column may carry neither a transition status nor candidates."""
    dataset = _one(records, "origin_sas_dataset", dataset="SRCLIB.OCS_CLAIMS_*")
    dataset["dest_data_scope"] = None

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("a no-transition column is always 'not_applicable'" in e for e in errors)
    assert any("no mappings should have been consulted" in e for e in errors)


@pytest.mark.parametrize("status", ["dropped", "unmapped"])
def test_status_outside_the_vocabulary_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]], status: str
) -> None:
    """Only the three statuses are valid; a plausible near-miss name is not."""
    _first_mapped_column(records)["mapping_status"] = status

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any(f"Invalid mapping_status '{status}'" in e for e in errors)


def test_no_equivalent_status_with_a_translated_candidate_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """no_equivalent means every candidate documents an absence, not a translation."""
    _first_mapped_column(records)["mapping_status"] = "no_equivalent"

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any(
        "status 'no_equivalent' requires candidates that all have a null" in e for e in errors
    )


def test_dataset_record_with_a_system_field_is_rejected(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    """Systems are meta-only, so a dataset record carrying one has an unexpected field."""
    dataset = _one(records, "origin_sas_dataset", dataset="SRCLIB.OCS_CLAIMS_*")
    dataset["origin_system"] = "warehouse"

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("Unexpected fields" in e and "origin_system" in e for e in errors)


def test_meta_record_without_a_system_is_rejected(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    """Both systems are required on meta: a null dest_system fails."""
    _one(records, "meta")["dest_system"] = None

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("Meta record: 'dest_system' must be a non-empty string" in e for e in errors)


# --- System records and the collapse rule ---


def test_missing_dest_system_record_is_rejected(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    """A dest_system record always publishes."""
    records = [r for r in records if r["record_type"] != "dest_system"]

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("Expected exactly one dest_system record, found 0" in e for e in errors)


def test_origin_system_record_under_equal_systems_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """With equal meta systems, an origin_system record violates the collapse rule."""
    meta = _one(records, "meta")
    meta["origin_system"] = meta["dest_system"]

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("no origin_system record may publish" in e for e in errors)


def test_missing_origin_system_record_under_differing_systems_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """With differing meta systems, exactly one origin_system record is required."""
    records = [r for r in records if r["record_type"] != "origin_system"]

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("exactly one origin_system record is required" in e for e in errors)


def test_system_record_disagreeing_with_meta_is_rejected(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    """A dest_system record must name the system meta declares."""
    _one(records, "dest_system")["system"] = "elsewhere"

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("but meta declares dest_system" in e for e in errors)


def test_origin_system_record_disagreeing_with_meta_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """The origin side of the same rule: the record names the system meta declares.

    The two sides are separate checks, so covering only dest would leave an
    origin_system record naming any system at all.
    """
    _one(records, "origin_system")["system"] = "elsewhere"

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("but meta declares origin_system" in e for e in errors)


# --- Table records: grain and addresses ---


def test_table_record_without_primary_key_columns_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """Every table record carries primary_key_columns, even as the empty list."""
    table = next(r for r in records if r["record_type"] == "dest_table")
    del table["primary_key_columns"]

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("Missing fields" in e and "primary_key_columns" in e for e in errors)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("clm_type_cd", "must be a list"),
        (["b", "a"], "must be sorted"),
        (["edwc_prd.claims_vw_prd.v_clm.geo_mbr_sk"], "leaf column names"),
    ],
)
def test_malformed_primary_key_columns_are_rejected(
    tmp_path: Path, records: list[dict[str, Any]], value: str | list[str], message: str
) -> None:
    """The grain is a sorted list of dotless leaf names; anything else is rejected."""
    table = next(r for r in records if r["record_type"] == "ref_table")
    table["primary_key_columns"] = value

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any(message in e for e in errors)


def test_deployed_venues_is_an_unexpected_field(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    """A deployed_venues field is rejected wherever it appears.

    Nothing in the record shape carries a venue list: the id-matched
    origin_table/dest_table pair carries the copy-switch fact, and reachability is the
    deployment gate's job.
    """
    table = next(r for r in records if r["record_type"] == "dest_table")
    table["deployed_venues"] = ["edw"]

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("Unexpected fields" in e and "deployed_venues" in e for e in errors)


@pytest.mark.parametrize("kind", ["origin_table", "dest_table", "ref_table"])
def test_table_record_with_null_physical_names_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]], kind: str
) -> None:
    """Every table form carries a full physical address."""
    table = next(r for r in records if r["record_type"] == kind)
    table["physical_table_name"] = None

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any(
        kind in e and "'physical_table_name' must be a non-empty string" in e
        for e in errors
    )


# --- The dest_table and origin_table sets ---


def test_dest_table_record_outside_the_derivation_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """A dest_table no surviving candidate derives is rejected."""
    template = next(r for r in records if r["record_type"] == "dest_table")
    extra = dict(template, table_id=UNREAD_TABLE)

    errors = validate_schema_resolution(_write(tmp_path, records, extra))

    assert any(f"dest_table record '{UNREAD_TABLE}' is not among the dest tables" in e for e in errors)


def test_missing_origin_table_record_violates_the_pairing_rule(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """Every SAS parent the pairing rule names must have its origin_table record."""
    removed = next(r for r in records if r["record_type"] == "origin_table")
    records.remove(removed)

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any(
        f"SAS parent '{removed['table_id']}' has no origin_table record" in e for e in errors
    )


def test_origin_table_record_outside_the_pairing_rule_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """An origin_table for a dest table the SAS process never read is rejected."""
    dest = next(r for r in records if r["record_type"] == "dest_table")
    template = next(r for r in records if r["record_type"] == "origin_table")
    extra = dict(template, table_id=dest["table_id"])

    errors = validate_schema_resolution(_write(tmp_path, records, extra))

    assert any("is not required by the pairing rule" in e for e in errors)


# --- dest_column records ---


def test_dest_column_with_an_unknown_field_is_rejected(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    """The dest_column field set is exact -- the origin-column shape minus mappings."""
    column = next(r for r in records if r["record_type"] == "dest_column")
    column["mapping_status"] = "mapped"

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("Unexpected fields" in e and "mapping_status" in e for e in errors)


def test_missing_dest_column_record_is_rejected(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    """Every column an expression, dest join, or dest table's key list references needs a record."""
    removed = next(r for r in records if r["record_type"] == "dest_column")
    records.remove(removed)

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any(
        f"Referenced dest column '{removed['column_id']}' has no dest_column record" in e
        for e in errors
    )


def test_unreferenced_dest_column_record_is_rejected(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    """A record for a column nothing references is rejected -- it was never read."""
    template = next(r for r in records if r["record_type"] == "dest_column")
    extra = dict(
        template,
        column_id=UNREAD_COLUMN,
        table_id=".".join(UNREAD_COLUMN.split(".")[:3]),
    )

    errors = validate_schema_resolution(_write(tmp_path, records, extra))

    assert any(
        "is not referenced by any surviving expression, dest join, or dest table's key list" in e
        for e in errors
    )


@pytest.mark.parametrize("field", ["is_nullable", "is_primary_key"])
def test_dest_column_with_a_non_boolean_flag_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]], field: str
) -> None:
    """The dest column's flags are booleans, as the origin column's are."""
    _one(records, "dest_column")[field] = "yes"

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any(f"'{field}' must be a boolean" in e for e in errors)


def test_dest_column_whose_table_id_is_not_its_prefix_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """A dest column's table_id is the leading three segments of its column_id."""
    _one(records, "dest_column")["table_id"] = "edwc_prd.claims_vw_prd.some_other_table"

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("is not the leading 3 segments of column_id" in e for e in errors)


def test_dest_column_on_a_table_that_is_not_a_dest_table_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """A dest column belongs to a table the converted code reads, not any table."""
    template = _one(records, "dest_column")
    extra = dict(template, table_id=UNREAD_TABLE, column_id=f"{UNREAD_TABLE}.mbr_sk")

    errors = validate_schema_resolution(_write(tmp_path, records, extra))

    assert any(f"its table '{UNREAD_TABLE}' is not a dest table" in e for e in errors)


def test_dest_table_key_without_a_dest_column_record_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """A dest table's flagged key is a read column, so it needs its dest_column record."""
    table = next(r for r in records if r["record_type"] == "dest_table")
    table["primary_key_columns"] = ["invented_key"]

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any(
        f"Referenced dest column '{table['table_id']}.invented_key' has no dest_column record" in e
        for e in errors
    )


# --- ref_table completeness ---


def test_ref_pointer_without_a_ref_table_record_is_rejected(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    """Every non-null ref_table_id must resolve to a ref_table record."""
    removed = next(r for r in records if r["record_type"] == "ref_table")
    records.remove(removed)

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any(
        f"Referenced code set '{removed['table_id']}' has no ref_table record" in e
        for e in errors
    )


def test_origin_column_ref_pointer_without_a_ref_table_record_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """The origin side of the code-set reconciliation runs too.

    Every origin column in the example carries a null pointer and only dest columns
    point at the code set, so without this the origin half of the rule never fires.
    """
    _first_mapped_column(records)["ref_table_id"] = "ref.codes.invented_cd"

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any(
        "Referenced code set 'ref.codes.invented_cd' has no ref_table record" in e
        for e in errors
    )


def test_ref_table_record_without_a_pointer_is_rejected(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    """A ref_table nothing points at is rejected -- code sets arrive only via pointers."""
    template = next(r for r in records if r["record_type"] == "ref_table")
    extra = dict(template, table_id="ref.codes.invented_cd")

    errors = validate_schema_resolution(_write(tmp_path, records, extra))

    assert any(
        "ref_table record 'ref.codes.invented_cd' is pointed at by no ref_table_id" in e
        for e in errors
    )


# --- Candidates ---


def test_candidate_with_a_deployment_flag_is_rejected(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    """A per-candidate deployability flag is an unexpected candidate field.

    The deployment gate means every published candidate's tables are reachable, so
    such a flag would be a constant.
    """
    candidate = _first_candidate(records)
    candidate["deployed_in_target_venue"] = True

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("Unexpected fields" in e and "deployed_in_target_venue" in e for e in errors)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("validated", "yes", "'validated' must be a boolean"),
        ("mapping_name", "", "'mapping_name' must be a non-empty string"),
        (
            "target_tables_referenced",
            "edwc_prd.claims_vw_prd.v_clm",
            "'target_tables_referenced' must be a list",
        ),
        ("target_expression", "", "'target_expression' must be null or a non-empty string"),
        (
            "target_tables_referenced",
            [],
            "a non-null target_expression must reference at least one target table",
        ),
    ],
)
def test_malformed_candidate_field_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]], field: str, value: Any, message: str
) -> None:
    """Each candidate field carries its own shape rule, checked whatever the status."""
    _first_candidate(records)[field] = value

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any(message in e for e in errors)


def test_candidate_targeting_outside_dest_data_scope_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """A surviving candidate reads only from dest_data_scope.

    This is the Step 4 filter's survivor rule, checked here independently: a candidate
    pointing at a region the conversion is not targeting is one the resolver should
    have filtered out.
    """
    _first_candidate(records)["target_tables_referenced"] = ["other_db.other_schema.other_table"]

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("is not under the dataset's dest_data_scope" in e for e in errors)


def test_duplicate_candidate_mapping_name_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """A mapping_name is unique per origin column, so the same one twice is a duplicate."""
    column = _first_mapped_column(records)
    column["candidates"].append(json.loads(json.dumps(column["candidates"][0])))

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("duplicate candidate mapping_name" in e for e in errors)


def test_multi_candidate_column_with_a_null_use_when_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """The catalog requires use_when on every mapping once a column carries several.

    Without it the planner is handed two translations and no rule for choosing, so a
    null one that reached the output is a carry-through bug.
    """
    column = _first_mapped_column(records)
    second = json.loads(json.dumps(column["candidates"][0]))
    second["mapping_name"] = "alternate"
    column["candidates"].append(second)

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("has a null use_when" in e for e in errors)


def test_duplicate_origin_column_within_a_variable_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """A variable's origin_columns list carries each matched column once."""
    variable = _one(records, "origin_sas_variable", variable="clm_type")
    variable["origin_columns"].append(json.loads(json.dumps(variable["origin_columns"][0])))

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("duplicate origin_column_id" in e for e in errors)


def test_null_expression_candidate_keeping_its_target_tables_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """A no-equivalent candidate references nothing and must say what replaced it.

    The expression and the referenced tables are one statement in two fields, so
    dropping the expression while keeping the tables leaves the record self-
    contradictory; the missing rationale is the second half of the same rule.
    """
    candidate = _first_candidate(records)
    candidate["target_expression"] = None
    candidate["notes"] = None

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("a null target_expression must reference no target tables" in e for e in errors)
    assert any("a null target_expression requires non-null notes" in e for e in errors)


# --- Joins ---


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("validated", "yes", "'validated' must be a boolean"),
        ("relationship_name", None, "'relationship_name' must be a non-empty string"),
        ("join_condition", "", "'join_condition' must be a non-empty string"),
    ],
)
def test_malformed_join_field_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]], field: str, value: Any, message: str
) -> None:
    """A join's identity and condition are shape-checked, not just present.

    The name is a third of the join identity -- the sort key, the dedup key, and the
    label the join's own errors are reported under -- so a null one renders as 'None'
    and dedups against every other nameless join.
    """
    _one(records, "dest_join")[field] = value

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any(message in e for e in errors)


def test_join_endpoint_outside_the_table_set_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """A dest_join may only join tables a candidate reads.

    The join analogue of the dest_column reconciliation: a join naming a table the
    conversion never reads would hand planning a relationship it cannot use.
    """
    _one(records, "dest_join")["table_b_id"] = UNREAD_TABLE

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any(
        f"endpoint '{UNREAD_TABLE}' is not among the dest_join table set" in e for e in errors
    )


def test_repeated_join_is_rejected(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    """One relationship publishes once: the same (a, b, name) twice is a duplicate."""
    original = _one(records, "dest_join")
    records.insert(records.index(original) + 1, json.loads(json.dumps(original)))

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("duplicate join" in e for e in errors)


def test_origin_join_repeating_a_dest_join_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """dest takes precedence, so one relationship never publishes under both types.

    The origin and dest table sets can overlap -- a mapping may target a table in its
    own data source -- which is exactly when a resolver bug would emit both.
    """
    dest_join = _one(records, "dest_join")
    origin_join = dict(json.loads(json.dumps(dest_join)), record_type="origin_join")
    records.insert(records.index(dest_join), origin_join)

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("is already a dest_join; dest takes precedence" in e for e in errors)


def test_origin_join_without_a_transition_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """Without a transition the SAS parents ARE the dest tables, so dest_join covers them."""
    for dataset in records:
        if dataset["record_type"] == "origin_sas_dataset":
            dataset["dest_data_scope"] = None
    parents = sorted(r["table_id"] for r in records if r["record_type"] == "origin_table")
    origin_join = dict(
        json.loads(json.dumps(_one(records, "dest_join"))),
        record_type="origin_join",
        table_a_id=parents[0],
        table_b_id=parents[1],
    )
    records.insert(records.index(_one(records, "dest_join")), origin_join)

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("origin_join records require at least one dataset" in e for e in errors)


# --- Origin column and variable field shapes ---


def test_origin_column_with_a_non_boolean_primary_key_flag_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """is_primary_key is a boolean, not the string a hand-edited export might carry."""
    _first_mapped_column(records)["is_primary_key"] = "yes"

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("'is_primary_key' must be a boolean" in e for e in errors)


def test_origin_column_whose_table_id_is_not_its_prefix_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """The table_id must be the leading three segments of the origin_column_id."""
    _first_mapped_column(records)["table_id"] = "ocs.non_institutional.some_other_table"

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("is not the leading 3 segments of origin_column_id" in e for e in errors)


@pytest.mark.parametrize("column_id", ["ocs.non_institutional..clm_type", "ocs.non_institutional.clm."])
def test_check_segments_rejects_an_id_with_an_empty_segment(
    tmp_path: Path, records: list[dict[str, Any]], column_id: str
) -> None:
    """A segment count alone would pass "a..b" and "a.b.", which address no catalog object.

    The sibling data_val_catalog_gaps.py enforces the same rule; both validators guard
    dotted ids the same way so a malformed id cannot pass one gate and fail the other.
    """
    _first_mapped_column(records)["origin_column_id"] = column_id

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("non-empty segments" in e for e in errors)


@pytest.mark.parametrize("entry", ["ocs..non_institutional", "ocs."])
def test_data_scope_entry_with_an_empty_segment_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]], entry: str
) -> None:
    """An ltree prefix is bounded above but not below; an empty segment matches no path."""
    _one(records, "meta")["origin_data_scope"] = [entry]

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("must have non-empty segments" in e for e in errors)


def test_meta_may_declare_a_null_origin_data_scope(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """`meta` carries what was *declared*, and declaring nothing process-wide is legal.

    Extraction requires only that every dataset have an *effective* origin_data_scope,
    so a config scoping each dataset and nothing at `[settings]` publishes an inventory
    whose meta carries no scope. The resolver resolves that inventory, so rejecting it
    here would fail a valid file at the last step. The dataset records still state what
    actually applied, and those remain required (the next test).
    """
    _one(records, "meta")["origin_data_scope"] = None

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert errors == []


@pytest.mark.parametrize("value", [None, []])
def test_dataset_record_without_a_resolved_origin_data_scope_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]], value: Any
) -> None:
    """A dataset record states the scope that applied, so it is never absent or empty."""
    _one(records, "origin_sas_dataset")["origin_data_scope"] = value

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("'origin_data_scope' must be a non-empty list" in e for e in errors)


def test_meta_origin_data_scope_present_but_empty_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """Null is a declaration of nothing; an empty list is a malformed declaration."""
    _one(records, "meta")["origin_data_scope"] = []

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("must be null or a non-empty list" in e for e in errors)


def test_origin_column_leaf_disagreeing_with_the_variable_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """The origin column's leaf name is the SAS variable it was matched to."""
    column = _first_mapped_column(records)
    column["origin_column_id"] = f"{column['table_id']}.some_other_column"

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("!= variable name" in e for e in errors)


def test_origin_column_outside_the_datasets_scope_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """The column was found by scanning origin_data_scope, so it must fall under it."""
    column = _first_mapped_column(records)
    leaf = column["origin_column_id"].split(".")[-1]
    column["table_id"] = "elsewhere.other_schema.other_table"
    column["origin_column_id"] = f"elsewhere.other_schema.other_table.{leaf}"

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("is not under the dataset's origin_data_scope" in e for e in errors)


def test_variable_with_a_non_integer_length_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """The carried SAS length is an integer, not its string rendering."""
    _one(records, "origin_sas_variable", variable="clm_type")["length"] = "8"

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("'length' must be an integer" in e for e in errors)


@pytest.mark.parametrize("sas_type", ["character", None])
def test_variable_with_a_type_outside_the_sas_vocabulary_is_rejected(
    tmp_path: Path, records: list[dict[str, Any]], sas_type: Any
) -> None:
    """A SAS variable is char or num -- the extraction validator's vocabulary.

    format and label are legitimately null in SAS, but the storage class never is, and
    standalone validation of a hand-held file is the documented invocation that would
    otherwise accept one.
    """
    _one(records, "origin_sas_variable", variable="clm_type")["type"] = sas_type

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any(f"Invalid type '{sas_type}'" in e for e in errors)


def test_non_object_origin_column_is_rejected(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    """A bare id string in origin_columns is rejected before any field is read."""
    variable = _one(records, "origin_sas_variable", variable="clm_type")
    variable["origin_columns"] = ["ocs.non_institutional.clm.clm_type"]

    errors = validate_schema_resolution(_write(tmp_path, records))

    assert any("each origin column must be an object" in e for e in errors)


# --- The input-inventory cross-check ---


def _input_inventory(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Derive the input_schema.jsonl records the committed example resolution implies.

    The cross-check demands the meta carried verbatim, each dataset's filepath and
    resolved scopes agreeing, and every variable's carried SAS metadata matching --
    so a matching inventory is rebuilt from the resolution's own records, and each
    rejection test then breaks exactly one agreement.

    Args:
        records: The committed example resolution's records.

    Returns:
        The inventory records: meta, then datasets, then variables.
    """
    meta = next(r for r in records if r["record_type"] == "meta")
    inventory: list[dict[str, Any]] = [dict(meta)]
    for r in records:
        if r["record_type"] == "origin_sas_dataset":
            inventory.append({
                "record_type": "origin_sas_dataset",
                "dataset": r["dataset"],
                "filepath": r["filepath"],
                "origin_data_scope": r["origin_data_scope"],
                "dest_data_scope": r["dest_data_scope"],
            })
        elif r["record_type"] == "origin_sas_variable":
            inventory.append({
                "record_type": "origin_sas_variable",
                "dataset": r["dataset"],
                "variable": r["variable"],
                **{field: r.get(field) for field in ("type", "format", "length", "label")},
            })
    return inventory


def _write_input(tmp_path: Path, inventory: list[dict[str, Any]]) -> Path:
    """Write inventory records as an input_schema.jsonl beside the resolution.

    Args:
        tmp_path: The pytest temporary directory.
        inventory: The inventory records to write.

    Returns:
        The path written.
    """
    path = tmp_path / "input_schema.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in inventory), encoding="utf-8")
    return path


def test_matching_input_inventory_is_accepted(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    """The committed example cross-checks cleanly against the inventory it implies."""
    inventory = _input_inventory(records)

    errors = validate_schema_resolution(_write(tmp_path, records), _write_input(tmp_path, inventory))

    assert errors == []


def test_input_inventory_without_meta_is_rejected(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    """An inventory with no meta record cannot anchor the cross-check."""
    inventory = [r for r in _input_inventory(records) if r["record_type"] != "meta"]

    errors = validate_schema_resolution(_write(tmp_path, records), _write_input(tmp_path, inventory))

    assert any("Input inventory has no meta record" in e for e in errors)


def test_meta_not_carried_verbatim_is_rejected(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    """The output meta must repeat the inventory's declaration field for field."""
    inventory = _input_inventory(records)
    inventory[0]["process_name"] = "renamed_process"

    errors = validate_schema_resolution(_write(tmp_path, records), _write_input(tmp_path, inventory))

    assert any("meta is carried through unchanged" in e and "process_name" in e for e in errors)


def test_input_variable_missing_from_output_is_rejected(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    """Every inventory variable must have an output record -- coverage is total."""
    inventory = _input_inventory(records)
    template = next(r for r in inventory if r["record_type"] == "origin_sas_variable")
    inventory.append(dict(template, variable="phantom_var"))

    errors = validate_schema_resolution(_write(tmp_path, records), _write_input(tmp_path, inventory))

    assert any("'phantom_var'" in e and "has no output origin_sas_variable record" in e for e in errors)


def test_output_variable_not_in_input_is_rejected(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    """An output variable the inventory never declared is an invention, not coverage."""
    inventory = _input_inventory(records)
    dropped = next(r for r in inventory if r["record_type"] == "origin_sas_variable")
    inventory.remove(dropped)

    errors = validate_schema_resolution(_write(tmp_path, records), _write_input(tmp_path, inventory))

    assert any(
        f"'{dropped['variable']}'" in e and "is not in the input inventory" in e for e in errors
    )


def test_drifted_carried_field_is_rejected(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    """The SAS metadata is carried verbatim: a drifted label fails the cross-check."""
    inventory = _input_inventory(records)
    variable = next(r for r in inventory if r["record_type"] == "origin_sas_variable")
    variable["label"] = "A label the resolution does not carry"

    errors = validate_schema_resolution(_write(tmp_path, records), _write_input(tmp_path, inventory))

    assert any("carried 'label'" in e for e in errors)


def test_input_dataset_missing_from_output_is_rejected(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    """Every inventory dataset must have an output origin_sas_dataset record."""
    inventory = _input_inventory(records)
    template = next(r for r in inventory if r["record_type"] == "origin_sas_dataset")
    inventory.append(dict(template, dataset="SRCLIB.PHANTOM"))

    errors = validate_schema_resolution(_write(tmp_path, records), _write_input(tmp_path, inventory))

    assert any("'SRCLIB.PHANTOM' has no output origin_sas_dataset record" in e for e in errors)


def test_output_dataset_not_in_input_is_rejected(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    """An output dataset the inventory never declared is an invention, not coverage.

    The mirror of the missing-input-dataset check: dropping the dataset from the
    inventory leaves the resolution claiming one the SAS process never read.
    """
    inventory = _input_inventory(records)
    dropped = next(r for r in inventory if r["record_type"] == "origin_sas_dataset")
    inventory.remove(dropped)

    errors = validate_schema_resolution(_write(tmp_path, records), _write_input(tmp_path, inventory))

    assert any(
        f"Output dataset '{dropped['dataset']}' is not in the input inventory" in e
        for e in errors
    )


def test_drifted_dataset_filepath_is_rejected(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    """A dataset's filepath is carried through, so a drifted one fails the cross-check."""
    inventory = _input_inventory(records)
    dataset = next(r for r in inventory if r["record_type"] == "origin_sas_dataset")
    dataset["filepath"] = "data/sas/renamed.sas7bdat"

    errors = validate_schema_resolution(_write(tmp_path, records), _write_input(tmp_path, inventory))

    assert any("filepath is" in e and "renamed.sas7bdat" in e for e in errors)


def test_missing_input_inventory_returns_the_load_error_alone(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    """An unreadable inventory reports itself, not every output variable as uncovered.

    The set differences are skipped when the inventory does not load: flagging every
    variable would bury the one error that explains them.
    """
    absent = tmp_path / "absent.jsonl"

    errors = validate_schema_resolution(_write(tmp_path, records), absent)

    assert errors == [f"File not found: {absent}"]


def test_drifted_resolved_scope_is_rejected(tmp_path: Path, records: list[dict[str, Any]]) -> None:
    """A dataset's resolved scopes must agree with the inventory's declaration-plus-overrides."""
    inventory = _input_inventory(records)
    dataset = next(r for r in inventory if r["record_type"] == "origin_sas_dataset")
    dataset["origin_data_scope"] = ["ocs.some_other_schema"]

    errors = validate_schema_resolution(_write(tmp_path, records), _write_input(tmp_path, inventory))

    assert any("resolved 'origin_data_scope'" in e for e in errors)


# --- The CLI entry point ---


def test_main_exits_non_zero_on_validation_errors(
    tmp_path: Path, records: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing resolution exits 1 through the CLI, not 0 with errors only logged."""
    path = _write(tmp_path, [r for r in records if r["record_type"] != "meta"])
    monkeypatch.setattr(sys, "argv", ["data_val_schema_resolution.py", "--input-data", str(path)])

    with pytest.raises(SystemExit) as excinfo:
        validator_main()

    assert excinfo.value.code == 1


def test_main_returns_cleanly_on_the_committed_example(monkeypatch: pytest.MonkeyPatch) -> None:
    """A passing resolution exits the CLI cleanly (no SystemExit)."""
    monkeypatch.setattr(
        sys, "argv", ["data_val_schema_resolution.py", "--input-data", str(EXAMPLE)]
    )

    assert validator_main() is None


def test_main_exits_non_zero_when_validation_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unexpected error inside validation still exits 1, not a traceback.

    Callers gate on the exit code, so an exception escaping main() would read as a
    crash rather than the clean failure the CLI contract promises -- and the abort
    path is what makes the crash-safety of every other check assertable.
    """

    def _raise(_input_data: Path, _input_schema: Path | None = None) -> list[str]:
        raise RuntimeError("unexpected failure")

    monkeypatch.setattr(
        sys, "argv", ["data_val_schema_resolution.py", "--input-data", str(EXAMPLE)]
    )
    monkeypatch.setattr(data_val_schema_resolution, "validate_schema_resolution", _raise)

    with pytest.raises(SystemExit) as excinfo:
        validator_main()

    assert excinfo.value.code == 1
