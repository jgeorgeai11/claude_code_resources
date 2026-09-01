"""Tests for the catalog-gaps work-order checks.

The gaps file is a machine-readable work order other tooling may consume, so its
validator is exercised on its own: record types, per-type field sets, value shapes,
the null-system rule, the write order every message and diff rests on, and the
no-empty / no-duplicate rules.
"""

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data_validation"))

import data_val_catalog_gaps
from data_val_catalog_gaps import main as validator_main
from data_val_catalog_gaps import validate_catalog_gaps


MISSING_VARIABLE = {
    "record_type": "missing_variable",
    "origin_sas_dataset": "SRCLIB.OCS_CLAIMS",
    "origin_sas_variable": "derived_flag",
    "origin_data_scope": ["ocs.non_institutional"],
}
MISSING_CANDIDATE = {
    "record_type": "missing_candidate",
    "origin_sas_dataset": "SRCLIB.OCS_CLAIMS",
    "origin_sas_variable": "clm_type",
    "origin_column_id": "ocs.non_institutional.clm.clm_type",
    "dest_data_scope": ["edwc_prd.claims_vw_prd"],
}
MISSING_DEPLOYMENT = {
    "record_type": "missing_deployment",
    "table_id": "edwc_prd.claims_vw_prd.v_clm",
    "system": "edw",
}


def _write(tmp_path: Path, records: list[dict[str, Any]]) -> Path:
    """Write gap records to a JSONL file for validation.

    Args:
        tmp_path: The pytest temporary directory.
        records: The gap records to write.

    Returns:
        The path written.
    """
    path = tmp_path / "input_schema_catalog_gaps.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def test_well_formed_variable_stage_work_order_passes(tmp_path: Path) -> None:
    """A variable-resolution work order (missing_variable + missing_candidate) validates cleanly."""
    path = _write(tmp_path, [MISSING_VARIABLE, MISSING_CANDIDATE])

    assert validate_catalog_gaps(path) == []


def test_well_formed_deployment_stage_work_order_passes(tmp_path: Path) -> None:
    """A deployment-stage work order validates cleanly on its own."""
    path = _write(tmp_path, [MISSING_DEPLOYMENT])

    assert validate_catalog_gaps(path) == []


def test_mixed_stage_work_order_is_rejected(tmp_path: Path) -> None:
    """Deployment gaps never share a file with variable/mapping gaps.

    Variable-resolution gaps fail the run before deployment is ever resolved, so a
    mixed work order means the writer is broken.
    """
    errors = validate_catalog_gaps(_write(tmp_path, [MISSING_VARIABLE, MISSING_DEPLOYMENT]))

    assert any("must not share a work order" in e for e in errors)


def test_invalid_json_line_is_reported_by_line_number(tmp_path: Path) -> None:
    """A malformed line is a validation error naming the line, not a crash."""
    path = tmp_path / "input_schema_catalog_gaps.jsonl"
    path.write_text(json.dumps(MISSING_DEPLOYMENT) + "\n{not json\n", encoding="utf-8")

    errors = validate_catalog_gaps(path)

    assert any("Line 2: Invalid JSON" in e for e in errors)


def test_non_object_line_is_reported_by_line_number(tmp_path: Path) -> None:
    """A JSON line that is not an object is reported by its physical line number.

    The blank line ahead of it is skipped silently and the malformed line before it is
    reported by line, so all three coordinates in the output mean the same thing --
    numbering by position among the parsed records would point here at line 1.
    """
    path = tmp_path / "input_schema_catalog_gaps.jsonl"
    path.write_text('\n{not json\n["missing_variable"]\n', encoding="utf-8")

    errors = validate_catalog_gaps(path)

    assert any("Line 2: Invalid JSON" in e for e in errors)
    assert any("Line 3: Record must be a JSON object, got list" in e for e in errors)


def test_record_error_is_reported_by_physical_line_number(tmp_path: Path) -> None:
    """A bad record after a skipped and a malformed line is still named by its own line.

    This is the case the two coordinate systems diverged on: the record is the first
    one parsed, so numbering by parsed position reported it as "Record 1" and sent a
    consumer to a blank line.
    """
    path = tmp_path / "input_schema_catalog_gaps.jsonl"
    bad = dict(MISSING_DEPLOYMENT, table_id="a.b")
    path.write_text("\n{not json\n" + json.dumps(bad) + "\n", encoding="utf-8")

    errors = validate_catalog_gaps(path)

    assert any(
        "Line 3 (missing_deployment): table_id 'a.b' must have 3 non-empty segments" in e
        for e in errors
    )


def test_blank_lines_are_skipped(tmp_path: Path) -> None:
    """A trailing newline or a blank separator line is not a record and not an error."""
    path = tmp_path / "input_schema_catalog_gaps.jsonl"
    path.write_text(json.dumps(MISSING_DEPLOYMENT) + "\n\n", encoding="utf-8")

    assert validate_catalog_gaps(path) == []


def test_unknown_record_type_is_rejected(tmp_path: Path) -> None:
    """Only the three gap kinds are legal record types."""
    errors = validate_catalog_gaps(_write(tmp_path, [{"record_type": "missing_concept"}]))

    assert any("Invalid or missing record_type 'missing_concept'" in e for e in errors)


@pytest.mark.parametrize(
    ("template", "dropped"),
    [
        (MISSING_VARIABLE, "origin_data_scope"),
        (MISSING_CANDIDATE, "origin_column_id"),
        (MISSING_DEPLOYMENT, "system"),
    ],
)
def test_missing_field_is_rejected(tmp_path: Path, template: dict[str, Any], dropped: str) -> None:
    """Each record type's field set is exact: a missing field fails."""
    record = {k: v for k, v in template.items() if k != dropped}

    errors = validate_catalog_gaps(_write(tmp_path, [record]))

    assert any("Missing fields" in e and dropped in e for e in errors)


@pytest.mark.parametrize("template", [MISSING_VARIABLE, MISSING_CANDIDATE, MISSING_DEPLOYMENT])
def test_unexpected_field_is_rejected(tmp_path: Path, template: dict[str, Any]) -> None:
    """Each record type's field set is exact: an extra field fails."""
    record = dict(template, remedy="document it")

    errors = validate_catalog_gaps(_write(tmp_path, [record]))

    assert any("Unexpected fields" in e and "remedy" in e for e in errors)


def test_empty_file_is_rejected(tmp_path: Path) -> None:
    """An empty work order must never accompany a failed run."""
    errors = validate_catalog_gaps(_write(tmp_path, []))

    assert any("0 records" in e for e in errors)


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    """A path that does not exist is an error, not a pass."""
    errors = validate_catalog_gaps(tmp_path / "absent.jsonl")

    assert any("File not found" in e for e in errors)


def test_unreadable_file_is_reported(tmp_path: Path) -> None:
    """A path that exists but cannot be read is an error, not a crash."""
    directory = tmp_path / "input_schema_catalog_gaps.jsonl"
    directory.mkdir()

    assert any("Failed to read file" in e for e in validate_catalog_gaps(directory))


def test_missing_deployment_accepts_a_null_system(tmp_path: Path) -> None:
    """A null system is legal on missing_deployment: the nowhere-deployed code set."""
    record = dict(MISSING_DEPLOYMENT, table_id="ref.codes.clm_type_cd", system=None)

    assert validate_catalog_gaps(_write(tmp_path, [record])) == []


@pytest.mark.parametrize("system", ["", 7, ["edw"]])
def test_missing_deployment_rejects_a_non_string_system(tmp_path: Path, system: object) -> None:
    """system must be a non-empty string or null -- nothing else."""
    record = dict(MISSING_DEPLOYMENT, system=system)

    errors = validate_catalog_gaps(_write(tmp_path, [record]))

    assert any("'system' must be a non-empty string or null" in e for e in errors)


def test_duplicate_records_are_rejected(tmp_path: Path) -> None:
    """The work order is a set: the same gap listed twice fails."""
    errors = validate_catalog_gaps(_write(tmp_path, [MISSING_VARIABLE, MISSING_VARIABLE]))

    assert any("duplicate gap record" in e for e in errors)


def test_duplicate_record_with_reordered_keys_is_rejected(tmp_path: Path) -> None:
    """The same gap written with its keys in another order is still a duplicate.

    The dedup key is the record canonicalized with sorted keys, precisely so this
    case collides; writing the same dict twice would pass either way, so it is this
    test that depends on the canonical key. A reordered copy is what a hand-edited or
    third-party work order produces -- the audience the module docstring names.
    """
    reordered = {k: MISSING_VARIABLE[k] for k in reversed(list(MISSING_VARIABLE))}

    errors = validate_catalog_gaps(_write(tmp_path, [MISSING_VARIABLE, reordered]))

    assert any("duplicate gap record" in e for e in errors)


def test_duplicate_scope_entry_is_rejected(tmp_path: Path) -> None:
    """A scope list is a set here as it is in the sibling validator.

    The two validators hold one scope contract, so a repeated prefix must not fail one
    gate and pass the other; the scope is quoted back to the person fixing the catalog,
    where a duplicate is meaningless noise.
    """
    record = dict(MISSING_VARIABLE, origin_data_scope=["ocs.a", "ocs.a"])

    errors = validate_catalog_gaps(_write(tmp_path, [record]))

    assert any("has duplicate entries; scope lists are sets" in e for e in errors)


def test_non_string_record_type_is_reported_rather_than_raising(tmp_path: Path) -> None:
    """An unhashable record_type is reported: the validator's job is to report it."""
    errors = validate_catalog_gaps(_write(tmp_path, [{"record_type": ["missing_variable"]}]))

    assert any("Invalid or missing record_type" in e for e in errors)


@pytest.mark.parametrize(
    ("template", "field", "value", "message"),
    [
        (MISSING_VARIABLE, "origin_data_scope", [], "must be a non-empty list of ltree prefixes"),
        (MISSING_VARIABLE, "origin_data_scope", ["a.b.c.d"], "has more than 3 segments"),
        (MISSING_VARIABLE, "origin_data_scope", [""], "entries must be non-empty strings"),
        (MISSING_VARIABLE, "origin_data_scope", ["a."], "must have non-empty segments"),
        (MISSING_VARIABLE, "origin_data_scope", ["a..b"], "must have non-empty segments"),
        (MISSING_VARIABLE, "origin_sas_dataset", "", "'origin_sas_dataset' must be a non-empty string"),
        (MISSING_CANDIDATE, "dest_data_scope", [], "must be a non-empty list of ltree prefixes"),
        (MISSING_CANDIDATE, "dest_data_scope", ["a.b.c.d"], "has more than 3 segments"),
        (MISSING_CANDIDATE, "dest_data_scope", [""], "entries must be non-empty strings"),
        (MISSING_CANDIDATE, "dest_data_scope", ["a..b"], "must have non-empty segments"),
    ],
)
def test_field_values_are_checked(
    tmp_path: Path, template: dict[str, Any], field: str, value: object, message: str
) -> None:
    """Scope lists and identifiers are shape-checked, not just present.

    Both scope fields are covered: they go through one helper but are passed by name,
    so exercising only origin_data_scope would leave a wrong field name in the
    missing_candidate branch undetected.
    """
    record = dict(template, **{field: value})

    errors = validate_catalog_gaps(_write(tmp_path, [record]))

    assert any(message in e for e in errors)


@pytest.mark.parametrize(
    ("record", "message"),
    [
        (dict(MISSING_CANDIDATE, origin_column_id="ocs.clm_type"), "must have 4 non-empty segments"),
        (
            dict(MISSING_DEPLOYMENT, table_id="ocs.non_institutional.clm.clm_type"),
            "must have 3 non-empty segments",
        ),
        # An empty segment keeps the count legal but names no catalog object.
        (
            dict(MISSING_CANDIDATE, origin_column_id="ocs.non_institutional..clm_type"),
            "must have 4 non-empty segments",
        ),
        (
            dict(MISSING_DEPLOYMENT, table_id="edwc_prd.claims_vw_prd."),
            "must have 3 non-empty segments",
        ),
    ],
)
def test_id_segment_counts_are_checked(tmp_path: Path, record: dict[str, Any], message: str) -> None:
    """A column id is 4 segments and a table id is 3, mirroring the system-free catalog."""
    errors = validate_catalog_gaps(_write(tmp_path, [record]))

    assert any(message in e for e in errors)


# --- Record order ---


def test_records_out_of_write_order_are_rejected(tmp_path: Path) -> None:
    """The work order has one legal serialization, as the resolution file does.

    A diff against the last run is a named consumer of this file, which only holds if
    the order is fixed; the resolver emits it that way, and this is what makes that a
    checked contract rather than a coincidence.
    """
    errors = validate_catalog_gaps(_write(tmp_path, [MISSING_CANDIDATE, MISSING_VARIABLE]))

    assert any("grouped by type in write order" in e for e in errors)


def test_unsorted_records_within_a_group_are_rejected(tmp_path: Path) -> None:
    """Within a group, records sort by their identifying fields."""
    later = dict(MISSING_VARIABLE, origin_sas_variable="zz_last")

    errors = validate_catalog_gaps(_write(tmp_path, [later, MISSING_VARIABLE]))

    assert any("'missing_variable' records must be sorted by" in e for e in errors)


def test_unsorted_deployment_records_are_rejected(tmp_path: Path) -> None:
    """A deployment work order sorts by (table_id, system), the resolver's own order."""
    other = dict(MISSING_DEPLOYMENT, table_id="aaa.bbb.ccc")

    errors = validate_catalog_gaps(_write(tmp_path, [MISSING_DEPLOYMENT, other]))

    assert any("'missing_deployment' records must be sorted by" in e for e in errors)


# --- The CLI entry point ---


def test_main_exits_non_zero_on_validation_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing work order exits 1 through the CLI, not 0 with the errors only logged."""
    path = _write(tmp_path, [MISSING_VARIABLE, MISSING_DEPLOYMENT])
    monkeypatch.setattr(sys, "argv", ["data_val_catalog_gaps.py", "--input-data", str(path)])

    with pytest.raises(SystemExit) as excinfo:
        validator_main()

    assert excinfo.value.code == 1


def test_main_returns_cleanly_on_a_well_formed_work_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A passing work order exits the CLI cleanly (no SystemExit)."""
    path = _write(tmp_path, [MISSING_VARIABLE, MISSING_CANDIDATE])
    monkeypatch.setattr(sys, "argv", ["data_val_catalog_gaps.py", "--input-data", str(path)])

    assert validator_main() is None


def test_main_exits_non_zero_when_validation_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unexpected error inside validation still exits 1, not a traceback.

    Callers gate on the exit code, so an exception escaping main() would read as a
    crash rather than the clean failure the CLI contract promises.
    """

    def _raise(_input_data: Path) -> list[str]:
        raise RuntimeError("unexpected failure")

    path = _write(tmp_path, [MISSING_VARIABLE, MISSING_CANDIDATE])
    monkeypatch.setattr(sys, "argv", ["data_val_catalog_gaps.py", "--input-data", str(path)])
    monkeypatch.setattr(data_val_catalog_gaps, "validate_catalog_gaps", _raise)

    with pytest.raises(SystemExit) as excinfo:
        validator_main()

    assert excinfo.value.code == 1
