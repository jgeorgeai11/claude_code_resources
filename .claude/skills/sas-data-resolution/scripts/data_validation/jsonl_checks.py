"""Record checks shared by the sas-data-resolution JSONL validators.

`data_val_schema_resolution.py` and `data_val_catalog_gaps.py` check two different
files against one contract: the same exact-field-set rule, the same dotted-id
segment counts, the same ltree scope-prefix rules, and the same JSONL reader. Those
rules lived in both modules as byte-identical copies and had already drifted once (a
duplicate scope entry failed one gate and passed the other), so they live here once
and both validators call them.

Nothing here knows about either file's record types: a caller supplies the label it
wants errors reported under and the field set the record must carry. Every check
appends to an error list rather than raising, because reporting a malformed record is
what these validators exist to do.
"""

import json
from pathlib import Path
from typing import Any

# Ids are system-free, so a table id is 3 segments
# ({data_source}.{schema}.{table}) and a column id is 4.
TABLE_ID_SEGMENTS = 3
COLUMN_ID_SEGMENTS = 4
# The same 3 as a table id, but a different rule: a scope prefix names a data source,
# a schema, or a table, so 3 is its upper bound rather than its exact length.
MAX_SCOPE_SEGMENTS = 3


def check_fields(label: str, record: dict[str, Any], allowed: set[str], errors: list[str]) -> None:
    """Append errors for any missing or unexpected keys against an exact field set.

    Args:
        label: Human-readable identifier for the record, used in error messages.
        record: The record whose keys are checked.
        allowed: The exact set of keys the record must have -- no more, no fewer.
        errors: List of error messages, appended to in place.
    """
    keys = set(record.keys())
    missing = allowed - keys
    unexpected = keys - allowed
    # Sets are interpolated in sorted order: their iteration order varies with hash
    # randomization across runs, which would make identical failures diff differently.
    if missing:
        errors.append(f"{label}: Missing fields: {sorted(missing)}")
    if unexpected:
        errors.append(f"{label}: Unexpected fields: {sorted(unexpected)}")


def require_nonempty_str(label: str, record: dict[str, Any], field: str, errors: list[str]) -> None:
    """Append an error if a field is not a non-empty string.

    Args:
        label: Human-readable identifier for the record.
        record: The record holding the field.
        field: The field name to check.
        errors: List of error messages, appended to in place.
    """
    value = record.get(field)
    if not isinstance(value, str) or not value:
        errors.append(f"{label}: '{field}' must be a non-empty string")


def check_segments(label: str, value: object, expected: int, what: str, errors: list[str]) -> bool:
    """Append an error unless value is a dotted id with the expected non-empty segment count.

    Args:
        label: Human-readable identifier for the enclosing record.
        value: The candidate id.
        expected: Required number of dot-separated segments.
        what: What the id is, for the error message.
        errors: List of error messages, appended to in place.

    Returns:
        True when the id is well-formed.
    """
    if not isinstance(value, str) or not value:
        errors.append(f"{label}: {what} must be a non-empty string")
        return False
    # Every segment must be non-empty: a count alone would pass "a..b" and "a.b.",
    # which name no catalog object the id is supposed to address.
    segments = value.split(".")
    if len(segments) != expected or not all(segments):
        errors.append(f"{label}: {what} '{value}' must have {expected} non-empty segments")
        return False
    return True


def check_scope_entries(label: str, field: str, value: list[Any], errors: list[str]) -> None:
    """Append errors for any malformed or repeated entry in a data-scope list.

    The list-ness of the field itself is the caller's rule -- `origin_data_scope` is
    always required while `dest_data_scope` may be null -- so this takes a list and
    checks only what every scope list must satisfy.

    Args:
        label: Human-readable identifier for the record.
        field: The scope field name (origin_data_scope / dest_data_scope), for messages.
        value: The scope list's entries.
        errors: List of error messages, appended to in place.
    """
    for entry in value:
        if not isinstance(entry, str) or not entry:
            errors.append(f"{label}: '{field}' entries must be non-empty strings")
            continue
        # A prefix is bounded above but not below, so the count and the emptiness are
        # separate faults: "a..b" is within the bound yet matches no ltree path.
        segments = entry.split(".")
        if len(segments) > MAX_SCOPE_SEGMENTS:
            errors.append(
                f"{label}: '{field}' entry '{entry}' has more than {MAX_SCOPE_SEGMENTS} segments"
            )
        elif not all(segments):
            errors.append(f"{label}: '{field}' entry '{entry}' must have non-empty segments")
    # Scope lists are sets, so a repeated entry is meaningless. Only the string entries
    # are counted: an unhashable entry is already reported above, and folding it into a
    # set here would raise instead of reporting.
    entries = [entry for entry in value if isinstance(entry, str)]
    if len(set(entries)) != len(entries):
        errors.append(f"{label}: '{field}' has duplicate entries; scope lists are sets")


def load_jsonl(input_data: Path, errors: list[str]) -> list[tuple[int, dict[str, Any]]]:
    """Read a JSONL file into (line number, record) pairs, recording parse errors.

    The physical line number travels with every record so that each of a validator's
    messages means the same thing: numbering records by their position among the parsed
    ones would point a reader at the wrong line whenever a blank or malformed line
    precedes a bad record.

    Args:
        input_data: Path to the JSONL file.
        errors: List of error messages, appended to in place.

    Returns:
        One (1-based physical line number, record) pair per object line; empty if the
        file is missing or unreadable. Non-object lines are recorded as errors rather
        than returned, so every downstream `record.get(...)` is safe.
    """
    if not input_data.exists():
        errors.append(f"File not found: {input_data}")
        return []

    records: list[tuple[int, dict[str, Any]]] = []
    try:
        with input_data.open("r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError as e:
                    errors.append(f"Line {line_num}: Invalid JSON - {e}")
                    continue
                if not isinstance(parsed, dict):
                    errors.append(
                        f"Line {line_num}: Record must be a JSON object, got {type(parsed).__name__}"
                    )
                    continue
                records.append((line_num, parsed))
    except OSError as e:
        errors.append(f"Failed to read file: {e}")
    return records
