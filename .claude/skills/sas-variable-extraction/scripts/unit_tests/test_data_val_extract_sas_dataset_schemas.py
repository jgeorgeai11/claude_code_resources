"""Unit tests for data_val_extract_sas_dataset_schemas.py.

The validator is a second, independent statement of the inventory contracts: the
extractor decides which records to write, and these checks decide which it was
allowed to write. Testing them through the extractor's output would defeat that, so
every case here builds its records by hand -- a well-formed inventory, then copies
of it broken one field at a time.

The two inventory kinds share their record shapes but not their rules, so the
shared rules (structure, variables) are parametrized over both validators, while
the coordinate rules that separate them get their own cases on each side.
"""

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

# conftest.py puts scripts/data_validation/ on the path, so the validator imports by
# name here without going through the extractor that inserts it in production.
import data_val_extract_sas_dataset_schemas as data_val
from data_val_extract_sas_dataset_schemas import validate_input_schemas, validate_output_schemas

# Both public validators take a path and answer with the errors found, which is the
# signature the parametrized cases below depend on and the one the extractor annotates
# when it takes either of them as its validator callable.
Validator = Callable[[Path], list[str]]

# The shared rules hold identically on both sides; the filename is irrelevant to the
# validators themselves, which are handed the contract to apply by main().
BOTH_VALIDATORS = pytest.mark.parametrize(
    "validate", [validate_input_schemas, validate_output_schemas], ids=["input", "output"]
)

# The coordinates an outputs inventory carries nowhere.
COORDINATES = [
    ("origin_system", "warehouse"),
    ("dest_system", "edw"),
    ("origin_data_scope", ["fixture_ocs.general"]),
    ("dest_data_scope", ["fixture_edw"]),
]


# --- Builders ---


def _meta(**extra: Any) -> dict[str, Any]:
    """Build a meta record.

    Args:
        **extra: Fields to add beyond process_name.

    Returns:
        The record.
    """
    return {"record_type": "meta", "process_name": "p", **extra}


def _dataset(dataset: str = "RAW.FOO", **extra: Any) -> dict[str, Any]:
    """Build a dataset record carrying the SAS identity.

    Args:
        dataset: The dataset name.
        **extra: Fields to add beyond the identity.

    Returns:
        The record.
    """
    return {
        "record_type": "origin_sas_dataset",
        "dataset": dataset,
        "filepath": "x.xpt",
        **extra,
    }


def _variable(dataset: str = "RAW.FOO", variable: str = "x", **extra: Any) -> dict[str, Any]:
    """Build a variable record with every required field.

    Args:
        dataset: The dataset the variable belongs to.
        variable: The variable name.
        **extra: Fields to override or add.

    Returns:
        The record.
    """
    return {
        "record_type": "origin_sas_variable",
        "dataset": dataset,
        "variable": variable,
        "type": "num",
        "format": "BEST12.",
        "length": 8,
        "label": "X",
        **extra,
    }


def _well_formed() -> list[dict[str, Any]]:
    """A minimal inventory valid under both contracts.

    The meta carries process_name alone and the dataset record exactly the SAS
    identity, so the outputs contract's absence rules hold as well as the input
    contract's.

    Returns:
        The records, in write order.
    """
    return [_meta(), _dataset(), _variable()]


def _write(tmp_path: Path, records: list[dict[str, Any]], name: str = "input_schema.jsonl") -> Path:
    """Write records to a JSONL file for validation.

    Args:
        tmp_path: The pytest temporary directory.
        records: The records to write.
        name: The filename to write under; only main() reads it.

    Returns:
        The path written.
    """
    path = tmp_path / name
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


# --- Reading the file ---


@BOTH_VALIDATORS
def test_validate_schemas_missing_file_is_reported(tmp_path: Path, validate: Validator) -> None:
    """A path that does not exist is reported as an error, not raised."""
    absent = tmp_path / "input_schema.jsonl"

    errors = validate(absent)

    assert errors == [f"File not found: {absent}"]


@BOTH_VALIDATORS
def test_validate_schemas_unreadable_file_is_reported(tmp_path: Path, validate: Validator) -> None:
    """An OSError while reading is reported as an error, not raised.

    A directory stands in for the unreadable file: it exists, so the reader gets past
    the missing-file branch, and opening it for reading raises an OSError.
    """
    directory = tmp_path / "input_schema.jsonl"
    directory.mkdir()

    errors = validate(directory)

    assert any("Failed to read file" in e for e in errors)


@BOTH_VALIDATORS
def test_validate_schemas_malformed_json_line_is_reported(tmp_path: Path, validate: Validator) -> None:
    """A truncated line is reported with its line number, not raised as a decode error."""
    path = tmp_path / "input_schema.jsonl"
    path.write_text('{"record_type":\n', encoding="utf-8")

    errors = validate(path)

    assert any("Line 1: Invalid JSON" in e for e in errors)


@BOTH_VALIDATORS
def test_validate_schemas_non_object_line_is_reported(tmp_path: Path, validate: Validator) -> None:
    """A JSON array line is reported and dropped: every later check assumes an object.

    Without the drop the array reaches the structural checks and crashes them on
    record.get() -- a traceback on exactly the malformed input the validator exists
    to report. The blank line ahead of it is skipped silently, so the reported line
    number is the physical one rather than a count of parsed records.
    """
    path = tmp_path / "input_schema.jsonl"
    path.write_text(
        "\n[1, 2]\n" + "".join(json.dumps(r) + "\n" for r in _well_formed()), encoding="utf-8"
    )

    errors = validate(path)

    assert errors == ["Line 2: Expected a JSON object, got list"]


@BOTH_VALIDATORS
def test_validate_schemas_empty_file_is_rejected(tmp_path: Path, validate: Validator) -> None:
    """A file that parses to no records fails rather than passing vacuously."""
    path = _write(tmp_path, [])

    errors = validate(path)

    assert errors == ["File contains 0 records"]


# --- The structural gate, shared by both contracts ---


@BOTH_VALIDATORS
def test_validate_schemas_well_formed_inventory_passes(tmp_path: Path, validate: Validator) -> None:
    """The minimal inventory both contracts accept validates clean.

    This is the accept direction for every rule below: each rejection case starts
    from these records, so a check that rejected something legal fails here first.
    """
    assert validate(_write(tmp_path, _well_formed())) == []


@BOTH_VALIDATORS
def test_validate_schemas_unknown_record_type_is_rejected(tmp_path: Path, validate: Validator) -> None:
    """A record_type outside the three the contract defines is rejected by position."""
    records = _well_formed()
    records[2]["record_type"] = "origin_sas_footnote"

    errors = validate(_write(tmp_path, records))

    assert any("Record 3: Invalid or missing record_type 'origin_sas_footnote'" in e for e in errors)


@BOTH_VALIDATORS
def test_validate_schemas_missing_record_type_is_rejected(tmp_path: Path, validate: Validator) -> None:
    """A record with no record_type at all is rejected, not silently ignored."""
    records = _well_formed()
    del records[1]["record_type"]

    errors = validate(_write(tmp_path, records))

    assert any("Record 2: Invalid or missing record_type 'None'" in e for e in errors)


@BOTH_VALIDATORS
def test_validate_schemas_missing_meta_is_rejected(tmp_path: Path, validate: Validator) -> None:
    """An inventory with no meta record has nothing declaring the process."""
    records = [r for r in _well_formed() if r["record_type"] != "meta"]

    errors = validate(_write(tmp_path, records))

    assert any("Expected exactly one meta record, found 0" in e for e in errors)


@BOTH_VALIDATORS
def test_validate_schemas_duplicate_meta_is_rejected(tmp_path: Path, validate: Validator) -> None:
    """A second meta record is rejected: the process is declared once."""
    records = _well_formed()
    records.insert(1, _meta())

    errors = validate(_write(tmp_path, records))

    assert any("Expected exactly one meta record, found 2" in e for e in errors)


@BOTH_VALIDATORS
def test_validate_schemas_meta_off_the_first_line_is_rejected(tmp_path: Path, validate: Validator) -> None:
    """The meta record anchors the file, so it must be line 1."""
    records = _well_formed()
    records[0], records[1] = records[1], records[0]

    errors = validate(_write(tmp_path, records))

    assert any("The meta record must be the first line" in e for e in errors)


@BOTH_VALIDATORS
def test_validate_schemas_meta_without_process_name_is_rejected(tmp_path: Path, validate: Validator) -> None:
    """process_name is required on meta under both contracts."""
    records = _well_formed()
    del records[0]["process_name"]

    errors = validate(_write(tmp_path, records))

    assert any("Meta record: Missing fields" in e and "process_name" in e for e in errors)


@BOTH_VALIDATORS
@pytest.mark.parametrize("value", [None, "", 7])
def test_validate_schemas_meta_process_name_without_a_usable_value_is_rejected(
    tmp_path: Path, validate: Validator, value: Any
) -> None:
    """process_name has to name the process, not merely be present.

    A null, a blank, and a number all pass a key-presence check, and the resolution
    validator downstream requires a non-empty string of this same carried field, so
    each would publish here and fail one step later.
    """
    records = _well_formed()
    records[0]["process_name"] = value

    errors = validate(_write(tmp_path, records))

    assert any("Meta record: 'process_name' must be a non-empty string" in e for e in errors)


# --- The input contract: systems are meta-only ---


def test_validate_input_schemas_meta_coordinates_are_accepted(tmp_path: Path) -> None:
    """The input meta carries the process-level catalog coordinates.

    The mirror of the outputs rule below: what the outputs meta may never carry is
    exactly what the input meta is for.
    """
    records = _well_formed()
    records[0].update(dict(COORDINATES))

    assert validate_input_schemas(_write(tmp_path, records)) == []


@pytest.mark.parametrize("field", ["origin_system", "dest_system"])
def test_validate_input_schemas_dataset_system_field_is_rejected(tmp_path: Path, field: str) -> None:
    """A dataset record carrying a system field is rejected independently.

    Systems are process-wide and declared on meta alone, so a regression in the
    extractor fails validation here rather than publishing an inventory the resolver
    would reject one step later.
    """
    records = _well_formed()
    records[1][field] = "edw"

    errors = validate_input_schemas(_write(tmp_path, records))

    assert any("carries system fields" in e and field in e for e in errors)


def test_validate_input_schemas_dataset_data_scope_overrides_are_accepted(tmp_path: Path) -> None:
    """origin_data_scope / dest_data_scope overrides remain legal on a dataset record."""
    records = _well_formed()
    records[1]["origin_data_scope"] = ["fixture_ocs.general.clm"]
    records[1]["dest_data_scope"] = ["fixture_edw"]

    assert validate_input_schemas(_write(tmp_path, records)) == []


@pytest.mark.parametrize("field", ["origin_data_scope", "dest_data_scope"])
@pytest.mark.parametrize("index", [0, 1], ids=["meta", "dataset"])
def test_validate_input_schemas_data_scope_as_a_bare_string_is_rejected(
    tmp_path: Path, field: str, index: int
) -> None:
    """A scope is a list of ltree prefixes wherever it appears, never one bare string.

    Both fields are checked on the meta that declares them and on a dataset record that
    narrows them, because the resolver's validator requires the list shape of exactly
    these fields and an inventory carrying a string would fail one step later.
    """
    records = _well_formed()
    records[index][field] = "fixture_ocs.general"

    errors = validate_input_schemas(_write(tmp_path, records))

    assert any(f"'{field}' must be a non-empty list of ltree prefixes" in e for e in errors)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ([], "must be a non-empty list of ltree prefixes"),
        (None, "must be a non-empty list of ltree prefixes"),
        (["fixture_ocs.general", ""], "entries must be non-empty strings"),
        (["fixture_ocs.general", 7], "entries must be non-empty strings"),
    ],
)
def test_validate_input_schemas_data_scope_of_the_wrong_shape_is_rejected(
    tmp_path: Path, value: Any, expected: str
) -> None:
    """An empty scope, a null one, and one holding an unusable entry are each rejected.

    A present scope narrows what resolution searches, so an empty or null one says
    nothing while looking deliberate, and one blank entry is a prefix matching
    everything.
    """
    records = _well_formed()
    records[0]["origin_data_scope"] = value

    errors = validate_input_schemas(_write(tmp_path, records))

    assert any(f"'origin_data_scope' {expected}" in e for e in errors)


def test_validate_input_schemas_dataset_unknown_field_is_accepted(tmp_path: Path) -> None:
    """The exact-field rule is the outputs contract's alone, so an input record may say more.

    The input side constrains which coordinates a dataset record carries, not the whole
    field set. This is the counterpart to the outputs case that rejects the same
    unknown field, pinning the asymmetry from both directions.
    """
    records = _well_formed()
    records[1]["engine"] = "xport"

    assert validate_input_schemas(_write(tmp_path, records)) == []


# --- The outputs contract: no coordinate anywhere ---


@pytest.mark.parametrize(("field", "value"), COORDINATES)
def test_validate_output_schemas_meta_coordinate_is_rejected(
    tmp_path: Path, field: str, value: str | list[str]
) -> None:
    """The outputs meta carries process_name alone -- any coordinate is rejected.

    Outputs are inventoried, not resolved: where the interface tables land is the
    conversion plan's decision, not an extraction fact.
    """
    records = _well_formed()
    records[0][field] = value

    errors = validate_output_schemas(_write(tmp_path, records))

    assert any("Meta record" in e and field in e for e in errors)


@pytest.mark.parametrize(("field", "value"), COORDINATES)
def test_validate_output_schemas_dataset_coordinate_is_rejected(
    tmp_path: Path, field: str, value: str | list[str]
) -> None:
    """An outputs dataset record carries exactly dataset and filepath -- nothing else."""
    records = _well_formed()
    records[1][field] = value

    errors = validate_output_schemas(_write(tmp_path, records))

    assert any("RAW.FOO" in e and field in e for e in errors)


def test_validate_output_schemas_dataset_extra_field_is_rejected(tmp_path: Path) -> None:
    """The outputs dataset field set is exact, so any unknown field is rejected too."""
    records = _well_formed()
    records[1]["engine"] = "xport"

    errors = validate_output_schemas(_write(tmp_path, records))

    assert any("carries fields" in e and "engine" in e for e in errors)


# --- Dataset records, shared by both contracts ---


@BOTH_VALIDATORS
def test_validate_schemas_dataset_without_filepath_is_rejected(tmp_path: Path, validate: Validator) -> None:
    """Both contracts require the SAS identity: dataset and filepath."""
    records = _well_formed()
    del records[1]["filepath"]

    errors = validate(_write(tmp_path, records))

    assert any("Dataset record for 'RAW.FOO': Missing fields" in e and "filepath" in e for e in errors)


@BOTH_VALIDATORS
@pytest.mark.parametrize("field", ["dataset", "filepath"])
@pytest.mark.parametrize("value", [None, "", 7])
def test_validate_schemas_dataset_identity_without_a_usable_value_is_rejected(
    tmp_path: Path, validate: Validator, field: str, value: Any
) -> None:
    """Each half of the SAS identity has to be usable, not merely present.

    Everything downstream cites the dataset by name and reads it from the filepath, and
    a blanked or retyped half of the pair passes a key-presence check while the
    resolution validator rejects it one step later.
    """
    records = _well_formed()
    records[1][field] = value

    errors = validate(_write(tmp_path, records))

    assert any(f"'{field}' must be a non-empty string" in e for e in errors)


@BOTH_VALIDATORS
def test_validate_schemas_duplicate_dataset_record_is_rejected(tmp_path: Path, validate: Validator) -> None:
    """A second dataset record with the same name is rejected, not collapsed.

    The mirror of the duplicate-variable rule: dataset records are unique by name.
    """
    records = _well_formed()
    records.insert(2, _dataset())

    errors = validate(_write(tmp_path, records))

    assert any("Duplicate dataset record 'RAW.FOO'" in e for e in errors)


# --- Variable records: identical rules on both sides ---


@BOTH_VALIDATORS
def test_validate_schemas_variable_missing_fields_is_rejected(tmp_path: Path, validate: Validator) -> None:
    """Every variable record carries the full SAS metadata, nulls included."""
    records = _well_formed()
    del records[2]["label"]

    errors = validate(_write(tmp_path, records))

    assert any("Variable 'x' in 'RAW.FOO': Missing fields" in e and "label" in e for e in errors)


@BOTH_VALIDATORS
@pytest.mark.parametrize("value", [None, "", 7])
def test_validate_schemas_variable_name_without_a_usable_value_is_rejected(
    tmp_path: Path, validate: Validator, value: Any
) -> None:
    """The variable name is what resolution maps to a column, so it must be a real name."""
    records = _well_formed()
    records[2]["variable"] = value

    errors = validate(_write(tmp_path, records))

    assert any("'variable' must be a non-empty string" in e for e in errors)


@BOTH_VALIDATORS
@pytest.mark.parametrize("value", ["8", None, 8.5, True])
def test_validate_schemas_variable_non_integer_length_is_rejected(
    tmp_path: Path, validate: Validator, value: Any
) -> None:
    """length is the SAS storage width in bytes, and the resolution validator wants an int.

    A quoted "8" is what a round trip through a spreadsheet produces, and True is here
    because isinstance calls a bool an int: it would pass a plain type check and is
    still not a width.
    """
    records = _well_formed()
    records[2]["length"] = value

    errors = validate(_write(tmp_path, records))

    assert any("Variable 'x' in 'RAW.FOO': 'length' must be an integer" in e for e in errors)


@BOTH_VALIDATORS
@pytest.mark.parametrize("bad_type", ["date", "character", "", None])
def test_validate_schemas_variable_invalid_type_is_rejected(
    tmp_path: Path, validate: Validator, bad_type: str | None
) -> None:
    """SAS has two storage types, so anything but char or num is rejected."""
    records = _well_formed()
    records[2]["type"] = bad_type

    errors = validate(_write(tmp_path, records))

    assert any(f"Invalid type '{bad_type}'" in e for e in errors)


@BOTH_VALIDATORS
def test_validate_schemas_orphan_variable_is_rejected(tmp_path: Path, validate: Validator) -> None:
    """A variable whose dataset has no dataset record is an orphan."""
    records = _well_formed()
    records[2]["dataset"] = "RAW.ELSEWHERE"

    errors = validate(_write(tmp_path, records))

    assert any("'RAW.ELSEWHERE' has no origin_sas_dataset record" in e for e in errors)


@BOTH_VALIDATORS
def test_validate_schemas_duplicate_variable_is_rejected(tmp_path: Path, validate: Validator) -> None:
    """A variable repeated within one dataset is rejected."""
    records = _well_formed()
    records.append(_variable())

    errors = validate(_write(tmp_path, records))

    assert any("Duplicate variable 'x' in dataset 'RAW.FOO'" in e for e in errors)


@BOTH_VALIDATORS
def test_validate_schemas_same_variable_in_two_datasets_is_accepted(
    tmp_path: Path, validate: Validator
) -> None:
    """The duplicate rule is per dataset: one name may appear in each."""
    records = _well_formed()
    records.insert(2, _dataset("RAW.BAR"))
    records.append(_variable("RAW.BAR"))

    assert validate(_write(tmp_path, records)) == []


@BOTH_VALIDATORS
def test_validate_schemas_variable_missing_dataset_is_keyed_consistently(
    tmp_path: Path, validate: Validator
) -> None:
    """A variable with no dataset field is keyed the same way by both variable passes.

    Keying the orphan check on a missing field but the duplicate check on the empty
    string collides the two records below into a spurious duplicate: each is an
    orphan under its own key, and neither repeats the other.
    """
    records = _well_formed()
    records.append(_variable(variable="y"))
    del records[-1]["dataset"]
    records.append(_variable(dataset="", variable="y"))

    errors = validate(_write(tmp_path, records))

    assert not any("Duplicate variable" in e for e in errors)
    assert any("dataset 'None' has no origin_sas_dataset record" in e for e in errors)
    assert any("dataset '' has no origin_sas_dataset record" in e for e in errors)


# --- The CLI entry point: which contract gets applied ---


def _argv(monkeypatch: pytest.MonkeyPatch, path: Path, *flags: str) -> None:
    """Point sys.argv at the validator CLI for one file.

    Args:
        monkeypatch: The pytest monkeypatch fixture.
        path: The inventory file to validate.
        *flags: Extra command-line flags.
    """
    monkeypatch.setattr(sys, "argv", ["data_val", "--input-data", str(path), *flags])


def _meta_with_a_system() -> list[dict[str, Any]]:
    """An inventory legal under the input contract and illegal under the outputs one.

    The single discriminator the CLI tests turn on: a meta system is what an input
    inventory declares and what an outputs inventory may never carry.

    Returns:
        The records.
    """
    records = _well_formed()
    records[0]["origin_system"] = "warehouse"
    return records


def test_main_infers_the_input_contract_from_the_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file named exactly input_schema.jsonl gets the input contract without --kind."""
    path = _write(tmp_path, _meta_with_a_system(), "input_schema.jsonl")
    _argv(monkeypatch, path)

    assert data_val.main() is None


def test_main_infers_the_outputs_contract_from_the_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file named exactly output_schema.jsonl gets the outputs contract without --kind.

    The same records pass under --kind input, so the rejection is the inferred
    contract's doing rather than a defect in the file.
    """
    path = _write(tmp_path, _meta_with_a_system(), "output_schema.jsonl")

    _argv(monkeypatch, path)
    with pytest.raises(SystemExit) as excinfo:
        data_val.main()
    assert excinfo.value.code == 1

    _argv(monkeypatch, path, "--kind", "input")
    assert data_val.main() is None


def test_main_other_filename_without_kind_exits_asking_for_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A basename that is neither exact inventory name exits naming --kind.

    Silently defaulting to the input contract is how a differently named outputs file
    gets validated against the wrong contract and passes, so the CLI refuses to guess
    -- even for a file, as here, that either contract would accept.
    """
    path = _write(tmp_path, _well_formed(), "example_output_schema.jsonl")
    _argv(monkeypatch, path)

    with caplog.at_level("ERROR"), pytest.raises(SystemExit) as excinfo:
        data_val.main()

    assert excinfo.value.code == 1
    assert "--kind" in caplog.text


def test_main_explicit_kind_covers_an_arbitrary_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--kind applies the named contract to a file of any name.

    The same arbitrarily named file passes under --kind input and fails under --kind
    output, proving the flag rather than the filename picked the contract.
    """
    path = _write(tmp_path, _meta_with_a_system(), "anything.jsonl")

    _argv(monkeypatch, path, "--kind", "input")
    assert data_val.main() is None

    _argv(monkeypatch, path, "--kind", "output")
    with pytest.raises(SystemExit) as excinfo:
        data_val.main()
    assert excinfo.value.code == 1


def test_main_validation_errors_exit_non_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A failing inventory exits 1 through the CLI, not 0 with the errors only logged."""
    records = _well_formed()
    records[2]["type"] = "date"
    _argv(monkeypatch, _write(tmp_path, records))

    with caplog.at_level("ERROR"), pytest.raises(SystemExit) as excinfo:
        data_val.main()

    assert excinfo.value.code == 1
    assert "VALIDATION FAILED" in caplog.text


def test_main_unexpected_failure_is_logged_and_exits_non_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An unanticipated exception is reported, not raised as a bare traceback.

    A list where a dataset name belongs is unhashable, so it raises a TypeError deep
    in the duplicate check -- the class of failure that would otherwise escape the CLI
    with no closing separator and nothing said about which file caused it.
    """
    records = _well_formed()
    records[1]["dataset"] = ["RAW.FOO"]
    _argv(monkeypatch, _write(tmp_path, records))

    with caplog.at_level("ERROR"), pytest.raises(SystemExit) as excinfo:
        data_val.main()

    assert excinfo.value.code == 1
    assert "Validation aborted" in caplog.text


# --- The split notation: both sides marked or neither, and in the filename alone ---


SPLIT_ERROR = "the split notation marks only one side"
DIRECTORY_GLOB_ERROR = "carries a '*' outside its filename"


@BOTH_VALIDATORS
def test_validate_schemas_split_notation_on_both_sides_is_valid(tmp_path: Path, validate: Validator) -> None:
    """A split dataset marks its name and its filepath, and that is a clean record."""
    records = [
        _meta(),
        _dataset("RAW.CLM_*", filepath="data/sas/clm_*.xpt"),
        _variable("RAW.CLM_*"),
    ]

    assert validate(_write(tmp_path, records)) == []


@BOTH_VALIDATORS
def test_validate_schemas_split_notation_on_neither_side_is_valid(tmp_path: Path, validate: Validator) -> None:
    """An ordinary single-file dataset marks neither side."""
    assert validate(_write(tmp_path, _well_formed())) == []


@BOTH_VALIDATORS
def test_validate_schemas_split_notation_on_the_name_alone_is_rejected(tmp_path: Path, validate: Validator) -> None:
    """A `*` name over a concrete path claims a split the filepath does not describe."""
    records = [_meta(), _dataset("RAW.CLM_*"), _variable("RAW.CLM_*")]

    errors = validate(_write(tmp_path, records))

    assert any(SPLIT_ERROR in e and "'filepath' carries no '*'" in e for e in errors)


@BOTH_VALIDATORS
def test_validate_schemas_split_notation_on_the_filepath_alone_is_rejected(tmp_path: Path, validate: Validator) -> None:
    """A pattern path under a concrete name loses the split from every variable record."""
    records = [
        _meta(),
        _dataset("RAW.CLM", filepath="data/sas/clm_*.xpt"),
        _variable("RAW.CLM"),
    ]

    errors = validate(_write(tmp_path, records))

    assert any(SPLIT_ERROR in e and "'dataset' carries no '*'" in e for e in errors)


@BOTH_VALIDATORS
def test_validate_schemas_split_notation_in_a_directory_is_rejected(
    tmp_path: Path, validate: Validator
) -> None:
    """A `*` outside the filename is rejected: the extractor expands the filename only.

    The name and the filepath are both marked here, so the half-applied rule has
    nothing to say about this record -- a directory glob is its own defect, and the
    extractor would have looked the path up literally and matched nothing.
    """
    records = [
        _meta(),
        _dataset("RAW.CLM_*", filepath="data/*/clm.xpt"),
        _variable("RAW.CLM_*"),
    ]

    errors = validate(_write(tmp_path, records))

    assert any(DIRECTORY_GLOB_ERROR in e and "belongs in the filename alone" in e for e in errors)


@BOTH_VALIDATORS
def test_validate_schemas_split_notation_directory_glob_under_a_plain_name_is_rejected(
    tmp_path: Path, validate: Validator
) -> None:
    """A directory glob under an unmarked name is still rejected, by the directory rule.

    This is why the two rules are independent: the name and the basename agree that
    nothing is split, so pairing them is silent, and only the directory rule is left to
    catch a filepath the extractor could never have expanded.
    """
    records = [_meta(), _dataset("RAW.CLM", filepath="data/*/clm.xpt"), _variable("RAW.CLM")]

    errors = validate(_write(tmp_path, records))

    assert any(DIRECTORY_GLOB_ERROR in e for e in errors)
    assert not any(SPLIT_ERROR in e for e in errors)


@BOTH_VALIDATORS
def test_validate_schemas_split_notation_check_is_silent_when_a_field_is_missing(tmp_path: Path, validate: Validator) -> None:
    """A record missing filepath reports that, not a second complaint about the notation."""
    records = [_meta(), {"record_type": "origin_sas_dataset", "dataset": "RAW.CLM_*"}]

    errors = validate(_write(tmp_path, records))

    assert any("Missing fields" in e for e in errors)
    assert not any(SPLIT_ERROR in e for e in errors)
