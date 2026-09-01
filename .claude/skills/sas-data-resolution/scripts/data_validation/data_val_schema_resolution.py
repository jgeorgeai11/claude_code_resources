"""Validate the input_schema_resolution.jsonl output from the sas-data-resolution skill.

Structural and semantic checks run on the resolution file alone. When the
input_schema.jsonl (from sas-variable-extraction) is supplied, coverage and
carried SAS metadata are cross-checked against it.

Scope: this checks the output's internal consistency and its fidelity to the
input inventory. It does NOT verify records against metadata_db -- a well-formed
resolution that does not actually exist in the catalog will pass. Confirming that
origin columns, candidates, deployments, joins, and concepts are real is the job
of the DB-backed resolver.

The completeness contract is enforced here independently of the resolver: the file
carries at least one variable, every variable carries at least one origin column,
every in-transition column is `mapped` or `no_equivalent` with at least one
candidate, `not_applicable` appears exactly when the dataset has no dest_data_scope,
every table record carries a full physical address and its `primary_key_columns`,
every referenced dest column and code set has exactly one record, the system records
follow the collapse rule, a concept publishes on the side its anchor decides, and
the dest_table and origin_table sets match their derivations. A resolver
regression that stops raising on catalog gaps therefore fails validation rather
than publishing.

Segment counts mirror the system-free catalog: ids never contain a system name, so
a column_id is 4 segments ({data_source}.{schema}.{table}.{column}) and a table_id
is 3. One structural assumption mirrors the skill's queries rather than a catalog
FK: a concept's namespace is one of the catalog objects the conversion touches --
a data source, schema, table, or column -- and carries a `.concept.` marker
(concepts.concept_id has no foreign key enforcing this).

The shape rules the sibling `data_val_catalog_gaps.py` also enforces -- exact field
sets, dotted-id segment counts, scope-prefix rules, and the JSONL reader -- come from
`jsonl_checks.py` so the two validators cannot drift apart on them.
"""

import re
import sys
import argparse
from pathlib import Path
from typing import Any
from collections import defaultdict

# logconfig ships in this skill's scripts/ folder, one level up. Resolve against
# this file, never the cwd, so this module imports from any working directory.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
from logconfig import setup_logging, get_logger

# The shared checks sit beside this module, so anchor them the same way.
sys.path.insert(0, str(_HERE))
from jsonl_checks import (
    COLUMN_ID_SEGMENTS,
    TABLE_ID_SEGMENTS,
    check_fields,
    check_scope_entries,
    check_segments,
    load_jsonl,
    require_nonempty_str,
)

logger = get_logger(__name__)

# Write order: descend the catalog hierarchy, origin before dest within each level,
# the SAS input last. Everything the variable payload references precedes it; one
# accepted forward reference remains -- a concept anchored to an origin column
# precedes the variable records that define origin columns (the anchor is a
# self-describing dotted id and the definition is self-contained prose).
RECORD_ORDER = (
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
RECORD_TYPES = set(RECORD_ORDER)
# Three statements of what the catalog knows: an equivalent is documented, the
# catalog affirmatively documents that none exists, or the question was never asked
# because the dataset does not change data sources. A silent column never publishes
# -- it fails the resolver as a catalog gap -- so there is no status for it.
MAPPING_STATUSES = {"mapped", "no_equivalent", "not_applicable"}
CARRIED_FIELDS = ("type", "format", "length", "label")
# The SAS storage class, carried verbatim from the inventory. The vocabulary is the
# extraction validator's (data_val_extract_sas_dataset_schemas.py VALID_TYPES): the
# two must not drift, since a type this accepts and extraction rejects could never
# have been produced.
SAS_TYPES = {"char", "num"}

# Sort key per record type within its group (by identifying ids).
SORT_KEYS = {
    "origin_system": lambda r: (r.get("system") or "",),
    "dest_system": lambda r: (r.get("system") or "",),
    "origin_data_source": lambda r: (r.get("data_source_id") or "",),
    "dest_data_source": lambda r: (r.get("data_source_id") or "",),
    "origin_schema": lambda r: (r.get("schema_id") or "",),
    "dest_schema": lambda r: (r.get("schema_id") or "",),
    "origin_table": lambda r: (r.get("table_id") or "",),
    "dest_table": lambda r: (r.get("table_id") or "",),
    "ref_table": lambda r: (r.get("table_id") or "",),
    "dest_column": lambda r: (r.get("column_id") or "",),
    "origin_join": lambda r: (r.get("table_a_id") or "", r.get("table_b_id") or "", r.get("relationship_name") or ""),
    "dest_join": lambda r: (r.get("table_a_id") or "", r.get("table_b_id") or "", r.get("relationship_name") or ""),
    "origin_concept": lambda r: (r.get("concept_id") or "",),
    "dest_concept": lambda r: (r.get("concept_id") or "",),
    "origin_sas_dataset": lambda r: (r.get("dataset") or "",),
    "origin_sas_variable": lambda r: (r.get("dataset") or "", r.get("variable") or ""),
}

# Field names behind each sort key, for error messages.
_SORT_FIELDS = {
    "origin_system": ("system",),
    "dest_system": ("system",),
    "origin_data_source": ("data_source_id",),
    "dest_data_source": ("data_source_id",),
    "origin_schema": ("schema_id",),
    "dest_schema": ("schema_id",),
    "origin_table": ("table_id",),
    "dest_table": ("table_id",),
    "ref_table": ("table_id",),
    "dest_column": ("column_id",),
    "origin_join": ("table_a_id", "table_b_id", "relationship_name"),
    "dest_join": ("table_a_id", "table_b_id", "relationship_name"),
    "origin_concept": ("concept_id",),
    "dest_concept": ("concept_id",),
    "origin_sas_dataset": ("dataset",),
    "origin_sas_variable": ("dataset", "variable"),
}

# Exact field sets per record type -- no more, no fewer.
META_FIELDS = {
    "record_type", "process_name", "origin_system", "dest_system",
    "origin_data_scope", "dest_data_scope",
}
# A declared system's catalog prose, published per the collapse rule.
SYSTEM_FIELDS = {"record_type", "system", "description", "notes"}
# The systems are process-wide, so a dataset record carries only the data scopes: a
# dataset system would be trivially derivable from the meta header, and one that
# differed would claim a system the resolution never used.
DATASET_FIELDS = {
    "record_type", "dataset", "filepath", "origin_data_scope", "dest_data_scope",
}
# All three table forms share one shape: prose, grain, and a physical address --
# dest_table addressed in the destination system, origin_table in the origin system,
# ref_table wherever the catalog's instance hosts it.
TABLE_FIELDS = {
    "record_type", "table_id", "description", "notes", "primary_key_columns",
    "physical_database_name", "physical_schema_name", "physical_table_name",
}
SCHEMA_FIELDS = {"record_type", "schema_id", "description", "notes"}
DATA_SOURCE_FIELDS = {"record_type", "data_source_id", "description", "notes"}
VARIABLE_FIELDS = {
    "record_type", "dataset", "variable", "type", "format", "length", "label", "origin_columns",
}
ORIGIN_COLUMN_FIELDS = {
    "table_id", "origin_column_id", "data_type", "is_nullable", "is_primary_key",
    "ref_table_id", "description", "notes", "mapping_status", "candidates",
}
# The origin-column shape minus the mapping machinery: a dest column documents
# what the emitted code reads, not how an origin column maps onto it.
DEST_COLUMN_FIELDS = {
    "record_type", "table_id", "column_id", "data_type", "is_nullable",
    "is_primary_key", "ref_table_id", "description", "notes",
}
# No deployability flag: the resolver's deployment gate means every candidate's
# tables are reachable in a published file, so the flag would be a constant.
CANDIDATE_FIELDS = {
    "mapping_name", "target_expression", "target_tables_referenced",
    "use_when", "notes", "validated",
}
JOIN_FIELDS = {
    "record_type", "table_a_id", "table_b_id", "relationship_name",
    "join_condition", "cardinality", "use_when", "notes", "validated",
}
CONCEPT_FIELDS = {
    "record_type", "concept_id", "label", "definition", "notes", "related_object_ids",
}


def _dest_columns(expression: Any, tables: Any) -> set[str]:
    """Return the dest columns an expression reads, given the tables it names.

    Mirrors `dest_columns_referenced` in resolve_schema.py -- that function is the copy
    this one must not drift from, since the resolver derives the `dest_column` records
    with it and this check re-derives what those records must cover.

    The catalog records which tables a translation reads, not which columns, so the
    columns are recovered from the expression text: a column reference is a known table
    id followed by a column name. The scan folds case on both sides -- a
    `target_expression` is authored SQL, where case is free, while catalog ids are
    lowercase -- so an expression writing `BENE.BENE_SEX_CD` yields the same column id
    as one writing `bene.bene_sex_cd`. Matching case-sensitively would derive nothing
    from an uppercase expression and silently agree with a resolver that derived
    nothing either.

    Args:
        expression: The candidate's target expression; a non-string reads nothing.
        tables: The table ids the candidate references; a non-list names nothing.

    Returns:
        The referenced column ids, in the catalog's lowercase form.
    """
    if not isinstance(expression, str) or not isinstance(tables, list):
        return set()
    found = set()
    for table_id in tables:
        if not isinstance(table_id, str):
            continue
        # The lookbehind mirrors the resolver: a table id must not match as the
        # dotted suffix of a longer identifier, which would yield a phantom column id.
        for column in re.findall(
            rf"(?<![a-z0-9_.-]){re.escape(table_id)}\.([a-z0-9_-]+)", expression
        ):
            found.add(f"{table_id}.{column}")
    return found


def _under_any(dotted: Any, scopes: Any) -> bool:
    """Return whether a dotted id is a descendant-or-self of any scope prefix.

    Mirrors the ltree `<@` semantics the skill's queries use: a prefix matches only at
    a segment boundary, so 'a.bc' is not under 'a.b'.

    Args:
        dotted: The id to test; non-string values fail the match rather than raising.
        scopes: The scope prefixes to test against; non-list values match nothing.

    Returns:
        True when the id falls under at least one scope.
    """
    if not isinstance(dotted, str) or not isinstance(scopes, list):
        return False
    return any(
        isinstance(p, str) and (dotted == p or dotted.startswith(p + "."))
        for p in scopes
    )


def _ltree_prefixes(dotted: str) -> set[str]:
    """Return the 1- and 2-segment (data source and schema) prefixes of a dotted id.

    Ids are system-free, so the data source is the leading segment and the schema is
    the first two.

    Args:
        dotted: A dotted ltree id (e.g. an origin column id or dest table id).

    Returns:
        The set of its `{data_source}` and `{data_source}.{schema}` prefixes.
    """
    parts = dotted.split(".")
    prefixes = {parts[0]}
    if len(parts) >= 2:
        prefixes.add(".".join(parts[:2]))
    return prefixes


def _validate_data_scopes(
    label: str, record: dict[str, Any], errors: list[str], *, origin_required: bool = True
) -> None:
    """Validate the two data scope coordinates on a meta or dataset record.

    Args:
        label: Human-readable identifier for the record.
        record: The meta or origin_sas_dataset record.
        errors: List of error messages, appended to in place.
        origin_required: True for a resolved dataset record, which always states the
            scope that applied. False for meta, which may carry a null declaration.
    """
    # dest_data_scope is null exactly when the data source is unchanged (the dataset
    # does not transition). origin_data_scope is required on a dataset record, which
    # states the scope that actually applied, but may be null on meta, which carries
    # what was *declared*: extraction legitimately publishes an inventory scoped per
    # dataset with nothing at the process level, and the resolver resolves it, so
    # demanding one here would reject a valid file at the last step.
    origin_data_scope = record.get("origin_data_scope")
    if origin_required:
        if not isinstance(origin_data_scope, list) or not origin_data_scope:
            errors.append(f"{label}: 'origin_data_scope' must be a non-empty list of ltree prefixes")
    elif origin_data_scope is not None and (not isinstance(origin_data_scope, list) or not origin_data_scope):
        errors.append(f"{label}: 'origin_data_scope' must be null or a non-empty list of ltree prefixes")
    dest_data_scope = record.get("dest_data_scope")
    if dest_data_scope is not None and (not isinstance(dest_data_scope, list) or not dest_data_scope):
        errors.append(f"{label}: 'dest_data_scope' must be null or a non-empty list of ltree prefixes")

    for field in ("origin_data_scope", "dest_data_scope"):
        value = record.get(field)
        if not isinstance(value, list):
            continue
        check_scope_entries(label, field, value, errors)


def _validate_meta(meta: dict[str, Any], errors: list[str]) -> None:
    """Validate the single meta record's fields and coordinate shapes.

    The systems live here and nowhere else -- they are process-wide, since deployment
    is resolved once over the pooled dest tables -- and BOTH are required: the
    resolver's Step 1 gate fails a meta missing either, so a published file always
    carries both.

    Args:
        meta: The meta record.
        errors: List of error messages, appended to in place.
    """
    check_fields("Meta record", meta, META_FIELDS, errors)
    require_nonempty_str("Meta record", meta, "process_name", errors)
    for field in ("origin_system", "dest_system"):
        require_nonempty_str("Meta record", meta, field, errors)
    _validate_data_scopes("Meta record", meta, errors, origin_required=False)


def _validate_system_records(
    system_records: list[dict[str, Any]], meta: dict[str, Any] | None, errors: list[str]
) -> None:
    """Validate the system records against the collapse rule.

    A `dest_system` record always exists, carrying the destination system's prose; an
    `origin_system` record exists exactly when the meta systems differ. Nothing else
    may take a system record.

    Args:
        system_records: The origin_system and dest_system records, in file order.
        meta: The meta record, or None when absent.
        errors: List of error messages, appended to in place.
    """
    origin_records = [r for r in system_records if r.get("record_type") == "origin_system"]
    dest_records = [r for r in system_records if r.get("record_type") == "dest_system"]

    for record in system_records:
        label = f"{record.get('record_type')} '{record.get('system')}'"
        check_fields(label, record, SYSTEM_FIELDS, errors)
        require_nonempty_str(label, record, "system", errors)
        require_nonempty_str(label, record, "description", errors)

    if len(dest_records) != 1:
        errors.append(f"Expected exactly one dest_system record, found {len(dest_records)}")
    if meta is None:
        return

    origin_system, dest_system = meta.get("origin_system"), meta.get("dest_system")
    if dest_records and dest_records[0].get("system") != dest_system:
        errors.append(
            f"dest_system record names '{dest_records[0].get('system')}', but meta "
            f"declares dest_system '{dest_system}'"
        )
    systems_differ = origin_system != dest_system
    if systems_differ and len(origin_records) != 1:
        errors.append(
            f"The systems differ, so exactly one origin_system record is required; "
            f"found {len(origin_records)}"
        )
    if not systems_differ and origin_records:
        errors.append(
            "The systems are equal, so no origin_system record may publish (the "
            "collapse rule); found one"
        )
    if systems_differ and origin_records and origin_records[0].get("system") != origin_system:
        errors.append(
            f"origin_system record names '{origin_records[0].get('system')}', but meta "
            f"declares origin_system '{origin_system}'"
        )


def _validate_dataset(record: dict[str, Any], errors: list[str]) -> None:
    """Validate an origin_sas_dataset record: fields, filepath, and resolved scopes.

    The exact field set rejects a system field outright -- systems are meta-only.

    Args:
        record: The origin_sas_dataset record.
        errors: List of error messages, appended to in place.
    """
    label = f"Dataset record '{record.get('dataset')}'"
    check_fields(label, record, DATASET_FIELDS, errors)
    for field in ("dataset", "filepath"):
        require_nonempty_str(label, record, field, errors)
    # Data scopes are resolved here, so both are present rather than only overrides.
    _validate_data_scopes(label, record, errors)


def _checkable_records(
    records: list[dict[str, Any]], errors: list[str]
) -> list[tuple[int, dict[str, Any]]]:
    """Report and set aside records whose record_type or identifying ids are not strings.

    The identifying ids (`_SORT_FIELDS`) are this file's hash and sort keys: they group
    records, deduplicate them, and order them. A non-scalar id -- a `table_id` of
    `["a", "b", "c"]` from a hand-held or foreign file -- would raise TypeError out of
    the first set or sort it reached, and this module exists to *report* a malformed
    record, so the one shape it cannot report must not be a malformed id. A null id is
    kept: it is hashable and sortable, and the per-record shape checks name it exactly.

    Args:
        records: All parsed records, in file order.
        errors: List of error messages, appended to in place.

    Returns:
        One (1-based position among the parsed records, record) pair per record the
        later checks can run on, in file order.
    """
    checkable: list[tuple[int, dict[str, Any]]] = []
    for position, record in enumerate(records, start=1):
        record_type = record.get("record_type")
        if not isinstance(record_type, str):
            errors.append(f"Record {position}: Invalid or missing record_type '{record_type}'")
            continue
        malformed = sorted(
            field for field in _SORT_FIELDS.get(record_type, ())
            if record.get(field) is not None and not isinstance(record.get(field), str)
        )
        if malformed:
            errors.append(
                f"Record {position} ({record_type}): identifying id(s) {malformed} must be "
                f"strings; the record cannot be checked further"
            )
            continue
        checkable.append((position, record))
    return checkable


def _validate_record_order(records: list[dict[str, Any]], errors: list[str]) -> None:
    """Validate that records are grouped by type in write order and sorted within groups.

    Args:
        records: All parsed records, in file order.
        errors: List of error messages, appended to in place.
    """
    rank = {name: i for i, name in enumerate(RECORD_ORDER)}
    ranks = [rank[r["record_type"]] for r in records if r.get("record_type") in rank]
    if ranks != sorted(ranks):
        errors.append(
            "Records must be grouped by type in write order: " + ", ".join(RECORD_ORDER)
        )
        return

    for name, key in SORT_KEYS.items():
        group = [r for r in records if r.get("record_type") == name]
        keys = [key(r) for r in group]
        if keys != sorted(keys):
            errors.append(f"'{name}' records must be sorted by {', '.join(_SORT_FIELDS[name])}")


def _validate_table_record(record: dict[str, Any], kind: str, errors: list[str]) -> None:
    """Validate one table record: fields, id shape, grain, and a full address.

    Every `dest_table`, `origin_table`, and `ref_table` carries a full physical
    address -- the resolver's deployment gate failed the run otherwise -- and its
    `primary_key_columns`: a sorted, possibly empty list of dotless leaf names (the
    record's table_id supplies the prefix).

    Args:
        record: The table record.
        kind: origin_table, dest_table, or ref_table, for error messages.
        errors: List of error messages, appended to in place.
    """
    label = f"{kind} '{record.get('table_id')}'"
    check_fields(label, record, TABLE_FIELDS, errors)
    check_segments(label, record.get("table_id"), TABLE_ID_SEGMENTS, "table_id", errors)

    keys = record.get("primary_key_columns")
    if not isinstance(keys, list) or not all(isinstance(k, str) and k for k in keys):
        errors.append(f"{label}: 'primary_key_columns' must be a list of non-empty strings")
    else:
        if any("." in k for k in keys):
            errors.append(
                f"{label}: 'primary_key_columns' entries must be leaf column names "
                f"(no dots); the record's table_id supplies the prefix"
            )
        if keys != sorted(keys):
            errors.append(f"{label}: 'primary_key_columns' must be sorted")

    for field in ("physical_database_name", "physical_schema_name", "physical_table_name"):
        value = record.get(field)
        if not isinstance(value, str) or not value:
            errors.append(f"{label}: '{field}' must be a non-empty string")


def _validate_scope_record(record: dict[str, Any], errors: list[str]) -> None:
    """Validate a schema or data-source record: fields and id shape.

    Args:
        record: The scope record.
        errors: List of error messages, appended to in place.
    """
    kind = record.get("record_type", "")
    if kind.endswith("_schema"):
        label = f"Schema '{record.get('schema_id')}'"
        check_fields(label, record, SCHEMA_FIELDS, errors)
        check_segments(label, record.get("schema_id"), 2, "schema_id", errors)
    else:
        label = f"Data source '{record.get('data_source_id')}'"
        check_fields(label, record, DATA_SOURCE_FIELDS, errors)
        check_segments(label, record.get("data_source_id"), 1, "data_source_id", errors)


def _validate_candidate(
    clabel: str, cand: dict[str, Any], dest_data_scope: object, errors: list[str]
) -> None:
    """Validate one candidate's fields, types, and referenced target tables.

    Args:
        clabel: Human-readable identifier for the candidate.
        cand: The candidate object.
        dest_data_scope: The dataset's effective dest_data_scope, or None.
        errors: List of error messages, appended to in place.
    """
    check_fields(clabel, cand, CANDIDATE_FIELDS, errors)

    if not isinstance(cand.get("validated"), bool):
        errors.append(f"{clabel}: 'validated' must be a boolean")
    if not isinstance(cand.get("mapping_name"), str) or not cand.get("mapping_name"):
        errors.append(f"{clabel}: 'mapping_name' must be a non-empty string")

    tables = cand.get("target_tables_referenced")
    if not isinstance(tables, list):
        errors.append(f"{clabel}: 'target_tables_referenced' must be a list")
        tables = []
    else:
        for table in tables:
            check_segments(clabel, table, TABLE_ID_SEGMENTS, "target table", errors)

    expression = cand.get("target_expression")
    if expression is None:
        # A no-equivalent mapping: it references no target, and the catalog guarantees
        # a rationale naming what the destination uses instead.
        if tables:
            errors.append(f"{clabel}: a null target_expression must reference no target tables")
        if not isinstance(cand.get("notes"), str) or not cand.get("notes"):
            errors.append(f"{clabel}: a null target_expression requires non-null notes")
    else:
        if not isinstance(expression, str) or not expression:
            errors.append(f"{clabel}: 'target_expression' must be null or a non-empty string")
        if not tables:
            errors.append(f"{clabel}: a non-null target_expression must reference at least one target table")
        # A surviving candidate reads only from dest_data_scope.
        if isinstance(dest_data_scope, list):
            for table in tables:
                if not _under_any(table, dest_data_scope):
                    errors.append(
                        f"{clabel}: target table '{table}' is not under the dataset's "
                        f"dest_data_scope {dest_data_scope}; it should have been filtered out"
                    )


def _validate_origin_column(
    vlabel: str,
    column: dict[str, Any],
    variable_name: object,
    coords: dict[str, Any],
    errors: list[str],
) -> None:
    """Validate one origin column: fields, id shapes, status, and candidates.

    Args:
        vlabel: Human-readable identifier for the parent variable.
        column: The origin column object.
        variable_name: The SAS variable name the column must correspond to; a
            non-string skips the name comparison, since the parent record already
            reports the bad variable name.
        coords: The dataset record holding this variable's resolved coordinates.
        errors: List of error messages, appended to in place.
    """
    ocid = column.get("origin_column_id")
    label = f"{vlabel} origin column '{ocid}'"
    check_fields(label, column, ORIGIN_COLUMN_FIELDS, errors)

    for field in ("is_nullable", "is_primary_key"):
        if not isinstance(column.get(field), bool):
            errors.append(f"{label}: '{field}' must be a boolean")
    require_nonempty_str(label, column, "data_type", errors)

    # origin_column_id is 4 segments; its leading 3 are the table_id and its leaf is
    # the variable.
    if check_segments(label, ocid, COLUMN_ID_SEGMENTS, "origin_column_id", errors):
        segments = ocid.split(".")
        table_id = column.get("table_id")
        if isinstance(table_id, str) and ".".join(segments[:TABLE_ID_SEGMENTS]) != table_id:
            errors.append(
                f"{label}: table_id '{table_id}' is not the leading "
                f"{TABLE_ID_SEGMENTS} segments of origin_column_id"
            )
        if isinstance(variable_name, str) and segments[-1].lower() != variable_name.lower():
            errors.append(
                f"{label}: origin_column_id column '{segments[-1]}' != variable name '{variable_name}'"
            )
    check_segments(label, column.get("table_id"), TABLE_ID_SEGMENTS, "table_id", errors)

    # The column was found by scanning origin_data_scope, so it must fall under it.
    origin_data_scope = coords.get("origin_data_scope")
    if isinstance(origin_data_scope, list) and isinstance(ocid, str) and not _under_any(ocid, origin_data_scope):
        errors.append(f"{label}: is not under the dataset's origin_data_scope {origin_data_scope}")

    status = column.get("mapping_status")
    if status not in MAPPING_STATUSES:
        errors.append(
            f"{label}: Invalid mapping_status '{status}', expected {sorted(MAPPING_STATUSES)}"
        )

    candidates = column.get("candidates")
    if not isinstance(candidates, list):
        errors.append(f"{label}: 'candidates' must be a list")
        return

    # No dest_data_scope means column_mappings was never consulted: the status is
    # not_applicable exactly then, and always with an empty candidate list.
    dest_data_scope = coords.get("dest_data_scope")
    if not dest_data_scope and candidates:
        errors.append(
            f"{label}: has {len(candidates)} candidate(s) but the dataset has no dest_data_scope, "
            f"so no mappings should have been consulted"
        )
    if not dest_data_scope and status in MAPPING_STATUSES and status != "not_applicable":
        errors.append(
            f"{label}: status '{status}' but the dataset has no dest_data_scope; a "
            f"no-transition column is always 'not_applicable'"
        )
    # An in-transition column always publishes an answer: mapped or no_equivalent,
    # with at least one candidate. A silent column is a catalog gap the resolver
    # fails on, so its appearance here is a resolver regression.
    if dest_data_scope and status == "not_applicable":
        errors.append(
            f"{label}: status 'not_applicable' but the dataset has dest_data_scope; an "
            f"in-transition column must be 'mapped' or 'no_equivalent'"
        )
    if dest_data_scope and not candidates:
        errors.append(
            f"{label}: has no candidates but the dataset has dest_data_scope; a silent "
            f"in-transition column is a catalog gap, never a published record"
        )

    seen = set()
    for i, cand in enumerate(candidates):
        if not isinstance(cand, dict):
            errors.append(f"{label} candidate {i + 1}: must be an object")
            continue
        _validate_candidate(f"{label} candidate {i + 1}", cand, dest_data_scope, errors)
        name = cand.get("mapping_name")
        if name in seen:
            errors.append(f"{label}: duplicate candidate mapping_name '{name}'")
        seen.add(name)

    # The catalog requires use_when on every mapping when an origin column carries more
    # than one, so a multi-candidate column with a null use_when is a carry-through bug.
    if len(candidates) > 1:
        for cand in candidates:
            if isinstance(cand, dict) and cand.get("use_when") is None:
                errors.append(
                    f"{label}: candidate '{cand.get('mapping_name')}' has a null use_when, "
                    f"but the column carries {len(candidates)} candidates"
                )

    # Status must agree with the candidates.
    has_expression = any(
        isinstance(c, dict) and c.get("target_expression") is not None for c in candidates
    )
    if status == "mapped" and not has_expression:
        errors.append(f"{label}: status 'mapped' but no candidate has a non-null target_expression")
    if status == "no_equivalent" and (not candidates or has_expression):
        errors.append(
            f"{label}: status 'no_equivalent' requires candidates that all have a null "
            f"target_expression (no-equivalent mappings)"
        )


def _validate_variable(
    record: dict[str, Any], coords: dict[str, Any], errors: list[str]
) -> None:
    """Validate a single origin_sas_variable record and each of its origin columns.

    Args:
        record: The origin_sas_variable record to check.
        coords: The dataset record holding this variable's resolved coordinates.
        errors: List of error messages, appended to in place.
    """
    label = f"Variable '{record.get('variable')}' in '{record.get('dataset')}'"
    check_fields(label, record, VARIABLE_FIELDS, errors)

    for field in ("dataset", "variable"):
        require_nonempty_str(label, record, field, errors)
    if not isinstance(record.get("length"), int) or isinstance(record.get("length"), bool):
        errors.append(f"{label}: 'length' must be an integer")
    # The other carried fields are legitimately null in SAS -- an unformatted, unlabelled
    # variable is ordinary -- but the storage class never is, so it is checked against
    # the extraction validator's vocabulary rather than merely for presence.
    sas_type = record.get("type")
    if not isinstance(sas_type, str) or sas_type not in SAS_TYPES:
        errors.append(f"{label}: Invalid type '{sas_type}', expected {sorted(SAS_TYPES)}")

    columns = record.get("origin_columns")
    if not isinstance(columns, list):
        errors.append(f"{label}: 'origin_columns' must be a list")
        return

    # An origin SAS dataset's variables are all documented, so every published
    # variable carries at least one origin column; an unmatched variable is a catalog
    # gap the resolver fails on before writing anything.
    if not columns:
        errors.append(
            f"{label}: has no origin columns; an unmatched variable is a catalog gap, "
            f"never a published record"
        )
    seen = set()
    for column in columns:
        if not isinstance(column, dict):
            errors.append(f"{label}: each origin column must be an object")
            continue
        _validate_origin_column(label, column, record.get("variable", ""), coords, errors)
        ocid = column.get("origin_column_id")
        if ocid in seen:
            errors.append(f"{label}: duplicate origin_column_id '{ocid}'")
        seen.add(ocid)


def _validate_joins(
    joins: list[dict[str, Any]], allowed: set[str], kind: str, errors: list[str]
) -> None:
    """Validate join records: fields, endpoints within the allowed table set, uniqueness.

    Args:
        joins: The join records of one kind.
        allowed: The table ids both endpoints must be drawn from.
        kind: Either 'origin_join' or 'dest_join', for error messages.
        errors: List of error messages, appended to in place.
    """
    seen = set()
    for record in joins:
        a, b, rel = record.get("table_a_id"), record.get("table_b_id"), record.get("relationship_name")
        label = f"{kind} '{rel}' ({a}, {b})"
        check_fields(label, record, JOIN_FIELDS, errors)
        if not isinstance(record.get("validated"), bool):
            errors.append(f"{label}: 'validated' must be a boolean")
        # The name is a third of the join identity -- the sort key, the dedup key, and
        # the label these errors are reported under -- so a null renders as 'None' and
        # dedups against every other nameless join.
        for field in ("relationship_name", "join_condition"):
            require_nonempty_str(label, record, field, errors)
        for endpoint in (a, b):
            if endpoint not in allowed:
                errors.append(f"{label}: endpoint '{endpoint}' is not among the {kind} table set")
        key = (a, b, rel)
        if key in seen:
            errors.append(f"{label}: duplicate join {key}")
        seen.add(key)


def _validate_concepts(
    concepts: list[dict[str, Any]], scope: set[str], dest_objects: set[str], errors: list[str]
) -> None:
    """Validate concept records: fields, in-scope anchor, the side rule, and uniqueness.

    Args:
        concepts: The concept records.
        scope: The catalog objects in play -- data sources, schemas, tables, and columns.
        dest_objects: The dest-side subset of `scope`: the dest tables, the code sets,
            their data-source and schema prefixes, and the referenced dest columns.
        errors: List of error messages, appended to in place.
    """
    seen = set()
    for record in concepts:
        cid = record.get("concept_id", "")
        label = f"Concept '{cid}'"
        check_fields(label, record, CONCEPT_FIELDS, errors)
        require_nonempty_str(label, record, "definition", errors)
        # `label` is null-or-non-empty, not required: `catalog.concepts.label` is
        # nullable (the DDL declares it `label text`, alone among the prose fields, and
        # the loader's ConceptRow types it `str | None`), and the resolver passes it
        # through verbatim. Requiring it would reject a resolution the catalog can
        # legally produce. The loader does reject a blank one, so an empty string is a
        # malformed record rather than an absent label.
        concept_label = record.get("label")
        if concept_label is not None and (not isinstance(concept_label, str) or not concept_label):
            errors.append(f"{label}: 'label' must be null or a non-empty string")

        related = record.get("related_object_ids")
        if not isinstance(related, list) or not all(isinstance(o, str) for o in related):
            errors.append(f"{label}: 'related_object_ids' must be a list of strings")

        # A concept anchors at a data source, a schema, a table, or a column, and must
        # anchor at one this conversion actually touches. Accepting anything beneath an
        # in-scope schema would admit concepts for the hundreds of columns on a wide
        # view that no variable resolved to, which the resolver's scope rule excludes.
        if not isinstance(cid, str) or ".concept." not in cid:
            errors.append(f"{label}: concept_id must contain '.concept.'")
        else:
            anchor = cid.split(".concept.")[0]
            if anchor not in scope:
                errors.append(
                    f"{label}: anchor '{anchor}' is not a data source, "
                    f"schema, table, or column this conversion touches"
                )
            else:
                # The collapse rule decides the side, and the anchor decides it alone:
                # an object appearing only on the origin side publishes origin_concept,
                # every other anchor -- dest-side or shared -- publishes dest_concept.
                # Without this, a resolver regression putting every concept on one side
                # would satisfy every other concept rule.
                is_dest_anchor = anchor in dest_objects
                kind = record.get("record_type")
                if kind == "dest_concept" and not is_dest_anchor:
                    errors.append(
                        f"{label}: anchor '{anchor}' appears only on the origin side, so "
                        f"the concept publishes as origin_concept"
                    )
                if kind == "origin_concept" and is_dest_anchor:
                    errors.append(
                        f"{label}: anchor '{anchor}' is a dest-side object, so the "
                        f"concept publishes as dest_concept (the collapse rule)"
                    )

        if cid in seen:
            errors.append(f"{label}: duplicate concept_id")
        seen.add(cid)


def validate_schema_resolution(input_data: Path, input_schema: Path | None = None) -> list[str]:
    """Run all validation checks on the resolution JSONL file.

    Args:
        input_data: Path to the input_schema_resolution.jsonl file to validate.
        input_schema: Optional path to the input_schema.jsonl; when given, output
            variable coverage and carried SAS metadata are cross-checked against it.

    Returns:
        List of validation error messages. Empty list means all checks passed.
    """
    errors: list[str] = []

    logger.info(f"Validating: {input_data}")
    # The reader carries each record's physical line number for its own parse errors;
    # the per-record messages below number records by position among the parsed ones.
    records = [record for _, record in load_jsonl(input_data, errors)]
    logger.info(f"Loaded {len(records)} records")

    if not records:
        if not errors:
            errors.append("File contains 0 records")
        return errors

    checkable = _checkable_records(records, errors)
    for position, record in checkable:
        if record["record_type"] not in RECORD_TYPES:
            errors.append(
                f"Record {position}: Invalid or missing record_type '{record['record_type']}'"
            )
    # Every check below keys on an id, so it runs on the checkable records alone; the
    # ones set aside are already reported.
    records = [record for _, record in checkable]

    metas = [r for r in records if r.get("record_type") == "meta"]
    system_records = [r for r in records if r.get("record_type") in ("origin_system", "dest_system")]
    dataset_records = [r for r in records if r.get("record_type") == "origin_sas_dataset"]
    dest_table_records = [r for r in records if r.get("record_type") == "dest_table"]
    dest_column_records = [r for r in records if r.get("record_type") == "dest_column"]
    variables = [r for r in records if r.get("record_type") == "origin_sas_variable"]
    origin_joins = [r for r in records if r.get("record_type") == "origin_join"]
    dest_joins = [r for r in records if r.get("record_type") == "dest_join"]
    origin_table_records = [r for r in records if r.get("record_type") == "origin_table"]
    ref_table_records = [r for r in records if r.get("record_type") == "ref_table"]
    scope_records = [
        r for r in records
        if r.get("record_type", "").endswith(("_schema", "_data_source"))
    ]
    concepts = [
        r for r in records
        if r.get("record_type") in ("origin_concept", "dest_concept")
    ]

    _validate_record_order(records, errors)

    # Exactly one meta record, first line.
    if len(metas) != 1:
        errors.append(f"Expected exactly one meta record, found {len(metas)}")
    elif records[0].get("record_type") != "meta":
        errors.append("The meta record must be the first line")
    if metas:
        _validate_meta(metas[0], errors)

    # Every completeness rule below is expressed per variable, so a file carrying none
    # satisfies them all vacuously -- a byte-stable resolution that accounts for
    # nothing. The contract is that a resolution accounts for the entire SAS input,
    # which no variable at all cannot do.
    if not variables:
        errors.append(
            "File carries no origin_sas_variable record; a resolution accounts for "
            "every variable of the SAS input, so it can never carry none"
        )

    # The system records follow the collapse rule: dest always, origin exactly when
    # the systems differ.
    _validate_system_records(system_records, metas[0] if metas else None, errors)

    # One dataset record per dataset, and every variable belongs to one.
    coordinates_by_dataset: dict[str, dict[str, Any]] = {}
    for record in dataset_records:
        _validate_dataset(record, errors)
        name = record.get("dataset")
        if name in coordinates_by_dataset:
            errors.append(f"Duplicate origin_sas_dataset record '{name}'")
        coordinates_by_dataset[name] = record
    for name in sorted({r.get("dataset") for r in variables} - set(coordinates_by_dataset), key=str):
        errors.append(f"Variables reference dataset '{name}', which has no origin_sas_dataset record")

    seen_dest_tables = set()
    for record in dest_table_records:
        _validate_table_record(record, "dest_table", errors)
        table_id = record.get("table_id")
        if table_id in seen_dest_tables:
            errors.append(f"Duplicate dest_table record '{table_id}'")
        seen_dest_tables.add(table_id)

    seen_origin_tables = set()
    for record in origin_table_records:
        _validate_table_record(record, "origin_table", errors)
        table_id = record.get("table_id")
        if table_id in seen_origin_tables:
            errors.append(f"Duplicate origin_table record '{table_id}'")
        seen_origin_tables.add(table_id)

    seen_ref_tables = set()
    for record in ref_table_records:
        _validate_table_record(record, "ref_table", errors)
        table_id = record.get("table_id")
        if table_id in seen_ref_tables:
            errors.append(f"Duplicate ref_table record '{table_id}'")
        seen_ref_tables.add(table_id)

    seen_scope = set()
    for record in scope_records:
        _validate_scope_record(record, errors)
        key = (record.get("record_type"), record.get("schema_id") or record.get("data_source_id"))
        if key in seen_scope:
            errors.append(f"Duplicate {key[0]} record '{key[1]}'")
        seen_scope.add(key)

    for record in variables:
        _validate_variable(record, coordinates_by_dataset.get(record.get("dataset")) or {}, errors)

    # Derive the SAS parents, the dest tables, the referenced dest columns, and the
    # concept scope. The dest tables are what the converted code reads, resolved per
    # dataset: a dataset with dest_data_scope contributes its candidates' target
    # tables, one without contributes its own origin tables. Both can occur in one run.
    sas_parents: set[str] = set()
    referenced_columns: set[str] = set()
    dest_tables: set[str] = set()
    objects: set[str] = set()
    ref_table_pointers: set[str] = set()
    for variable in variables:
        dataset_record = coordinates_by_dataset.get(variable.get("dataset")) or {}
        in_transition = bool(dataset_record.get("dest_data_scope"))
        for column in variable.get("origin_columns") or []:
            if not isinstance(column, dict):
                continue
            table_id = column.get("table_id")
            if isinstance(table_id, str):
                sas_parents.add(table_id)
                objects.add(table_id)
                objects |= _ltree_prefixes(table_id)
                if not in_transition:
                    dest_tables.add(table_id)
            ocid = column.get("origin_column_id")
            if isinstance(ocid, str):
                objects.add(ocid)
            if isinstance(column.get("ref_table_id"), str):
                ref_table_pointers.add(column["ref_table_id"])
            for cand in column.get("candidates") or []:
                if not isinstance(cand, dict) or cand.get("target_expression") is None:
                    continue
                # Guard the container once: a non-list value (already reported by
                # _validate_candidate) would otherwise iterate as characters and seed
                # phantom single-character table ids across every derived set.
                cand_tables = cand.get("target_tables_referenced")
                for table in cand_tables if isinstance(cand_tables, list) else []:
                    if isinstance(table, str):
                        dest_tables.add(table)
                        objects.add(table)
                        objects |= _ltree_prefixes(table)
                referenced_columns |= _dest_columns(cand.get("target_expression"), cand_tables)

    # The dest joins' condition columns are read by the generated join, so they are
    # referenced dest columns -- and touched objects -- like the expressions' own.
    for join in dest_joins:
        referenced_columns |= _dest_columns(
            join.get("join_condition"), [join.get("table_a_id"), join.get("table_b_id")]
        )
    # The dest tables' primary keys are the third collection source: grain columns
    # get read in practice, and the key list is derivable in-file from the
    # dest_table records themselves.
    for record in dest_table_records:
        table_id = record.get("table_id")
        keys = record.get("primary_key_columns")
        if isinstance(table_id, str) and isinstance(keys, list):
            for key_name in keys:
                if isinstance(key_name, str) and key_name:
                    referenced_columns.add(f"{table_id}.{key_name}")
    objects |= referenced_columns

    # key=str throughout the set-difference reports: a record missing its id
    # contributes None, which must sort into the error list, not raise TypeError.
    # Every dest table needs a dest_table record, and nothing else may have one.
    for table_id in sorted(dest_tables - seen_dest_tables, key=str):
        errors.append(f"Dest table '{table_id}' has no dest_table record")
    for table_id in sorted(seen_dest_tables - dest_tables, key=str):
        errors.append(f"dest_table record '{table_id}' is not among the dest tables")

    # The origin_table set matches the pairing rule: every SAS parent that is not a
    # dest table, plus -- when the systems differ -- every parent that is (the
    # id-matched pair carrying both addresses).
    meta = metas[0] if metas else {}
    systems_differ = meta.get("origin_system") != meta.get("dest_system")
    expected_origin = {t for t in sas_parents if t not in dest_tables}
    if systems_differ:
        expected_origin |= {t for t in sas_parents if t in dest_tables}
    for table_id in sorted(expected_origin - seen_origin_tables, key=str):
        errors.append(
            f"SAS parent '{table_id}' has no origin_table record "
            f"(the pairing rule requires one)"
        )
    for table_id in sorted(seen_origin_tables - expected_origin, key=str):
        errors.append(
            f"origin_table record '{table_id}' is not required by the pairing rule "
            f"(not a SAS parent, or a dest table under equal systems)"
        )

    # Every column a surviving expression, a dest join, or a dest table's key list
    # references has exactly one dest_column record -- the same grain the origin side
    # is documented at.
    seen_dest_columns: set[str] = set()
    for record in dest_column_records:
        cid = record.get("column_id")
        label = f"Dest column '{cid}'"
        check_fields(label, record, DEST_COLUMN_FIELDS, errors)
        require_nonempty_str(label, record, "data_type", errors)
        for field in ("is_nullable", "is_primary_key"):
            if not isinstance(record.get(field), bool):
                errors.append(f"{label}: '{field}' must be a boolean")
        if check_segments(label, cid, COLUMN_ID_SEGMENTS, "column_id", errors):
            segments = cid.split(".")
            parent = ".".join(segments[:TABLE_ID_SEGMENTS])
            table_id = record.get("table_id")
            if isinstance(table_id, str) and parent != table_id:
                errors.append(
                    f"{label}: table_id '{table_id}' is not the leading "
                    f"{TABLE_ID_SEGMENTS} segments of column_id"
                )
            if parent not in dest_tables:
                errors.append(f"{label}: its table '{parent}' is not a dest table")
        if isinstance(record.get("ref_table_id"), str):
            ref_table_pointers.add(record["ref_table_id"])
        if cid in seen_dest_columns:
            errors.append(f"Duplicate dest_column record '{cid}'")
        seen_dest_columns.add(cid)
    for cid in sorted(referenced_columns - seen_dest_columns, key=str):
        errors.append(f"Referenced dest column '{cid}' has no dest_column record")
    for cid in sorted(seen_dest_columns - referenced_columns, key=str):
        errors.append(
            f"dest_column record '{cid}' is not referenced by any surviving "
            f"expression, dest join, or dest table's key list"
        )

    # Every non-null ref_table_id -- on an origin column or a dest column -- has
    # exactly one ref_table record, and no ref_table publishes without a pointer.
    for table_id in sorted(ref_table_pointers - seen_ref_tables, key=str):
        errors.append(f"Referenced code set '{table_id}' has no ref_table record")
    for table_id in sorted(seen_ref_tables - ref_table_pointers, key=str):
        errors.append(f"ref_table record '{table_id}' is pointed at by no ref_table_id")

    _validate_joins(dest_joins, dest_tables, "dest_join", errors)
    _validate_joins(origin_joins, sas_parents, "origin_join", errors)

    # dest_join takes precedence: origin and dest table sets can overlap (a mapping
    # may target a table in its own data source), so the same relationship must not be
    # emitted under both types.
    def _key(record: dict[str, Any]) -> tuple[Any, Any, Any]:
        return (record.get("table_a_id"), record.get("table_b_id"), record.get("relationship_name"))

    dest_keys = {_key(r) for r in dest_joins}
    for record in origin_joins:
        if _key(record) in dest_keys:
            errors.append(
                f"origin_join {_key(record)} is already a dest_join; dest takes precedence"
            )
    # Origin joins describe how the SAS input was assembled, so they only apply when
    # the converted code reads something else. With no dataset in transition the SAS
    # parents are themselves the dest tables, and dest_join covers them.
    if origin_joins and not any(r.get("dest_data_scope") for r in dataset_records):
        errors.append(
            "origin_join records require at least one dataset with a dest_data_scope "
            "transition; without one the SAS parents are the dest tables"
        )

    for record in ref_table_records:
        rid = record.get("table_id")
        if isinstance(rid, str):
            objects.add(rid)
            objects |= _ltree_prefixes(rid)
    # The dest-side objects, mirroring build_concept_records in resolve_schema.py: the
    # dest tables and the code sets with their data-source and schema prefixes, plus the
    # referenced dest columns. An object in play but not here appears only on the origin
    # side, which is what decides a concept's side.
    dest_objects: set[str] = {t for t in dest_tables if isinstance(t, str)}
    dest_objects |= {t for t in seen_ref_tables if isinstance(t, str)}
    dest_objects |= {c for c in referenced_columns if isinstance(c, str)}
    for table_id in list(dest_tables) + list(seen_ref_tables):
        if isinstance(table_id, str):
            dest_objects |= _ltree_prefixes(table_id)
    _validate_concepts(concepts, objects, dest_objects, errors)

    # No duplicate (dataset, variable) among output variable records.
    seen_vars: dict[str, set[str]] = defaultdict(set)
    for record in variables:
        dataset = record.get("dataset", "")
        variable = record.get("variable", "")
        if variable in seen_vars[dataset]:
            errors.append(f"Duplicate origin_sas_variable record '{variable}' in dataset '{dataset}'")
        seen_vars[dataset].add(variable)

    if input_schema is not None:
        errors.extend(
            _validate_against_input(
                variables, dataset_records, metas[0] if metas else None, input_schema
            )
        )

    return errors


def _validate_resolved_coordinates(
    meta: dict[str, Any] | None,
    dataset_records: list[dict[str, Any]],
    input_records: list[dict[str, Any]],
    errors: list[str],
) -> None:
    """Cross-check the meta and dataset records against the input inventory.

    `meta` passes through unchanged, while each `origin_sas_dataset` states the data
    scopes that actually applied: the input dataset record's value when the key is
    present, else the `meta` default. The systems are meta-only, so a dataset record
    never restates them. Also confirms `filepath` was carried through.

    Args:
        meta: The output meta record, or None when absent.
        dataset_records: The output origin_sas_dataset records.
        input_records: The parsed input inventory.
        errors: List of error messages, appended to in place.
    """
    input_metas = [r for r in input_records if r.get("record_type") == "meta"]
    if not input_metas:
        errors.append("Input inventory has no meta record")
        return
    input_meta = input_metas[0]
    input_by_dataset = {
        r.get("dataset"): r for r in input_records if r.get("record_type") == "origin_sas_dataset"
    }

    # meta is the declared process-wide default, carried through verbatim.
    if meta is not None:
        for field in ("process_name", "origin_system", "dest_system", "origin_data_scope", "dest_data_scope"):
            expected = input_meta.get(field)
            if meta.get(field) != expected:
                errors.append(
                    f"Meta record: '{field}' is {meta.get(field)!r}, but the inventory "
                    f"declares {expected!r}; meta is carried through unchanged"
                )

    output_names = {r.get("dataset") for r in dataset_records}
    for name in sorted(input_by_dataset.keys() - output_names, key=str):
        errors.append(f"Input dataset '{name}' has no output origin_sas_dataset record")
    for name in sorted(output_names - input_by_dataset.keys(), key=str):
        errors.append(f"Output dataset '{name}' is not in the input inventory")

    for record in dataset_records:
        name = record.get("dataset")
        source = input_by_dataset.get(name)
        if source is None:
            continue
        if record.get("filepath") != source.get("filepath"):
            errors.append(
                f"Dataset record '{name}': filepath is {record.get('filepath')!r}, "
                f"input has {source.get('filepath')!r}"
            )
        for field in ("origin_data_scope", "dest_data_scope"):
            expected = source[field] if field in source else input_meta.get(field)
            if record.get(field) != expected:
                errors.append(
                    f"Dataset record '{name}': resolved '{field}' is {record.get(field)!r}, "
                    f"but the inventory resolves to {expected!r}"
                )


def _validate_against_input(
    variables: list[dict[str, Any]],
    dataset_records: list[dict[str, Any]],
    meta: dict[str, Any] | None,
    input_schema: Path,
) -> list[str]:
    """Cross-check output records against the input inventory.

    Args:
        variables: The output origin_sas_variable records.
        dataset_records: The output origin_sas_dataset records.
        meta: The output meta record, or None when absent.
        input_schema: Path to the input_schema.jsonl from sas-variable-extraction.

    Returns:
        List of coverage, coordinate, and carry-through error messages.
    """
    errors: list[str] = []
    logger.info(f"Cross-checking against input inventory: {input_schema}")

    input_records = [record for _, record in load_jsonl(input_schema, errors)]
    if not input_records:
        # Load failed or the inventory is empty; the load error (if any) is recorded.
        # Skip the set-difference checks, which would otherwise flag every output variable.
        return errors

    _validate_resolved_coordinates(meta, dataset_records, input_records, errors)

    input_by_pair = {
        (r.get("dataset"), r.get("variable")): r
        for r in input_records
        if r.get("record_type") == "origin_sas_variable"
    }
    output_by_pair = {(r.get("dataset"), r.get("variable")): r for r in variables}

    for pair in sorted(input_by_pair.keys() - output_by_pair.keys(), key=str):
        errors.append(f"Input variable {pair} has no output origin_sas_variable record")
    for pair in sorted(output_by_pair.keys() - input_by_pair.keys(), key=str):
        errors.append(f"Output variable {pair} is not in the input inventory")

    # Carried SAS metadata must match the input exactly.
    for pair in sorted(input_by_pair.keys() & output_by_pair.keys(), key=str):
        inp, outp = input_by_pair[pair], output_by_pair[pair]
        for field in CARRIED_FIELDS:
            if inp.get(field) != outp.get(field):
                errors.append(
                    f"Variable {pair}: carried '{field}' is {outp.get(field)!r}, input has {inp.get(field)!r}"
                )

    return errors


def main() -> None:
    """Run output validation on input_schema_resolution.jsonl."""
    parser = argparse.ArgumentParser(description="Validate input_schema_resolution.jsonl output")
    parser.add_argument("--input-data", type=Path, required=True, help="Path to input_schema_resolution.jsonl")
    parser.add_argument(
        "--input-schema",
        type=Path,
        default=None,
        help="Optional path to the input_schema.jsonl for coverage and carry-through cross-checks",
    )
    args = parser.parse_args()

    setup_logging(log_dir="logs/sas_parsing/data_validation")
    logger.info("=" * 60)

    try:
        errors = validate_schema_resolution(args.input_data, args.input_schema)
    except Exception as e:
        logger.error(f"Validation aborted: {e}")
        logger.info("=" * 60)
        sys.exit(1)

    if errors:
        for error in errors:
            logger.error(f"VALIDATION FAILED: {error}")
        logger.info(f"Validation completed with {len(errors)} error(s)")
        logger.info("=" * 60)
        sys.exit(1)
    else:
        logger.info("VALIDATION PASSED: All checks passed")
        logger.info("=" * 60)


if __name__ == "__main__":
    main()
