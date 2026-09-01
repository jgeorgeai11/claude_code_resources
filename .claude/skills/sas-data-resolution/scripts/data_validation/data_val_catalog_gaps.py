"""Validate the input_schema_catalog_gaps.jsonl work order the resolver writes on a gap failure.

The gaps file is a machine-readable catalog work order other tooling may consume --
a worklist, a documentation agent, a diff against the last run -- so its shape is
checked independently of the resolver that writes it: a malformed work order would
silently misdirect the catalog fix it exists to order.

Three record types, mirroring the three gap kinds: `missing_variable` (a SAS
variable matching no column under its dataset's origin_data_scope),
`missing_candidate` (an in-transition column with no usable mapping into
dest_data_scope), and `missing_deployment` (a table in play undeployed where the
process needs it; a null system is the nowhere-deployed code-set case).

The shape rules the sibling `data_val_schema_resolution.py` also enforces -- exact
field sets, dotted-id segment counts, scope-prefix rules, and the JSONL reader --
come from `jsonl_checks.py` so the two validators cannot drift apart on them.
"""

import sys
import json
import argparse
from pathlib import Path
from typing import Any

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

# Exact field sets per record type -- no more, no fewer. Fields referencing records
# in another file are named by the record type (origin_sas_dataset /
# origin_sas_variable), per the cross-skill name-consistency rule.
GAP_FIELDS = {
    "missing_variable": {"record_type", "origin_sas_dataset", "origin_sas_variable", "origin_data_scope"},
    "missing_candidate": {
        "record_type", "origin_sas_dataset", "origin_sas_variable", "origin_column_id", "dest_data_scope",
    },
    "missing_deployment": {"record_type", "table_id", "system"},
}

# Write order: the two variable-resolution kinds in the order the resolver collects
# them, then the deployment kind from its own later gate. A work order is diffed
# against the last run's, so it has exactly one legal serialization -- the same
# fixedness the resolution file rests on.
GAP_ORDER = ("missing_variable", "missing_candidate", "missing_deployment")

# Sort key per record type within its group (by identifying fields).
GAP_SORT_KEYS = {
    "missing_variable": lambda r: (r.get("origin_sas_dataset"), r.get("origin_sas_variable")),
    "missing_candidate": lambda r: (
        r.get("origin_sas_dataset"), r.get("origin_sas_variable"), r.get("origin_column_id"),
    ),
    # `or ""` on the system mirrors the resolver's own sort, where the nowhere-deployed
    # code-set case (a null system) sorts ahead of any named system for the same table.
    "missing_deployment": lambda r: (r.get("table_id"), r.get("system") or ""),
}

# Field names behind each sort key, for error messages.
_SORT_FIELDS = {
    "missing_variable": ("origin_sas_dataset", "origin_sas_variable"),
    "missing_candidate": ("origin_sas_dataset", "origin_sas_variable", "origin_column_id"),
    "missing_deployment": ("table_id", "system"),
}


def _validate_gap(line_num: int, record: dict[str, Any], errors: list[str]) -> None:
    """Validate one gap record's type, field set, and field values.

    Args:
        line_num: The record's 1-based physical line number in the file, for messages.
        record: The gap record.
        errors: List of error messages, appended to in place.
    """
    record_type = record.get("record_type")
    # A non-string record_type is reported rather than tested for membership: an
    # unhashable one would raise TypeError out of a validator whose job is to report it.
    if not isinstance(record_type, str) or record_type not in GAP_FIELDS:
        errors.append(f"Line {line_num}: Invalid or missing record_type '{record_type}'")
        return

    label = f"Line {line_num} ({record_type})"
    check_fields(label, record, GAP_FIELDS[record_type], errors)

    if record_type == "missing_variable":
        for field in ("origin_sas_dataset", "origin_sas_variable"):
            require_nonempty_str(label, record, field, errors)
        _check_scope_list(label, record, "origin_data_scope", errors)
    elif record_type == "missing_candidate":
        for field in ("origin_sas_dataset", "origin_sas_variable"):
            require_nonempty_str(label, record, field, errors)
        check_segments(
            label, record.get("origin_column_id"), COLUMN_ID_SEGMENTS, "origin_column_id", errors
        )
        _check_scope_list(label, record, "dest_data_scope", errors)
    elif record_type == "missing_deployment":
        check_segments(label, record.get("table_id"), TABLE_ID_SEGMENTS, "table_id", errors)
        # A null system is the nowhere-deployed code-set case; anything else must name
        # the system the table is missing from.
        # "" (not None) is the missing sentinel here, since null is a legal value.
        system = record.get("system", "")
        if system is not None and (not isinstance(system, str) or not system):
            errors.append(f"{label}: 'system' must be a non-empty string or null")
    else:
        errors.append(f"{label}: No validator implemented for record_type '{record_type}'")


def _check_scope_list(label: str, record: dict[str, Any], field: str, errors: list[str]) -> None:
    """Append errors unless a field is a non-empty list of ltree prefixes.

    Every scope field on a gap record is required, so the list-ness rule lives here
    while the entry and duplicate rules come from the shared checks.

    Args:
        label: Human-readable identifier for the record.
        record: The record holding the field.
        field: The scope field name (origin_data_scope / dest_data_scope).
        errors: List of error messages, appended to in place.
    """
    value = record.get(field)
    if not isinstance(value, list) or not value:
        errors.append(f"{label}: '{field}' must be a non-empty list of ltree prefixes")
        return
    check_scope_entries(label, field, value, errors)


def _validate_gap_order(records: list[tuple[int, dict[str, Any]]], errors: list[str]) -> None:
    """Validate that gap records are grouped by type in write order and sorted within groups.

    The module docstring names a diff against the last run as a consumer of this file,
    which holds only if there is one legal serialization; the resolver emits one, and
    this is what keeps that a checked contract rather than a coincidence.

    Args:
        records: The (line number, record) pairs, in file order.
        errors: List of error messages, appended to in place.
    """
    rank = {name: i for i, name in enumerate(GAP_ORDER)}
    # Only string types are ranked: a non-string record_type is reported per record,
    # and testing an unhashable one for membership would raise.
    ranks = [
        rank[record["record_type"]]
        for _, record in records
        if isinstance(record.get("record_type"), str) and record["record_type"] in rank
    ]
    if ranks != sorted(ranks):
        errors.append("Records must be grouped by type in write order: " + ", ".join(GAP_ORDER))
        return

    for name, key in GAP_SORT_KEYS.items():
        group = [r for _, r in records if r.get("record_type") == name]
        # Coerced to strings: a non-scalar id is reported by the per-record checks, and
        # sorting mixed types here would raise instead.
        keys = [tuple(str(part) for part in key(record)) for record in group]
        if keys != sorted(keys):
            errors.append(f"'{name}' records must be sorted by {', '.join(_SORT_FIELDS[name])}")


def validate_catalog_gaps(input_data: Path) -> list[str]:
    """Run all validation checks on an input_schema_catalog_gaps.jsonl work order.

    Args:
        input_data: Path to the input_schema_catalog_gaps.jsonl file to validate.

    Returns:
        List of validation error messages. Empty list means all checks passed.
    """
    errors: list[str] = []

    logger.info(f"Validating: {input_data}")
    records = load_jsonl(input_data, errors)
    logger.info(f"Loaded {len(records)} records")

    # An empty work order must never accompany a failed run: the file exists exactly
    # because at least one gap failed the resolution.
    if not records:
        if not errors:
            errors.append("File contains 0 records; an empty work order must never accompany a failed run")
        return errors

    _validate_gap_order(records, errors)

    seen: set[str] = set()
    for line_num, record in records:
        _validate_gap(line_num, record, errors)
        key = json.dumps(record, sort_keys=True)
        if key in seen:
            errors.append(f"Line {line_num}: duplicate gap record {record}")
        seen.add(key)

    # The two failure stages never share a file: variable/mapping gaps fail the run
    # before deployment is ever resolved, so a work order mixing missing_deployment
    # with the other kinds means the writer is broken (skill readme, Step 9 gate).
    types_present = {
        r["record_type"] for _, r in records if isinstance(r.get("record_type"), str)
    }
    if "missing_deployment" in types_present and types_present & {"missing_variable", "missing_candidate"}:
        errors.append(
            "missing_deployment records must not share a work order with "
            "missing_variable/missing_candidate records: variable-resolution gaps "
            "fail the run before deployment is resolved"
        )

    return errors


def main() -> None:
    """Run output validation on input_schema_catalog_gaps.jsonl."""
    parser = argparse.ArgumentParser(description="Validate an input_schema_catalog_gaps.jsonl work order")
    parser.add_argument("--input-data", type=Path, required=True, help="Path to input_schema_catalog_gaps.jsonl")
    args = parser.parse_args()

    setup_logging(log_dir="logs/sas_parsing/data_validation")
    logger.info("=" * 60)

    try:
        errors = validate_catalog_gaps(args.input_data)
    except Exception as e:
        logger.error(f"Validation aborted for {args.input_data}: {e}")
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
