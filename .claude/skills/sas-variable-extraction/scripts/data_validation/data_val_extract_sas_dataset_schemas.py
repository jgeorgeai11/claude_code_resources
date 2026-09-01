# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "python-json-logger",
# ]
# ///
"""Validate the input_schema.jsonl / output_schema.jsonl outputs from extract_sas_dataset_schemas.py.

The two inventory kinds are distinct contracts sharing their record shapes:

  - input_schema.jsonl (input inventory) — validate_input_schemas. The meta record
    may carry the process-level catalog coordinates, and origin_sas_dataset records
    may carry data-scope overrides — a non-empty list of ltree prefixes wherever one
    appears — but never a system field: systems are process-wide, meta-only.
  - output_schema.jsonl (outputs inventory) — validate_output_schemas. No record
    carries any catalog coordinate: outputs are inventoried, not resolved, so the
    meta record holds process_name alone and origin_sas_dataset records hold exactly
    the SAS identity (dataset, filepath). Enforcing the absence keeps the two
    inventory kinds impossible to confuse.

Variable-record rules are identical on both sides: required fields, a valid SAS
type, a matching origin_sas_dataset record, and no duplicates within a dataset.
Dataset records are likewise unique by name on both sides.

Required fields are checked for a usable value, not only for their presence: the
resolution validator downstream requires non-empty strings and an integer length of
the same carried fields, so a type or blanking regression fails here rather than
publishing an inventory that fails one step later.
"""

import sys
import json
import argparse
from pathlib import Path
from typing import Any
from collections import defaultdict

# logconfig ships beside the extractor, one level up, and travels with the move
# bundle. Resolve against this file, never the cwd, so it imports from any working
# directory.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
from logconfig import setup_logging, get_logger

logger = get_logger(__name__)

RECORD_TYPES = {"meta", "origin_sas_dataset", "origin_sas_variable"}
META_FIELDS = {"process_name"}
DATASET_FIELDS = {"dataset", "filepath"}
# Systems are process-wide, declared on the meta record alone: a dataset record
# carrying one is the inventory-side mirror of the resolution validator's rule, so a
# regression in the extractor fails validation rather than publishing.
DATASET_FORBIDDEN_FIELDS = {"origin_system", "dest_system"}
# record_type is in none of the field sets: it is what partitions the records, so a
# record reaching these checks has it by construction and the structural check is its
# sole enforcement.
VARIABLE_FIELDS = {"dataset", "variable", "type", "format", "length", "label"}
VALID_TYPES = {"char", "num"}
# The scope overrides an input dataset record may carry, and the two coordinates the
# input meta declares as lists rather than plain strings.
DATA_SCOPE_FIELDS = ("origin_data_scope", "dest_data_scope")

# The outputs inventory carries no catalog coordinates anywhere — nothing in it is
# being resolved, and where the interface tables land in the catalog is the
# conversion plan's decision, not an extraction fact.
COORDINATE_FIELDS = {"origin_system", "dest_system", "origin_data_scope", "dest_data_scope"}
OUTPUT_DATASET_ALLOWED_FIELDS = {"record_type"} | DATASET_FIELDS


def _load_records(input_data: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Load JSONL records from a file, collecting parse errors.

    Args:
        input_data: Path to the JSONL file.

    Returns:
        Tuple of (records, errors). A non-empty errors list from a missing or
        unreadable file means validation cannot proceed.
    """
    errors: list[str] = []

    if not input_data.exists():
        return [], [f"File not found: {input_data}"]

    records = []
    try:
        with open(input_data, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as e:
                    errors.append(f"Line {line_num}: Invalid JSON - {e}")
                    continue
                # A valid-JSON non-object (an array, a bare string) would reach the
                # structural checks and crash them on record.get(): report and drop it,
                # so the declared list[dict] return holds for every later check.
                if not isinstance(record, dict):
                    errors.append(
                        f"Line {line_num}: Expected a JSON object, got {type(record).__name__}"
                    )
                    continue
                records.append(record)
    except OSError as e:
        return [], [f"Failed to read file: {e}"]

    return records, errors


def _check_non_empty_string(
    record: dict[str, Any], field: str, label: str, errors: list[str]
) -> None:
    """Append an error when a field that is present holds no usable string.

    A key-presence check passes a present-but-worthless value -- a null, a blank
    string, a number where a name belongs -- and the resolution validator downstream
    requires a non-empty string for these same carried fields, so a blanking or
    retyping regression in the extractor would publish here and fail one step later.
    A field absent altogether is the caller's missing-field error, reported there and
    not repeated here.

    Args:
        record: The record holding the field.
        field: The field name to check.
        label: Human-readable identifier for the record, opening the message.
        errors: List of error messages, appended to in place.
    """
    if field not in record:
        return
    value = record[field]
    if not isinstance(value, str) or not value:
        errors.append(f"{label}: '{field}' must be a non-empty string")


def _check_data_scope(record: dict[str, Any], label: str, errors: list[str]) -> None:
    """Append an error when a data scope is not a non-empty list of ltree prefixes.

    Checked only where a scope may legally appear -- the input meta, which declares
    the process-level coordinates, and its dataset records, which may narrow them.
    The outputs contract rejects the field's presence outright, so it never gets here.
    Absence is how a scope is omitted (the extractor writes the field or nothing, never
    a null), so only a field that is present is examined.

    Shape alone is checked, not whether the prefixes exist: this script never reads
    metadata_db. The resolution validator requires exactly this shape of these fields,
    so a bare string here -- `"ocs.non_institutional"` where `["ocs.non_institutional"]`
    belongs -- is an
    inventory the resolver would reject.

    Args:
        record: The meta or origin_sas_dataset record.
        label: Human-readable identifier for the record, opening the message.
        errors: List of error messages, appended to in place.
    """
    for field in DATA_SCOPE_FIELDS:
        if field not in record:
            continue
        value = record[field]
        if not isinstance(value, list) or not value:
            errors.append(f"{label}: '{field}' must be a non-empty list of ltree prefixes")
        elif any(not isinstance(entry, str) or not entry for entry in value):
            errors.append(f"{label}: '{field}' entries must be non-empty strings")


def _check_structure(
    records: list[dict[str, Any]], errors: list[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Run the structural checks shared by both inventory kinds.

    Checks every record has a valid record_type, and that there is exactly one meta
    record, first in the file, carrying the required meta fields.

    Args:
        records: The loaded records.
        errors: The error list to append to (mutated in place).

    Returns:
        Tuple of (metas, dataset_records, variables) partitioned by record_type.
    """
    for i, record in enumerate(records):
        if record.get("record_type") not in RECORD_TYPES:
            errors.append(f"Record {i + 1}: Invalid or missing record_type '{record.get('record_type')}'")

    metas = [r for r in records if r.get("record_type") == "meta"]
    dataset_records = [r for r in records if r.get("record_type") == "origin_sas_dataset"]
    variables = [r for r in records if r.get("record_type") == "origin_sas_variable"]

    if len(metas) != 1:
        errors.append(f"Expected exactly one meta record, found {len(metas)}")
    elif records[0].get("record_type") != "meta":
        errors.append("The meta record must be the first line")
    if metas:
        missing = META_FIELDS - set(metas[0].keys())
        if missing:
            errors.append(f"Meta record: Missing fields: {missing}")
        _check_non_empty_string(metas[0], "process_name", "Meta record", errors)

    return metas, dataset_records, variables


def _check_dataset_identity(record: dict[str, Any], label: str, errors: list[str]) -> None:
    """Run the SAS-identity checks both contracts make on a dataset record.

    The identity is required and each half of it has to be usable: everything
    downstream cites the dataset by name and reads it from the filepath.

    Args:
        record: The origin_sas_dataset record.
        label: Human-readable identifier for the record, opening the message.
        errors: List of error messages, appended to in place.
    """
    missing = DATASET_FIELDS - set(record.keys())
    if missing:
        errors.append(f"{label}: Missing fields: {missing}")
    # Sorted so a record failing on both halves reports them in the same order every
    # run: set iteration order varies with hash randomization.
    for field in sorted(DATASET_FIELDS):
        _check_non_empty_string(record, field, label, errors)


def _check_split_notation(record: dict[str, Any], errors: list[str]) -> None:
    """Append an error when a dataset record misstates the split notation.

    A dataset split across numbered files of identical shape carries a `*` in its
    `LIBNAME.DATASET` name and in its filename. Both matter and neither substitutes for
    the other: the name is what every variable record cites and what a downstream gap
    record names, while the filepath is what says which files the name stands for. Half
    the pair is a half-applied rename, and it reads as an ordinary single-file dataset
    from whichever side was left behind -- which is the one thing this notation exists
    to prevent.

    Two rules state that, because the extractor recognizes a split from the filename
    alone -- a `*` in a directory is never expanded, so it is looked up as that literal
    path and matches nothing. So a directory `*` is rejected outright, as the config
    parse rejects it, and the name is then paired against the basename. Neither rule
    substitutes for the other: pairing on the basename alone would newly accept a
    directory glob under a concrete name. The config gate means an extractor run can no
    longer emit either shape, but this validator runs standalone against any inventory,
    including a hand-edited or foreign one, so it is the last place they can enter.

    Args:
        record: The origin_sas_dataset record.
        errors: List of error messages, appended to in place.
    """
    name, filepath = record.get("dataset"), record.get("filepath")
    # A non-string on either side is a missing-field or shape error the caller reports.
    if not isinstance(name, str) or not isinstance(filepath, str):
        return
    # Both separators are honoured because the record carries the path as the SAS
    # environment saw it, which may be a POSIX or a Windows path.
    directory, _, basename = filepath.replace("\\", "/").rpartition("/")
    if "*" in directory:
        errors.append(
            f"Dataset record for '{name}': the filepath '{filepath}' carries a '*' "
            f"outside its filename; a split '*' marks files within one directory and "
            f"belongs in the filename alone — a directory is never expanded"
        )
    if ("*" in name) == ("*" in basename):
        return
    bare = "filepath" if "*" in name else "dataset"
    errors.append(
        f"Dataset record for '{name}': the split notation marks only one side — "
        f"'{bare}' carries no '*'; a split dataset marks both its name and its filepath"
    )


def _check_variables(
    variables: list[dict[str, Any]], dataset_names: set[Any], errors: list[str]
) -> None:
    """Run the variable-record checks shared by both inventory kinds.

    Required fields, a usable name and length, a valid SAS type, a matching dataset
    record, and no duplicate variable within a dataset.

    Args:
        variables: The variable records.
        dataset_names: The dataset names that have a dataset record.
        errors: The error list to append to (mutated in place).
    """
    for record in variables:
        label = f"Variable '{record.get('variable')}' in '{record.get('dataset')}'"
        missing = VARIABLE_FIELDS - set(record.keys())
        if missing:
            errors.append(f"{label}: Missing fields: {missing}")
        _check_non_empty_string(record, "variable", label, errors)
        # length is the SAS storage width in bytes, and the resolution validator
        # requires an int of it. bool is an int to isinstance but not a width, and a
        # quoted "8" is the shape a JSON round trip through a spreadsheet produces.
        if "length" in record and (
            not isinstance(record["length"], int) or isinstance(record["length"], bool)
        ):
            errors.append(f"{label}: 'length' must be an integer")
        if record.get("type") not in VALID_TYPES:
            errors.append(f"{label}: Invalid type '{record.get('type')}', expected {VALID_TYPES}")
        if record.get("dataset") not in dataset_names:
            errors.append(f"Variable '{record.get('variable')}': dataset '{record.get('dataset')}' has no origin_sas_dataset record")

    seen = defaultdict(set)
    for record in variables:
        # Keyed exactly as the orphan check above keys it, so a record missing the
        # field groups the same way in both passes.
        dataset = record.get("dataset")
        variable = record.get("variable", "")
        if variable in seen[dataset]:
            errors.append(f"Duplicate variable '{variable}' in dataset '{dataset}'")
        seen[dataset].add(variable)


def validate_input_schemas(input_data: Path) -> list[str]:
    """Run all validation checks on an input inventory (input_schema.jsonl).

    Args:
        input_data: Path to the input_schema.jsonl file.

    Returns:
        List of validation error messages. Empty list means all checks passed.
    """
    logger.info(f"Validating input inventory: {input_data}")

    records, errors = _load_records(input_data)
    if not records:
        if not errors:
            errors.append("File contains 0 records")
        return errors

    logger.info(f"Loaded {len(records)} records")

    metas, dataset_records, variables = _check_structure(records, errors)

    # The meta record declares the process-level coordinates, so its scopes are shaped
    # here; its systems are plain strings the resolver checks against the catalog.
    if metas:
        _check_data_scope(metas[0], "Meta record", errors)

    # Dataset records have the required fields, no system fields (systems are
    # meta-only; origin_data_scope / dest_data_scope overrides remain legal, and are
    # shaped like the meta's), and are unique by name — the mirror of the
    # duplicate-variable rule.
    dataset_names = set()
    for record in dataset_records:
        label = f"Dataset record for '{record.get('dataset')}'"
        _check_dataset_identity(record, label, errors)
        forbidden = DATASET_FORBIDDEN_FIELDS & set(record.keys())
        if forbidden:
            errors.append(
                f"{label}: carries system fields {forbidden}; "
                f"systems are process-wide and belong on the meta record alone"
            )
        _check_data_scope(record, label, errors)
        _check_split_notation(record, errors)
        if record.get("dataset") in dataset_names:
            errors.append(f"Duplicate dataset record '{record.get('dataset')}'")
        dataset_names.add(record.get("dataset"))

    _check_variables(variables, dataset_names, errors)

    return errors


def validate_output_schemas(input_data: Path) -> list[str]:
    """Run all validation checks on an outputs inventory (output_schema.jsonl).

    The outputs inventory is its own contract: exactly one meta record, first line,
    carrying process_name and no coordinate fields; dataset records carrying exactly
    dataset and filepath (no coordinates, no systems); variable rules identical to
    the input side's.

    Args:
        input_data: Path to the output_schema.jsonl file.

    Returns:
        List of validation error messages. Empty list means all checks passed.
    """
    logger.info(f"Validating outputs inventory: {input_data}")

    records, errors = _load_records(input_data)
    if not records:
        if not errors:
            errors.append("File contains 0 records")
        return errors

    logger.info(f"Loaded {len(records)} records")

    metas, dataset_records, variables = _check_structure(records, errors)

    # The meta record carries process_name alone — no catalog coordinates
    if metas:
        forbidden = COORDINATE_FIELDS & set(metas[0].keys())
        if forbidden:
            errors.append(
                f"Meta record: carries coordinate fields {forbidden}; an outputs "
                f"inventory takes no catalog coordinates — outputs are inventoried, "
                f"not resolved"
            )

    # Dataset records carry exactly the SAS identity (dataset and filepath) and are
    # unique by name — the mirror of the duplicate-variable rule.
    dataset_names = set()
    for record in dataset_records:
        label = f"Dataset record for '{record.get('dataset')}'"
        _check_dataset_identity(record, label, errors)
        extra = set(record.keys()) - OUTPUT_DATASET_ALLOWED_FIELDS
        if extra:
            errors.append(
                f"{label}: carries fields {extra}; "
                f"an outputs inventory's dataset records carry exactly dataset and "
                f"filepath — no coordinates, no systems"
            )
        _check_split_notation(record, errors)
        if record.get("dataset") in dataset_names:
            errors.append(f"Duplicate dataset record '{record.get('dataset')}'")
        dataset_names.add(record.get("dataset"))

    _check_variables(variables, dataset_names, errors)

    return errors


def main() -> None:
    """Validate an input_schema.jsonl or output_schema.jsonl inventory, applying the matching contract."""
    parser = argparse.ArgumentParser(description="Validate an input_schema.jsonl or output_schema.jsonl inventory")
    parser.add_argument("--input-data", type=Path, required=True, help="Path to the inventory file")
    parser.add_argument(
        "--kind",
        choices=("input", "output"),
        default=None,
        help=(
            "Which contract to apply: 'input' for input_schema.jsonl, 'output' for "
            "output_schema.jsonl. Inferred only from those exact filenames when "
            "omitted; any other filename requires this flag."
        ),
    )
    args = parser.parse_args()

    setup_logging(log_dir="logs/sas_parsing/data_validation")
    logger.info("=" * 60)

    input_path = args.input_data
    # The kind is inferred only from the two exact basenames. Any other name must
    # say which contract it means: a silent default would validate a differently
    # named outputs file against the wrong (input) contract and pass it.
    if args.kind:
        kind = args.kind
    elif input_path.name == "input_schema.jsonl":
        kind = "input"
    elif input_path.name == "output_schema.jsonl":
        kind = "output"
    else:
        logger.error(
            f"Cannot infer the inventory kind from filename '{input_path.name}': "
            f"only the exact basenames input_schema.jsonl and output_schema.jsonl "
            f"are inferred — pass --kind input or --kind output"
        )
        logger.info("=" * 60)
        sys.exit(1)
    logger.info(f"Applying the {kind}-inventory contract")
    validator = validate_output_schemas if kind == "output" else validate_input_schemas
    # An unanticipated failure (an unhashable field value reaching a set, say) is
    # reported and logged like any other validation failure rather than escaping as a
    # bare traceback with no closing separator.
    try:
        errors = validator(input_path)
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
