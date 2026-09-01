"""Unit tests for extract_sas_dataset_schemas.py."""

import json
import subprocess
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from extract_sas_dataset_schemas import (
    build_meta_record,
    main,
    build_records,
    build_dataset_record,
    build_output_records,
    extract_dataset_variables,
    parse_config,
    write_jsonl,
)

# The tests that drive the extractor as a real CLI need a cwd where two sets of
# repo-relative paths resolve: the script path itself, and the dataset paths carried
# out of fixtures/test_config.toml (which the generator emits verbatim from a
# repo-relative output_dir). Anchor both on this file rather than the directory pytest
# happened to be invoked from, so the suite passes from anywhere -- run from inside
# unit_tests/ it used to fail two tests outright, and pass a third for the wrong
# reason, since "script not found" also satisfies a non-zero exit.
_REPO_ROOT = Path(__file__).resolve().parents[5]
_EXTRACTOR = _REPO_ROOT / ".claude/skills/sas-variable-extraction/scripts/extract_sas_dataset_schemas.py"


# --- Test TOML parsing: valid config produces correct settings and datasets ---


def test_parse_config_valid(test_config_path: Path) -> None:
    """Valid config produces correct settings, coordinates, and dataset entries."""
    config = parse_config(test_config_path)

    assert config["settings"]["process_name"] == "test_process"
    assert config["settings"]["overwrite"] is True
    assert config["settings"]["origin_system"] == "warehouse"
    assert config["settings"]["dest_system"] == "edw"
    assert config["settings"]["origin_data_scope"] == ["fixture_ocs.general"]
    assert config["settings"]["dest_data_scope"] == ["fixture_edw"]

    assert set(config["datasets"]) == {
        "RAW.MEDICAL_CLAIMS",
        "RAW.PHARMACY_CLAIMS",
        "RAW.MEMBER_ENROLLMENT",
        "RAW.EMPTY_DATASET",
        "RAW.SPLIT_LINES_*",
    }
    # Dataset entries are tables carrying at least a path, plus optional coordinate overrides
    assert config["datasets"]["RAW.MEDICAL_CLAIMS"]["path"].endswith("medical_claims.xpt")
    assert config["datasets"]["RAW.PHARMACY_CLAIMS"]["origin_data_scope"] == ["fixture_ocs.general.clm"]
    # The canonical config carries a split dataset too, so it exercises the `*` path
    assert config["datasets"]["RAW.SPLIT_LINES_*"]["path"].endswith("split_lines_*.xpt")


# --- Test TOML parsing: missing required fields raises error ---


def test_parse_config_missing_settings(tmp_path: Path) -> None:
    """Config without [settings] section raises KeyError."""
    config_file = tmp_path / "bad_config.toml"
    config_file.write_text('[datasets]\n"RAW.FOO" = { path = "bar.sas7bdat" }\n')

    with pytest.raises(KeyError, match="settings"):
        parse_config(config_file)


def test_parse_config_missing_process_name(tmp_path: Path) -> None:
    """Config without settings.process_name raises KeyError."""
    config_file = tmp_path / "bad_config.toml"
    config_file.write_text('[settings]\noverwrite = true\n\n[datasets]\n"RAW.FOO" = { path = "bar.sas7bdat" }\n')

    with pytest.raises(KeyError, match="process_name"):
        parse_config(config_file)


def test_parse_config_missing_overwrite(tmp_path: Path) -> None:
    """Config without settings.overwrite raises KeyError."""
    config_file = tmp_path / "bad_config.toml"
    config_file.write_text('[settings]\nprocess_name = "test"\n\n[datasets]\n"RAW.FOO" = { path = "bar.sas7bdat" }\n')

    with pytest.raises(KeyError, match="overwrite"):
        parse_config(config_file)


def test_parse_config_missing_datasets(tmp_path: Path) -> None:
    """Config without [datasets] section raises KeyError."""
    config_file = tmp_path / "bad_config.toml"
    config_file.write_text('[settings]\nprocess_name = "test"\noverwrite = true\n')

    with pytest.raises(KeyError, match="datasets"):
        parse_config(config_file)


def test_parse_config_empty_datasets(tmp_path: Path) -> None:
    """Config with empty [datasets] section raises ValueError."""
    config_file = tmp_path / "bad_config.toml"
    config_file.write_text('[settings]\nprocess_name = "test"\noverwrite = true\n\n[datasets]\n')

    with pytest.raises(ValueError, match="empty"):
        parse_config(config_file)


@pytest.mark.parametrize("section,kind", [("datasets", "Dataset"), ("outputs", "Output")])
def test_parse_config_entry_not_a_table(tmp_path: Path, section: str, kind: str) -> None:
    """An entry that is a bare string (not a table) raises ValueError in either section.

    The two sections share the entry shape, so each enforces it with its own message.
    """
    body = '[settings]\nprocess_name = "test"\noverwrite = true\n\n[datasets]\n'
    if section == "datasets":
        body += '"RAW.FOO" = "bar.sas7bdat"\n'
    else:
        body += '"RAW.OTHER" = { path = "other.sas7bdat" }\n\n[outputs]\n"RAW.FOO" = "bar.sas7bdat"\n'
    config_file = tmp_path / "bad_config.toml"
    config_file.write_text(body)

    with pytest.raises(ValueError, match=rf"{kind} 'RAW.FOO' must be a table"):
        parse_config(config_file)


def test_parse_config_dataset_missing_path(tmp_path: Path) -> None:
    """A dataset table without a 'path' key raises KeyError."""
    config_file = tmp_path / "bad_config.toml"
    config_file.write_text('[settings]\nprocess_name = "test"\noverwrite = true\n\n[datasets]\n"RAW.FOO" = { origin_data_scope = ["fixture_ocs.general"] }\n')

    with pytest.raises(KeyError, match="path"):
        parse_config(config_file)


def test_parse_config_malformed_toml(tmp_path: Path) -> None:
    """Malformed TOML file raises TOMLDecodeError."""
    config_file = tmp_path / "bad_config.toml"
    config_file.write_text("this is not valid [toml\n")

    with pytest.raises(tomllib.TOMLDecodeError):
        parse_config(config_file)


def test_parse_config_nonexistent_file(tmp_path: Path) -> None:
    """Nonexistent config file raises OSError."""
    config_file = tmp_path / "does_not_exist.toml"

    with pytest.raises(OSError):
        parse_config(config_file)


# --- Test [settings] value shapes: presence is not enough ---


@pytest.mark.parametrize("value", ['"false"', "0", '["true"]'])
def test_parse_config_overwrite_must_be_a_boolean(tmp_path: Path, value: str) -> None:
    """A non-boolean settings.overwrite raises ValueError rather than being read truthily.

    `overwrite = "false"` is valid TOML and a truthy string, so an unchecked value
    would silently enable overwriting — the opposite of the declared intent.
    """
    config_file = tmp_path / "bad_config.toml"
    config_file.write_text(
        f'[settings]\nprocess_name = "test"\noverwrite = {value}\n\n'
        '[datasets]\n"RAW.FOO" = { path = "bar.sas7bdat" }\n'
    )

    with pytest.raises(ValueError, match="settings.overwrite must be a boolean"):
        parse_config(config_file)


@pytest.mark.parametrize("value", ['""', "42", '["test"]'])
def test_parse_config_process_name_must_be_a_non_empty_string(tmp_path: Path, value: str) -> None:
    """A non-string or empty settings.process_name raises ValueError — it builds the output path."""
    config_file = tmp_path / "bad_config.toml"
    config_file.write_text(
        f'[settings]\nprocess_name = {value}\noverwrite = true\n\n'
        '[datasets]\n"RAW.FOO" = { path = "bar.sas7bdat" }\n'
    )

    with pytest.raises(ValueError, match="settings.process_name must be a non-empty string"):
        parse_config(config_file)


@pytest.mark.parametrize("value", ['""', "7", '["docs/activities"]'])
def test_parse_config_output_dir_must_be_a_non_empty_string(tmp_path: Path, value: str) -> None:
    """A present-but-malformed settings.output_dir raises ValueError — it builds the output path."""
    config_file = tmp_path / "bad_config.toml"
    config_file.write_text(
        f'[settings]\nprocess_name = "test"\noverwrite = true\noutput_dir = {value}\n\n'
        '[datasets]\n"RAW.FOO" = { path = "bar.sas7bdat" }\n'
    )

    with pytest.raises(ValueError, match="settings.output_dir must be a non-empty string"):
        parse_config(config_file)


@pytest.mark.parametrize("field", ["origin_system", "dest_system"])
@pytest.mark.parametrize("value", ['""', '["edw"]', "1"])
def test_parse_config_system_must_be_a_non_empty_string(tmp_path: Path, field: str, value: str) -> None:
    """A settings-level system of any non-string shape raises ValueError.

    A system is a single `systems` label. Unchecked, any truthy value would mark the
    process for conversion via dest_system and be published verbatim onto the meta
    record, while the data scopes beside it are strictly shape-validated.
    """
    config_file = tmp_path / "bad_config.toml"
    config_file.write_text(
        f'[settings]\nprocess_name = "test"\noverwrite = true\n{field} = {value}\n'
        'origin_data_scope = ["fixture_ocs.general"]\n\n'
        '[datasets]\n"RAW.FOO" = { path = "bar.sas7bdat" }\n'
    )

    with pytest.raises(ValueError, match=f"settings.{field} must be a non-empty string"):
        parse_config(config_file)


# --- Test that a key nothing consumes is rejected rather than silently dropped ---


def test_parse_config_unknown_settings_key_is_rejected(tmp_path: Path) -> None:
    """A misspelled [settings] key raises ValueError naming it and the legal set.

    Nothing defaults or merges an unknown key, so a misspelled output_dir would
    quietly relocate both inventories to the default path.
    """
    config_file = tmp_path / "bad_config.toml"
    config_file.write_text(
        '[settings]\nprocess_name = "test"\noverwrite = true\noutput_directory = "out"\n\n'
        '[datasets]\n"RAW.FOO" = { path = "bar.sas7bdat" }\n'
    )

    with pytest.raises(ValueError, match=r"\[settings\] has unknown key\(s\) \['output_directory'\]"):
        parse_config(config_file)


@pytest.mark.parametrize("key", ["dest_data_scop", "origin_dataset_scope", "pth"])
def test_parse_config_unknown_dataset_key_is_rejected(tmp_path: Path, key: str) -> None:
    """A misspelled [datasets] entry key raises ValueError naming the entry and the key.

    dest_data_scope is the dangerous case: misspelled and accepted, the dataset
    publishes without it, and the resolution step reads that absence as "the data
    source does not change" and never consults column_mappings — a confidently wrong
    resolution with no log line anywhere.
    """
    config_file = tmp_path / "bad_config.toml"
    config_file.write_text(
        '[settings]\nprocess_name = "test"\noverwrite = true\n\n'
        f'[datasets]\n"RAW.FOO" = {{ path = "bar.sas7bdat", {key} = ["fixture_edw"] }}\n'
    )

    with pytest.raises(ValueError, match=rf"Dataset 'RAW.FOO' has unknown key\(s\) \['{key}'\]"):
        parse_config(config_file)


def test_parse_config_unknown_key_error_lists_the_legal_keys(tmp_path: Path) -> None:
    """The rejection lists what the section does accept, so the typo can be corrected."""
    config_file = tmp_path / "bad_config.toml"
    config_file.write_text(
        '[settings]\nprocess_name = "test"\noverwrite = true\n\n'
        '[datasets]\n"RAW.FOO" = { path = "bar.sas7bdat", dest_data_scop = ["fixture_edw"] }\n'
    )

    with pytest.raises(ValueError, match=r"legal keys are \['dest_data_scope', 'origin_data_scope', 'path'\]"):
        parse_config(config_file)


# --- Test conversion coordinate requirements ---


def _conversion_config(tmp_path: Path, body: str) -> Path:
    """Write a conversion-coordinate config body to tmp_path.

    Args:
        tmp_path: The pytest temporary directory.
        body: The full TOML config body under test.

    Returns:
        The path written, for the caller to pass to parse_config.
    """
    config_file = tmp_path / "conversion.toml"
    config_file.write_text(body)
    return config_file


def test_parse_config_conversion_requires_origin_system(tmp_path: Path) -> None:
    """A dataset with an effective dest_system but no origin_system raises ValueError."""
    config_file = _conversion_config(
        tmp_path,
        '[settings]\nprocess_name = "p"\noverwrite = true\ndest_system = "edw"\n'
        'origin_data_scope = ["fixture_ocs.general"]\n\n'
        '[datasets]\n"RAW.FOO" = { path = "foo.xpt" }\n',
    )
    with pytest.raises(ValueError, match="origin_system is required"):
        parse_config(config_file)


def test_parse_config_conversion_requires_origin_data_scope(tmp_path: Path) -> None:
    """A dataset with an effective dest_system but no origin_data_scope raises ValueError."""
    config_file = _conversion_config(
        tmp_path,
        '[settings]\nprocess_name = "p"\noverwrite = true\n'
        'origin_system = "warehouse"\ndest_system = "edw"\n\n'
        '[datasets]\n"RAW.FOO" = { path = "foo.xpt" }\n',
    )
    with pytest.raises(ValueError, match="origin_data_scope is required"):
        parse_config(config_file)


def test_parse_config_conversion_meta_defaults_satisfy(tmp_path: Path) -> None:
    """Meta-level origin_system / origin_data_scope defaults satisfy the requirement for all datasets."""
    config_file = _conversion_config(
        tmp_path,
        '[settings]\nprocess_name = "p"\noverwrite = true\n'
        'origin_system = "warehouse"\ndest_system = "edw"\norigin_data_scope = ["fixture_ocs.general"]\n\n'
        '[datasets]\n"RAW.FOO" = { path = "foo.xpt" }\n',
    )
    config = parse_config(config_file)
    assert config["settings"]["origin_data_scope"] == ["fixture_ocs.general"]


def test_parse_config_conversion_dataset_origin_data_scope_satisfies(tmp_path: Path) -> None:
    """Per-dataset origin_data_scope (no [settings] default) satisfies the requirement.

    The systems are settings-level, but origin_data_scope may still be satisfied per dataset.
    """
    config_file = _conversion_config(
        tmp_path,
        '[settings]\nprocess_name = "p"\noverwrite = true\n'
        'origin_system = "warehouse"\ndest_system = "edw"\n\n'
        '[datasets]\n"RAW.FOO" = { path = "foo.xpt", '
        'origin_data_scope = ["fixture_ocs.general.clm"] }\n',
    )
    config = parse_config(config_file)
    assert config["datasets"]["RAW.FOO"]["origin_data_scope"] == ["fixture_ocs.general.clm"]


def test_parse_config_no_dest_system_is_inventory_only(tmp_path: Path) -> None:
    """With no dest_system anywhere the run is inventory-only, so no coordinates are required."""
    config_file = _conversion_config(
        tmp_path,
        '[settings]\nprocess_name = "p"\noverwrite = true\n\n'
        '[datasets]\n"RAW.FOO" = { path = "foo.xpt" }\n',
    )
    config = parse_config(config_file)
    assert "dest_system" not in config["settings"]
    assert config["datasets"]["RAW.FOO"]["path"] == "foo.xpt"


@pytest.mark.parametrize("field", ["origin_system", "dest_system"])
def test_parse_config_per_dataset_system_is_rejected(tmp_path: Path, field: str) -> None:
    """A system on a [datasets] entry is rejected at parse: systems are process-wide.

    One process is one compute job in one system pair, so only origin_data_scope and
    dest_data_scope remain overridable per dataset. The rejection names the dataset and
    the rule rather than silently stripping the field.
    """
    config_file = _conversion_config(
        tmp_path,
        '[settings]\nprocess_name = "p"\noverwrite = true\n'
        'origin_system = "warehouse"\ndest_system = "edw"\norigin_data_scope = ["fixture_ocs.general"]\n\n'
        f'[datasets]\n"RAW.FOO" = {{ path = "foo.xpt", {field} = "edw" }}\n',
    )
    with pytest.raises(ValueError, match=rf"Dataset 'RAW.FOO' sets '{field}'"):
        parse_config(config_file)


def test_parse_config_dest_data_scope_is_optional(tmp_path: Path) -> None:
    """dest_data_scope is optional — its absence means the data source does not change."""
    config_file = _conversion_config(
        tmp_path,
        '[settings]\nprocess_name = "p"\noverwrite = true\n'
        'origin_system = "warehouse"\ndest_system = "edw"\norigin_data_scope = ["fixture_ocs.general"]\n\n'
        '[datasets]\n"RAW.FOO" = { path = "foo.xpt" }\n',
    )
    config = parse_config(config_file)
    assert "dest_data_scope" not in config["settings"]


# --- Test data scope shape validation ---


def test_parse_config_scope_must_be_a_list(tmp_path: Path) -> None:
    """A origin_data_scope given as a bare string (not a list) raises ValueError."""
    config_file = _conversion_config(
        tmp_path,
        '[settings]\nprocess_name = "p"\noverwrite = true\n'
        'origin_system = "warehouse"\ndest_system = "edw"\norigin_data_scope = "fixture_ocs.general"\n\n'
        '[datasets]\n"RAW.FOO" = { path = "foo.xpt" }\n',
    )
    with pytest.raises(ValueError, match="must be a non-empty list"):
        parse_config(config_file)


def test_parse_config_scope_rejects_too_many_segments(tmp_path: Path) -> None:
    """A scope entry deeper than {data_source}.{schema}.{table} raises ValueError."""
    config_file = _conversion_config(
        tmp_path,
        '[settings]\nprocess_name = "p"\noverwrite = true\n'
        'origin_system = "warehouse"\ndest_system = "edw"\n'
        'origin_data_scope = ["fixture_ocs.general.clm.claim_no"]\n\n'
        '[datasets]\n"RAW.FOO" = { path = "foo.xpt" }\n',
    )
    with pytest.raises(ValueError, match="at most 3 are allowed"):
        parse_config(config_file)


def test_parse_config_scope_rejects_uppercase_segment(tmp_path: Path) -> None:
    """Catalog id segments are lowercase, so an uppercase scope segment raises ValueError."""
    config_file = _conversion_config(
        tmp_path,
        '[settings]\nprocess_name = "p"\noverwrite = true\n'
        'origin_system = "warehouse"\ndest_system = "edw"\norigin_data_scope = ["fixture_OCS.general"]\n\n'
        '[datasets]\n"RAW.FOO" = { path = "foo.xpt" }\n',
    )
    with pytest.raises(ValueError, match="invalid segment"):
        parse_config(config_file)


def test_parse_config_scope_rejects_a_segment_with_a_trailing_newline(tmp_path: Path) -> None:
    """A trailing newline is rejected here, not left for the resolver to trip over.

    TOML can express one (`"ocs.general\\n"` is a valid basic string), and Python's `$`
    matches immediately before a trailing newline — so an `^...$` anchor would pass it,
    the scope would publish verbatim into the inventory, and the resolver would
    interpolate it into a SQL literal that names no catalog object. `\\A...\\Z` refuses
    it at the tool whose job is checking this config.
    """
    config_file = _conversion_config(
        tmp_path,
        '[settings]\nprocess_name = "p"\noverwrite = true\n'
        'origin_system = "warehouse"\ndest_system = "edw"\n'
        'origin_data_scope = ["fixture_ocs.general\\n"]\n\n'
        '[datasets]\n"RAW.FOO" = { path = "foo.xpt" }\n',
    )

    with pytest.raises(ValueError, match="invalid segment"):
        parse_config(config_file)


def test_parse_config_scope_rejects_empty_list(tmp_path: Path) -> None:
    """An empty scope list raises ValueError — absent and empty are different states."""
    config_file = _conversion_config(
        tmp_path,
        '[settings]\nprocess_name = "p"\noverwrite = true\n'
        'origin_system = "warehouse"\ndest_system = "edw"\norigin_data_scope = []\n\n'
        '[datasets]\n"RAW.FOO" = { path = "foo.xpt" }\n',
    )
    with pytest.raises(ValueError, match="must be a non-empty list"):
        parse_config(config_file)


@pytest.mark.parametrize("value", ['[""]', "[7]", '["fixture_ocs", ""]'])
def test_parse_config_scope_rejects_a_malformed_entry(tmp_path: Path, value: str) -> None:
    """A well-formed list holding a malformed entry raises ValueError before it is split.

    The list shape passing says nothing about its entries: an empty or non-string entry
    would otherwise reach entry.split("."), where a number raises AttributeError and an
    empty string yields a single empty segment for the regex to reject with a confusing
    message about a segment the user never wrote.
    """
    config_file = _conversion_config(
        tmp_path,
        '[settings]\nprocess_name = "p"\noverwrite = true\n'
        f'origin_system = "warehouse"\ndest_system = "edw"\norigin_data_scope = {value}\n\n'
        '[datasets]\n"RAW.FOO" = { path = "foo.xpt" }\n',
    )
    with pytest.raises(ValueError, match="entries must be non-empty strings"):
        parse_config(config_file)


def test_parse_config_scope_validated_per_dataset(tmp_path: Path) -> None:
    """A malformed scope on a dataset entry is caught, not just one in [settings]."""
    config_file = _conversion_config(
        tmp_path,
        '[settings]\nprocess_name = "p"\noverwrite = true\n'
        'origin_system = "warehouse"\ndest_system = "edw"\norigin_data_scope = ["fixture_ocs.general"]\n\n'
        '[datasets]\n"RAW.FOO" = { path = "foo.xpt", dest_data_scope = ["A.b"] }\n',
    )
    with pytest.raises(ValueError, match="invalid segment"):
        parse_config(config_file)


def test_parse_config_scope_accepts_all_precisions(tmp_path: Path) -> None:
    """Scope entries may be 1, 2, or 3 segments, and may mix precisions in one list."""
    config_file = _conversion_config(
        tmp_path,
        '[settings]\nprocess_name = "p"\noverwrite = true\n'
        'origin_system = "warehouse"\ndest_system = "edw"\n'
        'origin_data_scope = ["fixture_ocs", "fixture_ocs.general", "fixture_ocs.general.clm"]\n\n'
        '[datasets]\n"RAW.FOO" = { path = "foo.xpt" }\n',
    )
    config = parse_config(config_file)
    assert len(config["settings"]["origin_data_scope"]) == 3


# --- Test meta record construction ---


def test_build_meta_record_with_coordinates() -> None:
    """Meta record carries process_name plus any process-level catalog defaults."""
    settings = {
        "process_name": "claims_analysis",
        "overwrite": True,
        "origin_system": "warehouse",
        "dest_system": "edw",
        "origin_data_scope": ["fixture_ocs.general"],
        "dest_data_scope": ["fixture_edw"],
    }
    assert build_meta_record(settings) == {
        "record_type": "meta",
        "process_name": "claims_analysis",
        "origin_system": "warehouse",
        "dest_system": "edw",
        "origin_data_scope": ["fixture_ocs.general"],
        "dest_data_scope": ["fixture_edw"],
    }


def test_build_meta_record_minimal() -> None:
    """With no coordinates, the meta record carries only record_type and process_name."""
    assert build_meta_record({"process_name": "p", "overwrite": True}) == {
        "record_type": "meta",
        "process_name": "p",
    }


# --- Test dataset record construction (coordinate precedence) ---


def test_build_dataset_record_override() -> None:
    """A coordinate that overrides the meta default is stamped; inherited defaults are not."""
    settings = {
        "process_name": "p",
        "origin_system": "warehouse",
        "dest_system": "edw",
        "origin_data_scope": ["fixture_ocs.general"],
    }
    entry = {"path": "x.xpt", "origin_data_scope": ["fixture_ocs.general.clm"]}
    record = build_dataset_record("RAW.PHARMACY_CLAIMS", entry, settings)

    assert record["record_type"] == "origin_sas_dataset"
    assert record["dataset"] == "RAW.PHARMACY_CLAIMS"
    assert record["filepath"] == "x.xpt"
    # A dataset-level list replaces the meta default outright — it does not merge
    assert record["origin_data_scope"] == ["fixture_ocs.general.clm"]
    assert "dest_system" not in record                    # inherits meta default → absent
    assert "origin_system" not in record


def test_build_dataset_record_dest_data_scope_override() -> None:
    """dest_data_scope set per dataset is stamped on the dataset record."""
    settings = {"process_name": "p", "origin_data_scope": ["fixture_ocs.general"], "dest_data_scope": ["fixture_edw"]}
    entry = {"path": "x.xpt", "dest_data_scope": ["fixture_edw.claims_vw"]}
    record = build_dataset_record("RAW.FOO", entry, settings)

    assert record["dest_data_scope"] == ["fixture_edw.claims_vw"]


def test_build_dataset_record_minimal() -> None:
    """A dataset with only a path yields a bare dataset record (defaults live on meta)."""
    settings = {"process_name": "p", "origin_data_scope": ["fixture_ocs.general"]}
    assert build_dataset_record("RAW.FOO", {"path": "x.xpt"}, settings) == {
        "record_type": "origin_sas_dataset",
        "dataset": "RAW.FOO",
        "filepath": "x.xpt",
    }


def test_build_dataset_record_no_stamp_when_equal_to_meta() -> None:
    """A dataset coordinate equal to the meta default is not re-stamped on the table."""
    settings = {"process_name": "p", "origin_data_scope": ["fixture_ocs.general"]}
    entry = {"path": "x.xpt", "origin_data_scope": ["fixture_ocs.general"]}
    record = build_dataset_record("RAW.FOO", entry, settings)

    assert "origin_data_scope" not in record


def test_build_dataset_record_never_carries_system_fields() -> None:
    """A dataset record carries only the data-scope overrides; systems live on meta.

    Even a stray system key on the entry (parse_config rejects one, but this builder
    must hold on its own) is never stamped onto the record.
    """
    settings = {"process_name": "p", "origin_system": "warehouse", "dest_system": "edw"}
    entry = {"path": "x.xpt", "origin_system": "elsewhere", "dest_data_scope": ["fixture_edw"]}

    record = build_dataset_record("RAW.FOO", entry, settings)

    assert record == {
        "record_type": "origin_sas_dataset",
        "dataset": "RAW.FOO",
        "filepath": "x.xpt",
        "dest_data_scope": ["fixture_edw"],
    }


# --- Test variable extraction: known file produces expected variable records ---


def test_extract_dataset_variables_medical_claims(fixtures_dir: Path) -> None:
    """Medical claims .xpt produces expected variable records, sorted by variable name."""
    filepath = fixtures_dir / "medical_claims.xpt"
    records = extract_dataset_variables("RAW.MEDICAL_CLAIMS", filepath)

    assert len(records) == 7
    var_names = [r["variable"] for r in records]
    assert var_names == sorted(var_names)
    assert {"member_id", "claim_id", "amount"} <= set(var_names)

    required_fields = {"record_type", "dataset", "variable", "type", "format", "length", "label"}
    for rec in records:
        assert set(rec.keys()) == required_fields
        assert rec["record_type"] == "origin_sas_variable"
        assert rec["dataset"] == "RAW.MEDICAL_CLAIMS"

    amount_rec = next(r for r in records if r["variable"] == "amount")
    assert amount_rec["type"] == "num"
    assert amount_rec["format"] == ""  # .xpt files don't carry format info
    assert amount_rec["length"] == 8
    assert amount_rec["label"] == "Claim Amount"

    member_rec = next(r for r in records if r["variable"] == "member_id")
    assert member_rec["type"] == "char"
    assert member_rec["label"] == "Member Identifier"


def test_extract_dataset_variables_empty_dataset(fixtures_dir: Path) -> None:
    """Empty dataset produces variable records with no labels."""
    filepath = fixtures_dir / "empty_dataset.xpt"
    records = extract_dataset_variables("RAW.EMPTY_DATASET", filepath)

    assert len(records) == 2
    assert {r["variable"] for r in records} == {"id", "value"}
    for rec in records:
        assert rec["record_type"] == "origin_sas_variable"
        assert rec["label"] == ""
        assert rec["format"] == ""


def test_extract_dataset_variables_unsupported_extension(tmp_path: Path) -> None:
    """Unsupported file extension raises ValueError."""
    bad_file = tmp_path / "data.csv"
    bad_file.write_text("a,b\n1,2\n")

    with pytest.raises(ValueError, match="Unsupported file extension"):
        extract_dataset_variables("RAW.TEST", bad_file)


# --- Test full record assembly ---


def test_build_records_structure(fixtures_dir: Path) -> None:
    """build_records emits meta, then every dataset record, then every variable record."""
    config = {
        "settings": {
            "process_name": "test",
            "overwrite": True,
            "origin_system": "warehouse",
            "dest_system": "edw",
            "origin_data_scope": ["fixture_ocs.general"],
        },
        "datasets": {
            "RAW.PHARMACY_CLAIMS": {
                "path": str(fixtures_dir / "pharmacy_claims.xpt"),
                "origin_data_scope": ["fixture_ocs.general.clm"],
            },
            "RAW.MEDICAL_CLAIMS": {"path": str(fixtures_dir / "medical_claims.xpt")},
        },
    }

    records = build_records(config)

    # First record is meta with the process-level defaults
    assert records[0] == {
        "record_type": "meta",
        "process_name": "test",
        "origin_system": "warehouse",
        "dest_system": "edw",
        "origin_data_scope": ["fixture_ocs.general"],
    }

    # Records are grouped by type: meta, then every table, then every variable
    types = [r["record_type"] for r in records]
    assert types == ["meta"] + ["origin_sas_dataset"] * 2 + ["origin_sas_variable"] * (len(records) - 3)

    # Datasets keep sorted order within the table group
    table_datasets = [r["dataset"] for r in records if r["record_type"] == "origin_sas_dataset"]
    assert table_datasets == ["RAW.MEDICAL_CLAIMS", "RAW.PHARMACY_CLAIMS"]

    # Variables stay grouped by dataset, in the same sorted dataset order
    variable_datasets = [r["dataset"] for r in records if r["record_type"] == "origin_sas_variable"]
    assert variable_datasets == sorted(variable_datasets)

    medical_vars = [r for r in records if r["record_type"] == "origin_sas_variable" and r["dataset"] == "RAW.MEDICAL_CLAIMS"]
    assert len(medical_vars) == 7

    # Pharmacy table carries the origin_data_scope override
    pharmacy_table = next(r for r in records if r["record_type"] == "origin_sas_dataset" and r["dataset"] == "RAW.PHARMACY_CLAIMS")
    assert pharmacy_table["origin_data_scope"] == ["fixture_ocs.general.clm"]


def test_build_records_missing_file(fixtures_dir: Path) -> None:
    """A dataset whose file is missing is skipped entirely (no table or variable records)."""
    config = {
        "settings": {"process_name": "test", "overwrite": True},
        "datasets": {
            "RAW.MISSING": {"path": "nonexistent/file.sas7bdat"},
            "RAW.MEDICAL_CLAIMS": {"path": str(fixtures_dir / "medical_claims.xpt")},
        },
    }

    records = build_records(config)

    datasets_in_output = {r.get("dataset") for r in records if r["record_type"] in ("origin_sas_dataset", "origin_sas_variable")}
    assert "RAW.MEDICAL_CLAIMS" in datasets_in_output
    assert "RAW.MISSING" not in datasets_in_output


# --- Test write_jsonl ---


def test_write_jsonl_creates_output(tmp_path: Path) -> None:
    """write_jsonl creates a valid JSONL file."""
    records = [
        {"record_type": "meta", "process_name": "test"},
        {"record_type": "origin_sas_variable", "dataset": "RAW.TEST", "variable": "x", "type": "num", "format": "BEST12.", "length": 8, "label": "X var"},
    ]
    output_path = tmp_path / "subdir" / "output.jsonl"
    write_jsonl(records, output_path)

    assert output_path.exists()
    with open(output_path, encoding="utf-8") as f:
        lines = [json.loads(line) for line in f]
    assert lines[0]["record_type"] == "meta"
    assert lines[1]["variable"] == "x"


# --- Test end-to-end script behavior (overwrite / output_dir) ---


def _write_config(config_file: Path, process_name: str, overwrite: bool, datasets: dict[str, dict[str, Any]], output_dir: str | None = None) -> None:
    """Write a minimal TOML config with table-form dataset entries.

    Args:
        config_file: The path to write the config to.
        process_name: The settings.process_name value.
        overwrite: The settings.overwrite value.
        datasets: Dataset name to entry mapping; only each entry's "path" is written.
        output_dir: The settings.output_dir value. Omitted from the config when None.
    """
    lines = ["[settings]", f'process_name = "{process_name}"', f"overwrite = {str(overwrite).lower()}"]
    if output_dir:
        lines.append(f'output_dir = "{output_dir}"')
    lines += ["", "[datasets]"]
    for name, entry in datasets.items():
        lines.append(f'"{name}" = {{ path = "{entry["path"]}" }}')
    config_file.write_text("\n".join(lines) + "\n")


def test_main_overwrite_false_existing_file(tmp_path: Path, test_config_path: Path) -> None:
    """Script exits with error when output exists and overwrite is false."""
    with open(test_config_path, "rb") as f:
        config = tomllib.load(f)

    process_name = "test_no_overwrite"
    config_file = tmp_path / "no_overwrite.toml"
    output_dir = str(tmp_path / "out").replace("\\", "/")
    _write_config(config_file, process_name, False, config["datasets"], output_dir=output_dir)

    output_file = Path(output_dir) / process_name / "input_schema.jsonl"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text('{"test": true}\n')

    result = subprocess.run(
        ["uv", "run", "python", str(_EXTRACTOR), "--config", str(config_file)],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )
    assert result.returncode != 0
    assert output_file.read_text() == '{"test": true}\n'


def test_main_overwrite_true_existing_file(tmp_path: Path, test_config_path: Path) -> None:
    """Script succeeds when output exists and overwrite is true; first record is meta."""
    with open(test_config_path, "rb") as f:
        config = tomllib.load(f)

    process_name = "test_overwrite"
    config_file = tmp_path / "overwrite.toml"
    output_dir = str(tmp_path / "out").replace("\\", "/")
    _write_config(config_file, process_name, True, config["datasets"], output_dir=output_dir)

    output_file = Path(output_dir) / process_name / "input_schema.jsonl"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text('{"old": true}\n')

    result = subprocess.run(
        ["uv", "run", "python", str(_EXTRACTOR), "--config", str(config_file)],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )
    assert result.returncode == 0
    with open(output_file, encoding="utf-8") as f:
        first_record = json.loads(f.readline())
    assert first_record["record_type"] == "meta"
    assert first_record["process_name"] == process_name


def test_main_custom_output_dir(tmp_path: Path, test_config_path: Path) -> None:
    """Script writes to custom output_dir when specified in config."""
    with open(test_config_path, "rb") as f:
        config = tomllib.load(f)

    process_name = "test_custom_output"
    custom_output_dir = str(tmp_path / "custom_output").replace("\\", "/")
    config_file = tmp_path / "custom_output.toml"
    _write_config(config_file, process_name, True, config["datasets"], output_dir=custom_output_dir)

    result = subprocess.run(
        ["uv", "run", "python", str(_EXTRACTOR), "--config", str(config_file)],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )
    assert result.returncode == 0

    output_file = Path(custom_output_dir) / process_name / "input_schema.jsonl"
    assert output_file.exists()
    with open(output_file, encoding="utf-8") as f:
        first_record = json.loads(f.readline())
    assert first_record["record_type"] == "meta"


# --- Test the validation gate: the inventory publishes only after it passes ---


def _run_main(monkeypatch: pytest.MonkeyPatch, config_file: Path) -> None:
    """Invoke main() in-process against a config.

    Args:
        monkeypatch: The pytest monkeypatch fixture, used to set sys.argv.
        config_file: The config to run against, passed as --config.
    """
    monkeypatch.setattr("sys.argv", ["extract_sas_dataset_schemas.py", "--config", str(config_file)])
    main()


def _sample_config(tmp_path: Path, fixtures_dir: Path) -> Path:
    """Write a config that extracts one fixture dataset into tmp_path.

    Args:
        tmp_path: The pytest temporary directory, used as the config's output_dir.
        fixtures_dir: The committed fixture directory holding medical_claims.xpt.

    Returns:
        The config path written, for the caller to pass to _run_main.
    """
    config_file = tmp_path / "gate.toml"
    output_dir = str(tmp_path / "out").replace("\\", "/")
    path = str(fixtures_dir / "medical_claims.xpt").replace("\\", "/")
    config_file.write_text(
        '[settings]\nprocess_name = "gate"\noverwrite = true\n'
        f'output_dir = "{output_dir}"\n\n'
        f'[datasets]\n"RAW.MEDICAL_CLAIMS" = {{ path = "{path}" }}\n'
    )
    return config_file


def test_main_publishes_only_after_validation_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixtures_dir: Path
) -> None:
    """A clean run leaves the inventory at its final path and no draft beside it."""
    _run_main(monkeypatch, _sample_config(tmp_path, fixtures_dir))

    process_dir = tmp_path / "out" / "gate"
    assert (process_dir / "input_schema.jsonl").exists()
    assert list(process_dir.glob("*.draft")) == []


def test_main_exits_non_zero_and_withholds_the_output_when_validation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixtures_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed validation keeps the file off its final path, leaving the draft to debug."""
    monkeypatch.setattr(
        "extract_sas_dataset_schemas.validate_input_schemas", lambda data: ["fabricated failure"]
    )

    with caplog.at_level("ERROR"), pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, _sample_config(tmp_path, fixtures_dir))

    assert excinfo.value.code == 1
    assert "fabricated failure" in caplog.text
    process_dir = tmp_path / "out" / "gate"
    assert not (process_dir / "input_schema.jsonl").exists()
    assert [q.name for q in process_dir.glob("*.draft")] == ["input_schema.jsonl.draft"]


# --- main()'s own gates, driven in-process so the log lines can be asserted ---


def test_main_missing_config_file_exits_non_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A --config path that does not exist exits 1 naming the file it looked for."""
    with caplog.at_level("ERROR"), pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, tmp_path / "does_not_exist.toml")

    assert excinfo.value.code == 1
    assert "Config file not found" in caplog.text


def test_main_invalid_config_exits_non_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A config parse_config rejects exits 1 through main()'s invalid-config handler.

    The handler is what turns any KeyError or ValueError raised anywhere in parsing
    into one operator-facing line, so the message the run ends on is asserted here
    rather than only the exit code.
    """
    config_file = tmp_path / "empty_datasets.toml"
    config_file.write_text('[settings]\nprocess_name = "p"\noverwrite = true\n\n[datasets]\n')

    with caplog.at_level("ERROR"), pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, config_file)

    assert excinfo.value.code == 1
    assert "Invalid config" in caplog.text
    assert "[datasets] section is empty" in caplog.text


def test_main_warns_when_a_readable_dataset_has_no_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A readable dataset carrying no variables publishes, with a warning saying so.

    A SAS dataset can legitimately have no variables (`data want; stop; run;`), and the
    inventory is still worth publishing — the datasets were read, so this is not the
    broken-config case that fails the run. XPORT cannot represent a zero-column dataset
    (pyreadstat refuses to write one), so the reader is stubbed at the pyreadstat
    boundary to return that metadata for a real file.
    """
    dataset = tmp_path / "novars.xpt"
    dataset.write_bytes(b"")
    no_variables = SimpleNamespace(
        column_names=[],
        column_labels=[],
        readstat_variable_types={},
        variable_storage_width={},
        original_variable_types={},
    )

    def _stub_reader(path: str, metadataonly: bool = False) -> tuple[None, SimpleNamespace]:
        """Stand in for pyreadstat.read_xport, reporting a dataset with no variables.

        Args:
            path: The file to read; ignored, the metadata is fixed.
            metadataonly: Accepted to match the pyreadstat signature the caller uses.

        Returns:
            Tuple of (None, metadata) as pyreadstat's readers return.
        """
        return None, no_variables

    monkeypatch.setattr("extract_sas_dataset_schemas.READERS", {".xpt": _stub_reader})

    output_dir = str(tmp_path / "out").replace("\\", "/")
    dataset_path = str(dataset).replace("\\", "/")
    config_file = tmp_path / "no_variables.toml"
    config_file.write_text(
        '[settings]\nprocess_name = "novars"\noverwrite = true\n'
        f'output_dir = "{output_dir}"\n\n'
        f'[datasets]\n"RAW.NOVARS" = {{ path = "{dataset_path}" }}\n'
    )

    with caplog.at_level("WARNING"):
        _run_main(monkeypatch, config_file)

    assert "No variables extracted" in caplog.text
    with open(Path(output_dir) / "novars" / "input_schema.jsonl", encoding="utf-8") as f:
        records = [json.loads(line) for line in f]
    assert [r["record_type"] for r in records] == ["meta", "origin_sas_dataset"]


# --- The [outputs] section: config parsing ---


def _chained_config_body(
    fixtures_dir: Path, output_dir: str, outputs_entry: str | None, overwrite: bool = True
) -> str:
    """Build a config body over the committed fixtures, optionally with [outputs].

    Args:
        fixtures_dir: The committed fixture datasets directory.
        output_dir: The output_dir setting (forward slashes).
        outputs_entry: The [outputs] section body, or None to omit the section.
        overwrite: The settings.overwrite value.

    Returns:
        The TOML text.
    """
    medical = str(fixtures_dir / "medical_claims.xpt").replace("\\", "/")
    body = (
        f'[settings]\nprocess_name = "chained"\noverwrite = {str(overwrite).lower()}\n'
        f'output_dir = "{output_dir}"\n\n'
        f'[datasets]\n"RAW.MEDICAL_CLAIMS" = {{ path = "{medical}" }}\n'
    )
    if outputs_entry is not None:
        body += f"\n[outputs]\n{outputs_entry}"
    return body


def _outputs_entry(fixtures_dir: Path, name: str = "SRCLIB.KEPT_OUTPUT", extra: str = "") -> str:
    """Build one [outputs] entry over the committed pharmacy fixture.

    Args:
        fixtures_dir: The committed fixture directory holding pharmacy_claims.xpt.
        name: The dataset name to key the entry by.
        extra: Additional TOML appended inside the entry table, after `path`. Must
            carry its own leading comma (e.g. `, dest_system = "edw"`) so that the
            default of no extra keys still leaves a well-formed table.

    Returns:
        The entry as a TOML line, for the caller to embed in an [outputs] section.
    """
    pharmacy = str(fixtures_dir / "pharmacy_claims.xpt").replace("\\", "/")
    return f'"{name}" = {{ path = "{pharmacy}"{extra} }}\n'


def test_parse_config_outputs_section_is_accepted(tmp_path: Path, fixtures_dir: Path) -> None:
    """A well-formed [outputs] section parses, with the [datasets] entry shape."""
    config_file = tmp_path / "outputs.toml"
    config_file.write_text(_chained_config_body(fixtures_dir, "out", _outputs_entry(fixtures_dir)))

    config = parse_config(config_file)

    assert set(config["outputs"]) == {"SRCLIB.KEPT_OUTPUT"}
    assert config["outputs"]["SRCLIB.KEPT_OUTPUT"]["path"].endswith("pharmacy_claims.xpt")


@pytest.mark.parametrize(
    "field,value",
    [
        ("origin_system", '"warehouse"'),
        ("dest_system", '"edw"'),
        ("origin_data_scope", '["fixture_ocs.general"]'),
        ("dest_data_scope", '["fixture_edw"]'),
    ],
)
def test_parse_config_output_coordinate_key_is_rejected(
    tmp_path: Path, fixtures_dir: Path, field: str, value: str
) -> None:
    """An output entry carrying any coordinate key is rejected naming the entry.

    Outputs are inventoried, not resolved, so they take no coordinates — where the
    interface tables land in the catalog is the plan's decision, not an extraction fact.
    """
    config_file = tmp_path / "outputs.toml"
    config_file.write_text(
        _chained_config_body(fixtures_dir, "out", _outputs_entry(fixtures_dir, extra=f", {field} = {value}"))
    )

    with pytest.raises(ValueError, match=rf"Output 'SRCLIB.KEPT_OUTPUT' sets '{field}'"):
        parse_config(config_file)


def test_parse_config_dataset_in_both_sections_is_rejected(tmp_path: Path, fixtures_dir: Path) -> None:
    """A dataset name listed in both [datasets] and [outputs] is rejected naming it."""
    config_file = tmp_path / "outputs.toml"
    config_file.write_text(
        _chained_config_body(fixtures_dir, "out", _outputs_entry(fixtures_dir, name="RAW.MEDICAL_CLAIMS"))
    )

    with pytest.raises(ValueError, match=r"'RAW.MEDICAL_CLAIMS' appears in both"):
        parse_config(config_file)


def test_parse_config_empty_outputs_is_rejected(tmp_path: Path, fixtures_dir: Path) -> None:
    """An empty [outputs] table is rejected — omit the section when nothing is kept."""
    config_file = tmp_path / "outputs.toml"
    config_file.write_text(_chained_config_body(fixtures_dir, "out", ""))

    with pytest.raises(ValueError, match=r"\[outputs\] section is empty"):
        parse_config(config_file)


def test_parse_config_unknown_output_key_is_rejected(tmp_path: Path, fixtures_dir: Path) -> None:
    """An output entry key outside {path} is rejected: an output takes nothing else."""
    config_file = tmp_path / "outputs.toml"
    config_file.write_text(
        _chained_config_body(fixtures_dir, "out", _outputs_entry(fixtures_dir, extra=', kept = true'))
    )

    with pytest.raises(ValueError, match=r"Output 'SRCLIB.KEPT_OUTPUT' has unknown key\(s\) \['kept'\]"):
        parse_config(config_file)


def test_parse_config_output_missing_path_is_rejected(tmp_path: Path, fixtures_dir: Path) -> None:
    """An output entry shares the [datasets] entry shape, so 'path' is required."""
    config_file = tmp_path / "outputs.toml"
    config_file.write_text(_chained_config_body(fixtures_dir, "out", '"SRCLIB.KEPT_OUTPUT" = { }\n'))

    with pytest.raises(KeyError, match="path"):
        parse_config(config_file)


# --- The outputs inventory: record shapes and the end-to-end write ---


def test_main_outputs_section_writes_output_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixtures_dir: Path
) -> None:
    """A config with [outputs] writes output_schema.jsonl beside input_schema.jsonl.

    The outputs inventory reuses the record shapes with no coordinates anywhere: the
    meta record carries process_name alone, dataset records exactly dataset and
    filepath, and variable records the input side's shape and sort. And the input
    inventory is untouched by the section: input_schema.jsonl is byte-identical to a run
    of the same config without [outputs].
    """
    with_dir = str(tmp_path / "with").replace("\\", "/")
    without_dir = str(tmp_path / "without").replace("\\", "/")
    with_config = tmp_path / "with.toml"
    with_config.write_text(_chained_config_body(fixtures_dir, with_dir, _outputs_entry(fixtures_dir)))
    without_config = tmp_path / "without.toml"
    without_config.write_text(_chained_config_body(fixtures_dir, without_dir, None))

    _run_main(monkeypatch, with_config)
    _run_main(monkeypatch, without_config)

    process_dir = Path(with_dir) / "chained"
    assert (process_dir / "input_schema.jsonl").exists()
    outputs_file = process_dir / "output_schema.jsonl"
    assert outputs_file.exists()

    with open(outputs_file, encoding="utf-8") as f:
        records = [json.loads(line) for line in f]

    # One meta record, first, carrying process_name alone — no coordinates
    assert records[0] == {"record_type": "meta", "process_name": "chained"}

    # One dataset record per output, carrying exactly the SAS identity
    dataset_records = [r for r in records if r["record_type"] == "origin_sas_dataset"]
    assert len(dataset_records) == 1
    assert set(dataset_records[0].keys()) == {"record_type", "dataset", "filepath"}
    assert dataset_records[0]["dataset"] == "SRCLIB.KEPT_OUTPUT"
    assert dataset_records[0]["filepath"].endswith("pharmacy_claims.xpt")

    # Variable records in the input side's shape and sort
    variables = [r for r in records if r["record_type"] == "origin_sas_variable"]
    assert variables
    for rec in variables:
        assert set(rec.keys()) == {"record_type", "dataset", "variable", "type", "format", "length", "label"}
        assert rec["dataset"] == "SRCLIB.KEPT_OUTPUT"
    assert [r["variable"] for r in variables] == sorted(r["variable"] for r in variables)

    # Grouped by type: meta, then datasets, then variables
    assert [r["record_type"] for r in records] == ["meta"] + ["origin_sas_dataset"] + ["origin_sas_variable"] * len(variables)

    # The input inventory is byte-identical with or without [outputs]
    with_bytes = (process_dir / "input_schema.jsonl").read_bytes()
    without_bytes = (Path(without_dir) / "chained" / "input_schema.jsonl").read_bytes()
    assert with_bytes == without_bytes


def test_main_no_outputs_section_writes_nothing_and_removes_stale_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixtures_dir: Path
) -> None:
    """Without [outputs], no outputs file is written and a stale one is removed.

    A leftover output_schema.jsonl from a prior run no longer has a config standing
    behind it, so it must not outlive the section.
    """
    output_dir = str(tmp_path / "out").replace("\\", "/")
    config_file = tmp_path / "no_outputs.toml"
    config_file.write_text(_chained_config_body(fixtures_dir, output_dir, None))

    stale = Path(output_dir) / "chained" / "output_schema.jsonl"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text('{"record_type": "meta", "process_name": "chained"}\n')

    _run_main(monkeypatch, config_file)

    assert (Path(output_dir) / "chained" / "input_schema.jsonl").exists()
    assert not stale.exists()


def test_main_outputs_overwrite_false_leaves_the_existing_inventory_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixtures_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An existing output_schema.jsonl with overwrite false exits 1 and is left as it was.

    The input inventory is absent here, so the input-side gate passes and the outputs
    one is what has to hold — the published file the run would replace is the outputs
    inventory alone.
    """
    output_dir = str(tmp_path / "out").replace("\\", "/")
    config_file = tmp_path / "outputs_no_overwrite.toml"
    config_file.write_text(
        _chained_config_body(fixtures_dir, output_dir, _outputs_entry(fixtures_dir), overwrite=False)
    )

    existing = Path(output_dir) / "chained" / "output_schema.jsonl"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text('{"record_type": "meta", "process_name": "published"}\n')

    with caplog.at_level("ERROR"), pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, config_file)

    assert excinfo.value.code == 1
    assert "overwrite is false" in caplog.text
    assert existing.read_text() == '{"record_type": "meta", "process_name": "published"}\n'
    assert not (Path(output_dir) / "chained" / "input_schema.jsonl").exists()


def test_main_outputs_overwrite_true_replaces_the_existing_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixtures_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An existing output_schema.jsonl with overwrite true is replaced, with a warning."""
    output_dir = str(tmp_path / "out").replace("\\", "/")
    config_file = tmp_path / "outputs_overwrite.toml"
    config_file.write_text(_chained_config_body(fixtures_dir, output_dir, _outputs_entry(fixtures_dir)))

    existing = Path(output_dir) / "chained" / "output_schema.jsonl"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text('{"record_type": "meta", "process_name": "stale"}\n')

    with caplog.at_level("WARNING"):
        _run_main(monkeypatch, config_file)

    assert "overwriting" in caplog.text
    with open(existing, encoding="utf-8") as f:
        records = [json.loads(line) for line in f]
    assert records[0] == {"record_type": "meta", "process_name": "chained"}
    assert any(r["record_type"] == "origin_sas_dataset" for r in records)


def test_main_stale_outputs_inventory_is_kept_when_overwrite_is_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixtures_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Removing a stale outputs inventory obeys overwrite too — it destroys a published file.

    Without [outputs] the run would unlink a leftover output_schema.jsonl. That is
    irreversible, so overwrite = false forbids it and the whole run stops before
    anything is promoted, rather than the input inventory landing and the outputs one
    being destroyed against the declared intent.
    """
    output_dir = str(tmp_path / "out").replace("\\", "/")
    config_file = tmp_path / "stale_no_overwrite.toml"
    config_file.write_text(_chained_config_body(fixtures_dir, output_dir, None, overwrite=False))

    stale = Path(output_dir) / "chained" / "output_schema.jsonl"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text('{"record_type": "meta", "process_name": "published"}\n')

    with caplog.at_level("ERROR"), pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, config_file)

    assert excinfo.value.code == 1
    assert "Stale outputs inventory exists and overwrite is false" in caplog.text
    assert stale.read_text() == '{"record_type": "meta", "process_name": "published"}\n'
    assert not (Path(output_dir) / "chained" / "input_schema.jsonl").exists()


def test_main_unreadable_output_dataset_fails_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixtures_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unreadable output dataset exits non-zero naming the file.

    A skipped output would silently publish an incomplete ground truth for the
    interface documentation to be authored against, so it is an error — unlike an
    input, which keeps today's log-and-skip (next test).
    """
    output_dir = str(tmp_path / "out").replace("\\", "/")
    config_file = tmp_path / "bad_output.toml"
    config_file.write_text(
        _chained_config_body(fixtures_dir, output_dir, '"SRCLIB.KEPT_OUTPUT" = { path = "nonexistent/kept.xpt" }\n')
    )

    with caplog.at_level("ERROR"), pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, config_file)

    assert excinfo.value.code == 1
    assert "SRCLIB.KEPT_OUTPUT" in caplog.text
    assert "nonexistent/kept.xpt" in caplog.text
    assert not (Path(output_dir) / "chained" / "output_schema.jsonl").exists()


def test_main_unreadable_input_dataset_keeps_log_and_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixtures_dir: Path,
) -> None:
    """The same condition on an input dataset keeps today's log-and-skip behaviour."""
    output_dir = str(tmp_path / "out").replace("\\", "/")
    medical = str(fixtures_dir / "medical_claims.xpt").replace("\\", "/")
    config_file = tmp_path / "bad_input.toml"
    config_file.write_text(
        '[settings]\nprocess_name = "chained"\noverwrite = true\n'
        f'output_dir = "{output_dir}"\n\n'
        '[datasets]\n'
        f'"RAW.MEDICAL_CLAIMS" = {{ path = "{medical}" }}\n'
        '"RAW.MISSING" = { path = "nonexistent/missing.xpt" }\n'
    )

    _run_main(monkeypatch, config_file)  # completes without SystemExit

    with open(Path(output_dir) / "chained" / "input_schema.jsonl", encoding="utf-8") as f:
        records = [json.loads(line) for line in f]
    datasets = {r["dataset"] for r in records if r["record_type"] == "origin_sas_dataset"}
    assert datasets == {"RAW.MEDICAL_CLAIMS"}


def test_main_no_readable_input_dataset_fails_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Every input unreadable exits non-zero instead of publishing a meta-only inventory.

    Per-dataset skip covers partial failures, but zero readable datasets means the
    config is broken; a meta-only file passes validation, so it would otherwise be
    published as a successful, empty extraction for sas-data-resolution to consume.
    """
    output_dir = str(tmp_path / "out").replace("\\", "/")
    config_file = tmp_path / "all_missing.toml"
    config_file.write_text(
        '[settings]\nprocess_name = "chained"\noverwrite = true\n'
        f'output_dir = "{output_dir}"\n\n'
        '[datasets]\n'
        '"RAW.MISSING" = { path = "nonexistent/missing.xpt" }\n'
        '"RAW.ALSO_MISSING" = { path = "nonexistent/also_missing.xpt" }\n'
    )

    with caplog.at_level("ERROR"), pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, config_file)

    assert excinfo.value.code == 1
    assert "No dataset could be read" in caplog.text
    assert not (Path(output_dir) / "chained" / "input_schema.jsonl").exists()
    assert not (Path(output_dir) / "chained" / "input_schema.jsonl.draft").exists()


def test_build_output_records_shapes(fixtures_dir: Path) -> None:
    """build_output_records emits meta (process_name alone), bare datasets, variables."""
    config = {
        "settings": {
            "process_name": "chained",
            "overwrite": True,
            "origin_system": "warehouse",
            "dest_system": "edw",
            "origin_data_scope": ["fixture_ocs.general"],
        },
        "outputs": {"SRCLIB.KEPT_OUTPUT": {"path": str(fixtures_dir / "pharmacy_claims.xpt")}},
    }

    records = build_output_records(config)

    # The settings coordinates never leak onto the outputs meta record
    assert records[0] == {"record_type": "meta", "process_name": "chained"}
    assert records[1]["record_type"] == "origin_sas_dataset"
    assert set(records[1].keys()) == {"record_type", "dataset", "filepath"}
    assert all(r["record_type"] == "origin_sas_variable" for r in records[2:])


def test_build_output_records_unreadable_dataset_raises() -> None:
    """An unreadable output dataset raises rather than being skipped."""
    config = {
        "settings": {"process_name": "chained", "overwrite": True},
        "outputs": {"SRCLIB.KEPT_OUTPUT": {"path": "nonexistent/kept.xpt"}},
    }

    with pytest.raises(RuntimeError, match=r"'SRCLIB.KEPT_OUTPUT' \(nonexistent/kept.xpt\)"):
        build_output_records(config)


# --- Present but unreadable: the case an absent path cannot stand in for ---


def _unreadable_dataset(tmp_path: Path, kind: str) -> Path:
    """Stage a dataset path that exists but cannot be read.

    An absent path cannot stand in for either kind, because both clear the checks an
    absent one fails (Path.exists() and the suffix gate) and then fail inside pyreadstat
    — in two different layers, raising two SIBLING exceptions, neither inheriting from
    the other: garbage bytes fail in the C reader (ReadstatError), while a directory
    carrying a dataset suffix (the SAS library folder given where the member was meant)
    is rejected by pyreadstat's own pre-read check (PyreadstatError). Catching one alone
    lets the other escape both the input skip and the output wrapper.

    Args:
        tmp_path: The pytest temporary directory to stage the path in.
        kind: 'corrupt' for garbage bytes in a .xpt file, 'directory' for a directory
            named like a dataset member.

    Returns:
        The staged path.
    """
    if kind == "corrupt":
        path = tmp_path / "corrupt.xpt"
        path.write_bytes(b"not a sas file\n")
    else:
        path = tmp_path / "dir.xpt"
        path.mkdir()
    return path


@pytest.mark.parametrize("kind", ["corrupt", "directory"])
def test_main_present_but_unreadable_input_dataset_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixtures_dir: Path,
    caplog: pytest.LogCaptureFixture, kind: str,
) -> None:
    """An input that exists but cannot be read is logged and skipped, like an absent one."""
    output_dir = str(tmp_path / "out").replace("\\", "/")
    medical = str(fixtures_dir / "medical_claims.xpt").replace("\\", "/")
    bad = str(_unreadable_dataset(tmp_path, kind)).replace("\\", "/")
    config_file = tmp_path / "unreadable_input.toml"
    config_file.write_text(
        '[settings]\nprocess_name = "chained"\noverwrite = true\n'
        f'output_dir = "{output_dir}"\n\n'
        '[datasets]\n'
        f'"RAW.MEDICAL_CLAIMS" = {{ path = "{medical}" }}\n'
        f'"RAW.UNREADABLE" = {{ path = "{bad}" }}\n'
    )

    with caplog.at_level("ERROR"):
        _run_main(monkeypatch, config_file)  # completes without SystemExit

    assert "Skipping dataset RAW.UNREADABLE" in caplog.text
    with open(Path(output_dir) / "chained" / "input_schema.jsonl", encoding="utf-8") as f:
        records = [json.loads(line) for line in f]
    datasets = {r["dataset"] for r in records if r["record_type"] == "origin_sas_dataset"}
    assert datasets == {"RAW.MEDICAL_CLAIMS"}


@pytest.mark.parametrize("kind", ["corrupt", "directory"])
def test_build_output_records_present_but_unreadable_dataset_raises(tmp_path: Path, kind: str) -> None:
    """An output that exists but cannot be read fails the build, naming the dataset."""
    bad = _unreadable_dataset(tmp_path, kind)
    config = {
        "settings": {"process_name": "chained", "overwrite": True},
        "outputs": {"SRCLIB.KEPT_OUTPUT": {"path": str(bad)}},
    }

    with pytest.raises(RuntimeError, match=r"Output dataset 'SRCLIB.KEPT_OUTPUT'"):
        build_output_records(config)


# --- The outputs gate: a failed outputs validation withholds both inventories ---


def test_main_failing_outputs_validation_leaves_draft_and_exits_non_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixtures_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed outputs validation withholds both inventories, leaving the drafts.

    A file at a final path has always passed, and promotion is all-or-nothing:
    a failed outputs gate also withholds the already-validated input inventory,
    so a fresh input_schema.jsonl never lands beside a stale output_schema.jsonl.
    """
    output_dir = str(tmp_path / "out").replace("\\", "/")
    config_file = tmp_path / "gate_outputs.toml"
    config_file.write_text(_chained_config_body(fixtures_dir, output_dir, _outputs_entry(fixtures_dir)))

    monkeypatch.setattr(
        "extract_sas_dataset_schemas.validate_output_schemas", lambda data: ["fabricated outputs failure"]
    )

    with caplog.at_level("ERROR"), pytest.raises(SystemExit) as excinfo:
        _run_main(monkeypatch, config_file)

    assert excinfo.value.code == 1
    assert "fabricated outputs failure" in caplog.text
    process_dir = Path(output_dir) / "chained"
    assert not (process_dir / "output_schema.jsonl").exists()
    assert (process_dir / "output_schema.jsonl.draft").exists()
    # The input inventory validated but is withheld — promotion is all-or-nothing
    assert not (process_dir / "input_schema.jsonl").exists()
    assert (process_dir / "input_schema.jsonl.draft").exists()


# --- Split datasets: a `*` pattern in the path and the LIBNAME.DATASET key ---


def test_extract_dataset_variables_resolves_a_split_pattern(fixtures_dir: Path) -> None:
    """A `*` pattern inventories the split from one member of identical shape."""
    records = extract_dataset_variables("RAW.SPLIT_LINES_*", fixtures_dir / "split_lines_*.xpt")

    assert [r["variable"] for r in records] == ["claim_id", "lineitem", "member_id", "paid_amount"]
    assert all(r["dataset"] == "RAW.SPLIT_LINES_*" for r in records)
    lineitem = next(r for r in records if r["variable"] == "lineitem")
    assert lineitem["label"] == "Line Item Number"


def test_extract_dataset_variables_split_pattern_matches_every_member(fixtures_dir: Path) -> None:
    """Either member yields the same variable records, which is what makes reading one enough."""
    from_pattern = extract_dataset_variables("RAW.SPLIT", fixtures_dir / "split_lines_*.xpt")
    from_second = extract_dataset_variables("RAW.SPLIT", fixtures_dir / "split_lines_01.xpt")

    assert from_pattern == from_second


def test_extract_dataset_variables_split_pattern_is_resolved_deterministically(
    fixtures_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The lowest-sorting match is read, and the log names the count and the member."""
    with caplog.at_level("INFO"):
        extract_dataset_variables("RAW.SPLIT_LINES_*", fixtures_dir / "split_lines_*.xpt")

    assert "is split across 2 files" in caplog.text
    assert "reading split_lines_00.xpt" in caplog.text


def test_extract_dataset_variables_split_pattern_matching_nothing_raises(tmp_path: Path) -> None:
    """A pattern that matches no file names the pattern rather than a resolved path."""
    with pytest.raises(FileNotFoundError, match=r"No dataset file matches the pattern"):
        extract_dataset_variables("RAW.ABSENT_*", tmp_path / "absent_*.xpt")


def test_extract_dataset_variables_concrete_path_is_unaffected(fixtures_dir: Path) -> None:
    """A path without `*` takes the ordinary route, including its own error message."""
    records = extract_dataset_variables("RAW.MEDICAL_CLAIMS", fixtures_dir / "medical_claims.xpt")
    assert len(records) == 7

    with pytest.raises(FileNotFoundError, match=r"Dataset file not found"):
        extract_dataset_variables("RAW.MISSING", fixtures_dir / "missing.xpt")


def test_build_dataset_record_keeps_the_pattern_not_the_member() -> None:
    """The inventory records the pattern and the `*` key, never the file that was read."""
    settings = {"origin_data_scope": ["fixture_ocs.general"]}
    entry = {"path": "data/sas/clm_*.sas7bdat"}

    record = build_dataset_record("SRCLIB.CLM_*", entry, settings)

    assert record["dataset"] == "SRCLIB.CLM_*"
    assert record["filepath"] == "data/sas/clm_*.sas7bdat"


def test_build_records_inventories_a_split_dataset(fixtures_dir: Path) -> None:
    """An end-to-end build carries the `*` on the dataset record and its variables."""
    config = {
        "settings": {
            "process_name": "split_process",
            "overwrite": True,
            "origin_system": "warehouse",
            "dest_system": "edw",
            "origin_data_scope": ["fixture_ocs.general"],
        },
        "datasets": {
            "RAW.SPLIT_LINES_*": {"path": str(fixtures_dir / "split_lines_*.xpt")},
        },
    }

    records = build_records(config)

    dataset = next(r for r in records if r["record_type"] == "origin_sas_dataset")
    assert dataset["dataset"] == "RAW.SPLIT_LINES_*"
    assert dataset["filepath"].endswith("split_lines_*.xpt")
    variables = [r for r in records if r["record_type"] == "origin_sas_variable"]
    assert variables and all(r["dataset"] == "RAW.SPLIT_LINES_*" for r in variables)


def test_build_records_skips_a_split_dataset_matching_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, fixtures_dir: Path
) -> None:
    """An input pattern matching nothing is skipped, like any unreadable input dataset."""
    config = {
        "settings": {
            "process_name": "split_process",
            "overwrite": True,
            "origin_system": "warehouse",
            "dest_system": "edw",
            "origin_data_scope": ["fixture_ocs.general"],
        },
        "datasets": {
            "RAW.SPLIT_LINES_*": {"path": str(fixtures_dir / "split_lines_*.xpt")},
            "RAW.ABSENT_*": {"path": str(tmp_path / "absent_*.xpt")},
        },
    }

    with caplog.at_level("ERROR"):
        records = build_records(config)

    assert "Skipping dataset RAW.ABSENT_*" in caplog.text
    datasets = {r["dataset"] for r in records if r["record_type"] == "origin_sas_dataset"}
    assert datasets == {"RAW.SPLIT_LINES_*"}


def test_build_output_records_fails_on_a_split_output_matching_nothing(tmp_path: Path) -> None:
    """A kept output's pattern matching nothing fails the build rather than being skipped.

    The no-match is reported through the same wrapper any unreadable output takes, so the
    message names the output and why a missing one cannot be skipped.
    """
    config = {
        "settings": {"process_name": "split_process", "overwrite": True},
        "outputs": {"PERM.ABSENT_*": {"path": str(tmp_path / "absent_*.xpt")}},
    }

    with pytest.raises(RuntimeError, match=r"Output dataset 'PERM.ABSENT_\*'"):
        build_output_records(config)


# --- Entry paths and split notation are checked at config parse, not only on the
# --- published inventory


def _entry_config_body(section: str, key: str, path_value: str) -> str:
    """Build a minimal config placing one entry in [datasets] or [outputs].

    parse_config never touches the filesystem, so the paths here need not exist.

    Args:
        section: 'datasets' or 'outputs' — where the entry goes. An [outputs] entry
            still needs a [datasets] section, which gets an unrelated entry.
        key: The LIBNAME.DATASET key for the entry.
        path_value: The entry's path as TOML *value text*, carrying its own quoting —
            `'"a/b.xpt"'` for a basic string, `"'a\\b.xpt'"` for a literal string
            (Windows paths, where a backslash would otherwise be an escape), or an
            unquoted `"12"` to give the path a non-string type.

    Returns:
        The TOML text.
    """
    body = '[settings]\nprocess_name = "test"\noverwrite = true\n\n[datasets]\n'
    entry = f'"{key}" = {{ path = {path_value} }}\n'
    if section == "datasets":
        return body + entry
    return body + '"RAW.OTHER" = { path = "data/sas/other.sas7bdat" }\n' + f"\n[outputs]\n{entry}"


@pytest.mark.parametrize("section", ["datasets", "outputs"])
@pytest.mark.parametrize(
    "key,path_value",
    [
        ("RAW.CLM_*", '"data/sas/clm_*.sas7bdat"'),
        ("RAW.CLM", '"data/sas/clm.sas7bdat"'),
        # A Windows path reaches the same rules; the `*` is still in the filename.
        ("RAW.CLM_*", r"'D:\sasdata\clm_*.sas7bdat'"),
    ],
)
def test_parse_config_matched_split_notation_is_accepted(
    tmp_path: Path, section: str, key: str, path_value: str
) -> None:
    """A `*` on both sides, or on neither, is a well-formed entry in either section."""
    config_file = tmp_path / "split.toml"
    config_file.write_text(_entry_config_body(section, key, path_value))

    config = parse_config(config_file)

    assert key in config[section]


@pytest.mark.parametrize("section", ["datasets", "outputs"])
@pytest.mark.parametrize(
    "key,path_value,marked,bare",
    [
        ("RAW.CLM_*", '"data/sas/clm_00.sas7bdat"', "key", "path"),
        ("RAW.CLM_00", '"data/sas/clm_*.sas7bdat"', "path", "key"),
    ],
)
def test_parse_config_half_applied_split_notation_is_rejected(
    tmp_path: Path, section: str, key: str, path_value: str, marked: str, bare: str
) -> None:
    """A `*` on one side only is rejected at parse, naming which side is bare.

    Left to the inventory check alone, the same mistake surfaces only after every
    dataset has been read, and in inventory rather than config terms.
    """
    config_file = tmp_path / "split.toml"
    config_file.write_text(_entry_config_body(section, key, path_value))

    with pytest.raises(ValueError, match=rf"the {marked} carries a '\*' but the {bare} does not"):
        parse_config(config_file)


@pytest.mark.parametrize(
    "section,kind",
    [("datasets", "Dataset"), ("outputs", "Output")],
)
def test_parse_config_half_applied_split_notation_names_its_section(
    tmp_path: Path, section: str, kind: str
) -> None:
    """The rejection names the entry as a dataset or an output, matching its section."""
    config_file = tmp_path / "split.toml"
    config_file.write_text(_entry_config_body(section, "RAW.CLM_*", '"data/sas/clm_00.sas7bdat"'))

    with pytest.raises(ValueError, match=rf"{kind} 'RAW\.CLM_\*' marks the split notation"):
        parse_config(config_file)


@pytest.mark.parametrize("section", ["datasets", "outputs"])
@pytest.mark.parametrize(
    "path_value",
    [
        '"data/*/clm.sas7bdat"',  # the `*` is a directory, not a filename
        '"data/*/clm_*.sas7bdat"',  # a filename `*` does not excuse the directory one
        '"data/sas_*/clm.sas7bdat"',  # a partial directory pattern
        r"'D:\sasdata\*\clm.sas7bdat'",  # a Windows path globbing a directory
    ],
)
def test_parse_config_directory_glob_is_rejected(
    tmp_path: Path, section: str, path_value: str
) -> None:
    """A `*` outside the filename is rejected: the extractor never expands a directory.

    Unrejected, the path is looked up literally, misses, and — on the input side, where
    an unreadable dataset is skipped — drops the dataset out of the inventory silently.
    The split-notation pairing check cannot catch this: both sides carry a `*`.
    """
    config_file = tmp_path / "dirglob.toml"
    config_file.write_text(_entry_config_body(section, "RAW.CLM_*", path_value))

    with pytest.raises(ValueError, match=r"globs a directory in its path"):
        parse_config(config_file)


@pytest.mark.parametrize(
    "section,kind",
    [("datasets", "Dataset"), ("outputs", "Output")],
)
def test_parse_config_directory_glob_names_its_section(
    tmp_path: Path, section: str, kind: str
) -> None:
    """The rejection names the entry as a dataset or an output, matching its section."""
    config_file = tmp_path / "dirglob.toml"
    config_file.write_text(_entry_config_body(section, "RAW.CLM_*", '"data/*/clm.sas7bdat"'))

    with pytest.raises(ValueError, match=rf"{kind} 'RAW\.CLM_\*' globs a directory"):
        parse_config(config_file)


@pytest.mark.parametrize("section", ["datasets", "outputs"])
@pytest.mark.parametrize("path_value", ["12", "true", '["data/sas/clm.sas7bdat"]', '""'])
def test_parse_config_path_must_be_a_non_empty_string(
    tmp_path: Path, section: str, path_value: str
) -> None:
    """A non-string or empty path is rejected at parse, naming the value.

    Presence alone is not enough: an empty path resolves to the working directory and a
    non-string one fails inside Path(), both of them well after the config was accepted,
    and on the input side both are logged and skipped rather than raised.
    """
    config_file = tmp_path / "path_shape.toml"
    config_file.write_text(_entry_config_body(section, "RAW.CLM", path_value))

    with pytest.raises(ValueError, match=r"must give 'path' as a non-empty string"):
        parse_config(config_file)
