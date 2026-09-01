# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pandas",
#     "pyreadstat",
#     "python-json-logger",
# ]
# ///
"""Extract variable-level schema information from SAS datasets into JSONL format.

Reads SAS datasets (.sas7bdat or .xpt) listed in a TOML config and produces a
JSONL file with three record types:
  - meta:               one line — process_name plus any process-level catalog
                        defaults (origin_system, dest_system, origin_data_scope,
                        dest_data_scope)
  - origin_sas_dataset: one line per SAS dataset — dataset name, filepath, and any
                        data-scope (origin_data_scope / dest_data_scope) overrides
                        of the meta defaults; the systems are process-wide and
                        appear on the meta record alone
  - origin_sas_variable: one line per variable — SAS metadata (type, format,
                        length, label)

An optional [outputs] config section inventories the process's KEPT outputs — the
permanent datasets other processes consume or that are delivered — into a second
file, output_schema.jsonl, beside input_schema.jsonl. Output records use the same three
shapes but carry no catalog coordinates at all: nothing is being searched for, so a
scope would be meaningless, and where the interface tables land in the catalog is a
planning decision, not an extraction fact. Unlike an input, an unreadable output dataset FAILS the run — a kept output
silently missing would publish an incomplete ground truth for the interface
documentation authored from this file.

The script never touches metadata_db. Catalog coordinates are passed through from
the config only; any coordinate the user does not supply is simply absent, and the
downstream resolution step (sas-data-resolution) resolves them against the catalog.

Each inventory is validated before it reaches its final path, so a file at that path
has always passed; a rejected one is left beside it as `.draft`.
"""

import re
import sys
import json
import argparse
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pyreadstat

# logconfig ships beside this script and travels with the move bundle. Resolve
# against this file, never the cwd, so it imports from any working directory.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from logconfig import setup_logging, get_logger

sys.path.insert(0, str(_HERE / "data_validation"))
from data_val_extract_sas_dataset_schemas import validate_input_schemas, validate_output_schemas

logger = get_logger(__name__)

# Map pyreadstat readstat_variable_types to SAS type names
TYPE_MAP = {
    "string": "char",
    "double": "num",
}

# Supported SAS file extensions and their pyreadstat reader functions
READERS = {
    ".sas7bdat": pyreadstat.read_sas7bdat,
    ".xpt": pyreadstat.read_xport,
}

# Every way reading one dataset can fail, as one tuple, so the input side's
# log-and-skip and the output side's fail-the-run cannot come to disagree about what
# counts as unreadable. PyreadstatError and ReadstatError are SIBLINGS -- both direct
# subclasses of Exception, neither inheriting from the other -- and pyreadstat picks
# between them by where the failure happened: its own pre-read checks raise
# PyreadstatError (a path that exists but is not a readable file, e.g. the SAS library
# directory given where the member was meant, which passes both Path.exists() and the
# suffix gate), while the C reader raises ReadstatError (a truncated or corrupt file).
# Catching either alone lets the other escape past both handlers and abort the run.
READ_ERRORS = (FileNotFoundError, ValueError, pyreadstat.PyreadstatError, pyreadstat.ReadstatError)

# Catalog coordinates. Catalog ids are system-free, so system and data scope are
# separate coordinates -- but they are not uniform:
#   origin_system / dest_system — `systems` labels, PROCESS-WIDE ONLY, declared in
#       [settings]. A system is where the physical tables live AND where compute
#       happens; one process is one compute job, so it has exactly one system pair.
#       dest_system is where the converted code runs and reads; its presence marks
#       the process for conversion. A per-dataset system is rejected at config parse.
#   origin_data_scope / dest_data_scope — lists of ltree prefixes at any precision
#       from data source (1 segment) to table (3 segments), e.g.
#       ["ocs.non_institutional.clm_line"], settable in [settings] and overridable per
#       dataset. dest_data_scope's presence signals a data transition (the data
#       source changes), which is what makes the downstream resolution step consult
#       column_mappings.
# A dataset-level list REPLACES the [settings] default; it never merges with it.
# The script passes all of these through verbatim and never reads metadata_db.
META_DEFAULT_COORDS = ("origin_system", "dest_system", "origin_data_scope", "dest_data_scope")

# The system coordinates, legal in [settings] alone.
SYSTEM_COORDS = ("origin_system", "dest_system")

# Coordinates whose value is a list of ltree prefixes rather than a single label;
# the only coordinates a dataset entry may override.
DATA_SCOPE_COORDS = ("origin_data_scope", "dest_data_scope")

# The complete legal key set of each config section. Nothing here defaults or merges an
# unknown key, so a key nothing consumes is a typo, and an accepted typo is silently
# dropped -- the same outcome the per-dataset system rejection below exists to prevent.
# dest_data_scope is the dangerous one: misspelled, the dataset publishes without it,
# and the resolution step reads that absence as "no data transition" and never consults
# column_mappings, producing a confidently wrong resolution with no log line anywhere.
# A misspelled output_dir would quietly relocate both inventories to the default path.
SETTINGS_KEYS = ("process_name", "overwrite", "output_dir", *META_DEFAULT_COORDS)
DATASET_ENTRY_KEYS = ("path", *DATA_SCOPE_COORDS)
OUTPUT_ENTRY_KEYS = ("path",)

# An ltree prefix: 1-3 lowercase segments ({data_source}[.{schema}[.{table}]]). The
# catalog enforces `[a-z0-9_-]` on every id segment, so anything else cannot resolve.
# Anchored \A..\Z, not ^..$: Python's `$` also matches immediately before a trailing
# newline, so `"ocs\n"` would clear this gate. TOML can express that newline, and the
# scope is published verbatim into the inventory, where the resolver interpolates it
# into a SQL literal -- a value that can name no catalog object should be refused by
# the tool whose job is checking this config, not by the step downstream of it.
_SEGMENT = re.compile(r"\A[a-z0-9_-]+\Z")
MAX_SCOPE_SEGMENTS = 3


def parse_config(config_path: Path) -> dict[str, Any]:
    """Parse and validate the TOML config file.

    Args:
        config_path: Path to the TOML config file.

    Returns:
        Parsed config dictionary with 'settings' and 'datasets' keys, plus an
        'outputs' key when the config inventories kept outputs.

    Raises:
        tomllib.TOMLDecodeError: If the config file is not valid TOML.
        OSError: If the config file cannot be read.
        KeyError: If required config fields are missing.
        ValueError: If config structure is invalid.
    """
    logger.info(f"Reading config: {config_path}")
    try:
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        logger.error(f"Failed to parse config file: {config_path} - {e}")
        raise
    except OSError as e:
        logger.error(f"Failed to read config file: {config_path} - {e}")
        raise

    for section in ("settings", "datasets"):
        if section not in config:
            raise KeyError(f"Missing required config section: [{section}]")
    for field in ("process_name", "overwrite"):
        if field not in config["settings"]:
            raise KeyError(f"Missing required config field: settings.{field}")
    _validate_settings(config["settings"])
    if not config["datasets"]:
        raise ValueError("Config [datasets] section is empty")

    for name, entry in config["datasets"].items():
        if not isinstance(entry, dict):
            raise ValueError(f"Dataset '{name}' must be a table, e.g. {{ path = \"...\" }}")
        if "path" not in entry:
            raise KeyError(f"Dataset '{name}' is missing the required 'path' field")
        _validate_entry_path("Dataset", name, entry["path"])
        _validate_split_notation("Dataset", name, entry["path"])
        # Systems are process-wide and belong in [settings]: one process is one
        # compute job in one system pair, so only the data scopes remain overridable
        # per dataset. Rejecting, rather than silently stripping, keeps a declared
        # coordinate from being honoured by ignoring it.
        for field in SYSTEM_COORDS:
            if field in entry:
                raise ValueError(
                    f"Dataset '{name}' sets '{field}': systems are process-wide and "
                    f"belong in [settings]; only origin_data_scope and dest_data_scope "
                    f"may be overridden per dataset"
                )
        # Checked after the system rejection above, so a system key keeps its own
        # message explaining where it belongs rather than being called unknown.
        _reject_unknown_keys(f"Dataset '{name}'", entry, DATASET_ENTRY_KEYS)

    _validate_data_scopes(config["settings"], config["datasets"])
    _validate_conversion_coordinates(config["settings"], config["datasets"])
    if "outputs" in config:
        _validate_outputs(config["outputs"], config["datasets"])

    logger.info(
        f"Config loaded: process_name={config['settings']['process_name']}, "
        f"{len(config['datasets'])} datasets, {len(config.get('outputs', {}))} outputs"
    )
    return config


def _reject_unknown_keys(label: str, table: dict[str, Any], allowed: tuple[str, ...]) -> None:
    """Reject a config key that nothing in the script consumes.

    Every section's key set is closed: a key outside it is read by nothing, so accepting
    it drops a declared intention on the floor. Naming the legal set turns a misspelled
    coordinate from a silent omission into a parse-time failure.

    Args:
        label: Human-readable identifier for the table, used in the error message.
        table: The parsed config table to check.
        allowed: The keys the section consumes.

    Raises:
        ValueError: If the table carries a key outside `allowed`.
    """
    unknown = sorted(set(table) - set(allowed))
    if unknown:
        raise ValueError(
            f"{label} has unknown key(s) {unknown}: nothing reads them, so a misspelled "
            f"key would be silently dropped; legal keys are {sorted(allowed)}"
        )


def _validate_settings(settings: dict[str, Any]) -> None:
    """Enforce the key set and the value shapes of the [settings] table.

    The caller checks presence; this checks type, because every one of these values
    is consumed by a truthiness test or interpolated verbatim, where a wrong type is
    silently wrong rather than loudly wrong: `overwrite = "false"` is valid TOML and
    a truthy string, so it would enable overwriting — the opposite of the declared
    intent — and a system given as a list would mark the process for conversion and
    be published verbatim onto the meta record. The data scopes get their own shape
    check in _validate_data_scope.

    Args:
        settings: The parsed [settings] table, with process_name and overwrite present.

    Raises:
        ValueError: If the table carries a key nothing consumes, or if any setting is
            present with the wrong type or is empty.
    """
    _reject_unknown_keys("[settings]", settings, SETTINGS_KEYS)
    if not isinstance(settings["process_name"], str) or not settings["process_name"]:
        raise ValueError("settings.process_name must be a non-empty string")
    if not isinstance(settings["overwrite"], bool):
        raise ValueError(
            f"settings.overwrite must be a boolean (true/false), got "
            f"{type(settings['overwrite']).__name__}: a quoted \"false\" is a truthy string"
        )
    if "output_dir" in settings and not (isinstance(settings["output_dir"], str) and settings["output_dir"]):
        raise ValueError("settings.output_dir must be a non-empty string")
    for field in SYSTEM_COORDS:
        if field in settings and not (isinstance(settings[field], str) and settings[field]):
            raise ValueError(
                f"settings.{field} must be a non-empty string: a system is a single "
                f"`systems` label, e.g. \"edw\""
            )


def _validate_outputs(outputs: dict[str, Any], datasets: dict[str, Any]) -> None:
    """Validate the optional [outputs] table inventorying the process's kept outputs.

    Output entries share the [datasets] entry shape ({ path = "..." }) but take no
    catalog coordinates at all: outputs are inventoried, not resolved, so nothing is
    being searched for and a scope would be meaningless. Where the interface tables
    land in the catalog is the conversion plan's decision, not an extraction fact.

    Args:
        outputs: The parsed [outputs] table.
        datasets: The parsed [datasets] table (to reject a name listed in both).

    Raises:
        ValueError: If the table is empty, an entry is not a table, an entry's path is
            malformed, an entry carries a coordinate key or any other key nothing
            consumes, an entry's split notation marks only one side, or a dataset name
            appears in both sections.
        KeyError: If an entry is missing the required 'path' field.
    """
    if not outputs:
        raise ValueError(
            "Config [outputs] section is empty: omit the section entirely when the "
            "process has no kept outputs"
        )
    for name, entry in outputs.items():
        if not isinstance(entry, dict):
            raise ValueError(f"Output '{name}' must be a table, e.g. {{ path = \"...\" }}")
        if "path" not in entry:
            raise KeyError(f"Output '{name}' is missing the required 'path' field")
        _validate_entry_path("Output", name, entry["path"])
        _validate_split_notation("Output", name, entry["path"])
        for field in META_DEFAULT_COORDS:
            if field in entry:
                raise ValueError(
                    f"Output '{name}' sets '{field}': outputs are inventoried, not "
                    f"resolved, so they take no catalog coordinates — where the "
                    f"interface tables land in the catalog is the conversion plan's "
                    f"decision, not an extraction fact"
                )
        # After the coordinate rejection above, so a coordinate key keeps the message
        # explaining why an output takes none rather than being called unknown.
        _reject_unknown_keys(f"Output '{name}'", entry, OUTPUT_ENTRY_KEYS)
        if name in datasets:
            raise ValueError(
                f"Dataset '{name}' appears in both [datasets] and [outputs]: a "
                f"dataset is an input or a kept output, never both"
            )


def _validate_entry_path(kind: str, name: str, path: Any) -> None:
    """Enforce the shape of a [datasets] / [outputs] entry's path.

    The caller checks presence; this checks that the value is a non-empty string and
    that any `*` in it marks a split across files in one directory rather than across
    directories. Both failures are otherwise silent rather than loud: the extractor
    expands a `*` in the filename alone, so `data/*/clm.sas7bdat` is never globbed but
    looked up as that literal path and missed, and an empty path resolves to the
    working directory and fails on its extension. On the input side an unreadable
    dataset is logged and skipped, so either one drops the dataset out of the published
    inventory with nothing but a log line to say so.

    Args:
        kind: 'Dataset' or 'Output', naming the section the entry came from.
        name: The entry's LIBNAME.DATASET config key.
        path: The entry's configured path.

    Raises:
        ValueError: If the path is not a non-empty string, or a `*` appears anywhere
            outside the filename.
    """
    if not isinstance(path, str) or not path:
        raise ValueError(
            f"{kind} '{name}' must give 'path' as a non-empty string — the dataset's "
            f"location as the SAS environment sees it, e.g. \"data/sas/clm.sas7bdat\" — "
            f"got {path!r}"
        )
    # Both separators are honoured because the config carries the path as the SAS
    # environment sees it, which may be a POSIX or a Windows path.
    directory, _, _filename = path.replace("\\", "/").rpartition("/")
    if "*" in directory:
        raise ValueError(
            f"{kind} '{name}' globs a directory in its path '{path}': a `*` marks a "
            f"split across files within one directory and belongs in the filename "
            f"alone — a directory is never expanded, so this would match nothing"
        )


def _validate_split_notation(kind: str, name: str, path: str) -> None:
    """Enforce that split notation marks an entry's key and its path together.

    A dataset split across numbered files of identical shape carries a `*` in both its
    `LIBNAME.DATASET` key and its path. Half the pair reads as an ordinary single-file
    dataset from whichever side was left behind, which is the one thing the notation
    exists to prevent. The published inventory is checked for this too, but catching it
    here names the mistake in the config's own terms — a key and a path — and before
    any dataset has been read, rather than after every one of them has.

    Args:
        kind: 'Dataset' or 'Output', naming the section the entry came from.
        name: The entry's LIBNAME.DATASET config key.
        path: The entry's configured path, already checked by _validate_entry_path —
            a non-empty string whose only `*`, if any, is in the filename.

    Raises:
        ValueError: If a `*` appears on only one of the key and the path.
    """
    if ("*" in name) == ("*" in path):
        return
    marked, bare = ("key", "path") if "*" in name else ("path", "key")
    raise ValueError(
        f"{kind} '{name}' marks the split notation on one side only: the {marked} "
        f"carries a '*' but the {bare} does not — a split dataset marks both its "
        f"LIBNAME.DATASET key and its path"
    )


def _validate_data_scope(label: str, value: Any) -> None:
    """Enforce that an origin_data_scope / dest_data_scope value is a list of ltree prefixes.

    Each entry is 1-3 lowercase segments — a data source, a schema, or a table. The
    script never reads metadata_db, so this checks shape only; whether the prefix
    resolves to a real catalog object is the resolution step's job.

    Args:
        label: Human-readable identifier for the coordinate, used in error messages.
        value: The configured value.

    Raises:
        ValueError: If the value is not a list of well-formed ltree prefixes.
    """
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must be a non-empty list of ltree prefixes, e.g. [\"ocs.non_institutional\"]")
    for entry in value:
        if not isinstance(entry, str) or not entry:
            raise ValueError(f"{label} entries must be non-empty strings")
        segments = entry.split(".")
        if len(segments) > MAX_SCOPE_SEGMENTS:
            raise ValueError(
                f"{label} entry '{entry}' has {len(segments)} segments; at most "
                f"{MAX_SCOPE_SEGMENTS} are allowed ({{data_source}}[.{{schema}}[.{{table}}]])"
            )
        for segment in segments:
            if not _SEGMENT.match(segment):
                raise ValueError(
                    f"{label} entry '{entry}' has an invalid segment '{segment}': "
                    f"catalog id segments are lowercase [a-z0-9_-]"
                )


def _validate_data_scopes(settings: dict[str, Any], datasets: dict[str, Any]) -> None:
    """Validate every origin_data_scope / dest_data_scope value in the config.

    Args:
        settings: The parsed [settings] table.
        datasets: The parsed [datasets] table.

    Raises:
        ValueError: If any scope value is malformed.
    """
    for field in DATA_SCOPE_COORDS:
        if field in settings:
            _validate_data_scope(f"settings.{field}", settings[field])
    for name, entry in datasets.items():
        for field in DATA_SCOPE_COORDS:
            if field in entry:
                _validate_data_scope(f"dataset '{name}' {field}", entry[field])


def _validate_conversion_coordinates(settings: dict[str, Any], datasets: dict[str, Any]) -> None:
    """Enforce that a process intended for conversion can be resolved downstream.

    A settings-level dest_system marks the whole process for conversion — systems are
    process-wide, so the mark cannot be per dataset — and it may equal the origin
    system. Resolution locates each variable's catalog columns from origin_data_scope
    and resolves physical names in the systems, so a converted process needs a
    settings-level origin_system, and every dataset needs an effective origin_data_scope
    (its own override or the [settings] default). dest_data_scope is always optional:
    absent means the data source does not change, so no column mappings are consulted.

    Args:
        settings: The parsed [settings] table.
        datasets: The parsed [datasets] table.

    Raises:
        ValueError: If a converted process has no origin_system, or one of its datasets
            has no effective origin_data_scope.
    """
    if not settings.get("dest_system"):
        return  # inventory-only process; no conversion coordinates required
    if not settings.get("origin_system"):
        raise ValueError(
            "origin_system is required: [settings] declares a dest_system (conversion "
            "is intended), so origin_system must be set in [settings] alongside it"
        )
    for name, entry in datasets.items():
        if not (entry.get("origin_data_scope") or settings.get("origin_data_scope")):
            raise ValueError(
                f"origin_data_scope is required for dataset '{name}': the process has a "
                f"dest_system (conversion is intended), so origin_data_scope must be set in "
                f"[settings] as a default or on the dataset entry"
            )


def build_meta_record(settings: dict[str, Any]) -> dict[str, Any]:
    """Build the single meta record from [settings].

    Carries process_name plus any process-level catalog defaults that were set.

    Args:
        settings: The parsed [settings] table.

    Returns:
        The meta record.
    """
    record: dict[str, Any] = {"record_type": "meta", "process_name": settings["process_name"]}
    for field in META_DEFAULT_COORDS:
        if settings.get(field):
            record[field] = settings[field]
    return record


def build_dataset_record(dataset_name: str, entry: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    """Build a dataset record for one SAS dataset.

    Carries the SAS identity (dataset, filepath) and only the data-scope overrides —
    the defaults themselves live on the meta record, and the systems live there alone,
    so a dataset record never carries a system field. The consumer resolves an
    effective value as table.get(field) or meta.get(field). A dataset-level
    origin_data_scope / dest_data_scope list REPLACES the meta default outright; the two are
    never merged, so a dataset can narrow the process-wide scope.

    Args:
        dataset_name: The SAS LIBNAME.DATASET key from the config.
        entry: The dataset's config table (path plus optional scope overrides).
        settings: The parsed [settings] table (source of the meta defaults).

    Returns:
        The dataset record.
    """
    record: dict[str, Any] = {
        "record_type": "origin_sas_dataset",
        "dataset": dataset_name,
        "filepath": str(entry["path"]),
    }
    for field in DATA_SCOPE_COORDS:
        value = entry.get(field)
        if value and value != settings.get(field):
            record[field] = value
    return record


def extract_dataset_variables(dataset_name: str, filepath: Path) -> list[dict[str, Any]]:
    """Extract variable-level SAS metadata from a single SAS dataset.

    A `*` in the filename marks a dataset split across numbered files of identical
    shape (`clm_*.sas7bdat`). Every file carries the same variables, so one is read to
    inventory them all: the lowest-sorting match, so a rerun reads the same file. The
    caller keeps recording the pattern rather than the file read -- the split is a fact
    about the dataset, and which member was opened is not.

    Args:
        dataset_name: Logical name for the dataset (from config).
        filepath: Path to the .sas7bdat or .xpt file, or a `*` pattern matching the
            files of one split dataset and nothing else.

    Returns:
        List of variable records, one per variable, sorted by variable name.

    Raises:
        FileNotFoundError: If the dataset file does not exist, or a `*` pattern matches
            nothing.
        ValueError: If the file extension is not supported.
        pyreadstat.PyreadstatError: If pyreadstat rejects the path before reading it —
            notably a path that exists but is not a readable file.
        pyreadstat.ReadstatError: If the file cannot be read by pyreadstat.
    """
    if "*" in filepath.name:
        # Sorted so the choice is stable across runs: every member has the same shape,
        # so which one is read cannot change the inventory -- but if that premise is
        # ever violated, a stable choice makes the divergence reproducible.
        matches = sorted(filepath.parent.glob(filepath.name))
        if not matches:
            raise FileNotFoundError(f"No dataset file matches the pattern: {filepath}")
        # The count is what tells an operator the pattern caught what they meant: three
        # matches where a hundred were expected is a typo this line surfaces.
        logger.info(
            f"{dataset_name} is split across {len(matches)} files matching "
            f"{filepath.name}; reading {matches[0].name}"
        )
        filepath = matches[0]

    if not filepath.exists():
        raise FileNotFoundError(f"Dataset file not found: {filepath}")

    suffix = filepath.suffix.lower()
    if suffix not in READERS:
        raise ValueError(f"Unsupported file extension: {suffix}. Supported: {list(READERS.keys())}")

    reader = READERS[suffix]
    logger.info(f"Reading metadata: {dataset_name} ({filepath})")

    try:
        _, meta = reader(str(filepath), metadataonly=True)
    except (pyreadstat.PyreadstatError, pyreadstat.ReadstatError) as e:
        logger.error(f"Failed to read metadata from {filepath}: {e}")
        raise

    records = []
    for var_name, label in zip(meta.column_names, meta.column_labels, strict=True):
        readstat_type = meta.readstat_variable_types.get(var_name, "")
        sas_type = TYPE_MAP.get(readstat_type, readstat_type)
        length = meta.variable_storage_width.get(var_name, 0)
        sas_format = meta.original_variable_types.get(var_name) or ""

        records.append({
            "record_type": "origin_sas_variable",
            "dataset": dataset_name,
            "variable": var_name,
            "type": sas_type,
            "format": sas_format,
            "length": length,
            "label": label or "",
        })

    records.sort(key=lambda r: r["variable"])
    logger.info(f"Extracted {len(records)} variables from {dataset_name}")
    return records


def build_records(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Build the full ordered record list for the JSONL output.

    Records are grouped by type: the meta record, then every dataset record, then every
    variable record. Within each group datasets keep their sorted order, and variables
    keep the per-dataset sort applied by extract_dataset_variables. Grouping by type
    lets a consumer read the whole inventory of datasets before any variable, so the
    effective coordinates are known up front. A dataset whose file cannot be read is
    logged and skipped entirely (no orphan dataset record).

    Args:
        config: Parsed TOML config dictionary.

    Returns:
        Ordered list of meta, dataset, and variable records.
    """
    settings = config["settings"]
    datasets = config["datasets"]

    dataset_records: list[dict[str, Any]] = []
    variables: list[dict[str, Any]] = []

    for dataset_name in sorted(datasets):
        entry = datasets[dataset_name]
        try:
            dataset_variables = extract_dataset_variables(dataset_name, Path(entry["path"]))
        except READ_ERRORS as e:
            logger.error(f"Skipping dataset {dataset_name}: {e}")
            continue
        dataset_records.append(build_dataset_record(dataset_name, entry, settings))
        variables.extend(dataset_variables)

    records: list[dict[str, Any]] = [build_meta_record(settings), *dataset_records, *variables]

    logger.info(f"Built {len(records)} records ({len(dataset_records)} dataset, {len(variables)} variable)")
    return records


def build_output_records(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Build the ordered record list for the output_schema.jsonl outputs inventory.

    Same grouping as the input inventory — one meta record, then every dataset
    record, then every variable record — but the records carry no catalog
    coordinates: the meta record holds process_name alone, and each dataset record
    holds only the SAS identity (dataset, filepath). Variable records are identical
    in shape and sort to the input side's.

    Unlike an input, an unreadable output dataset FAILS the build instead of being
    skipped: a kept output silently missing from the inventory would undermine
    exactly the ground-truth role output_schema.jsonl exists for — the interface
    documentation is authored from it.

    Args:
        config: Parsed TOML config dictionary (with an 'outputs' key).

    Returns:
        Ordered list of meta, dataset, and variable records.

    Raises:
        RuntimeError: If any output dataset cannot be read, naming the file.
    """
    outputs = config["outputs"]

    dataset_records: list[dict[str, Any]] = []
    variables: list[dict[str, Any]] = []

    for dataset_name in sorted(outputs):
        entry = outputs[dataset_name]
        try:
            dataset_variables = extract_dataset_variables(dataset_name, Path(entry["path"]))
        except READ_ERRORS as e:
            raise RuntimeError(
                f"Output dataset '{dataset_name}' ({entry['path']}) could not be "
                f"read: {e} — a kept output missing from the inventory would publish "
                f"an incomplete ground truth, so this fails the run"
            ) from e
        dataset_records.append({
            "record_type": "origin_sas_dataset",
            "dataset": dataset_name,
            "filepath": str(entry["path"]),
        })
        variables.extend(dataset_variables)

    meta: dict[str, Any] = {"record_type": "meta", "process_name": config["settings"]["process_name"]}
    records: list[dict[str, Any]] = [meta, *dataset_records, *variables]

    logger.info(
        f"Built {len(records)} output records ({len(dataset_records)} dataset, {len(variables)} variable)"
    )
    return records


def write_jsonl(records: list[dict[str, Any]], output_path: Path) -> None:
    """Write records to a JSONL file.

    Args:
        records: List of record dictionaries.
        output_path: Path to the output JSONL file.

    Raises:
        OSError: If the output directory cannot be created or the file cannot be written.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")
    logger.info(f"Wrote {len(records)} records to {output_path}")


def _stage_draft(records: list[dict[str, Any]], output_path: Path, validator: Callable[[Path], list[str]]) -> Path:
    """Write records to a validated draft beside their final path.

    Validates before the file can reach its final path, so a file at that path has
    always passed. Promotion is the caller's step — a run producing several
    inventories stages them all before promoting any, so a late failure never
    leaves a fresh file beside a stale sibling.

    Args:
        records: The record list to stage.
        output_path: The final path the draft is destined for; the draft is
            written beside it.
        validator: The validation function to run against the draft (returns a list
            of error messages; empty means the draft passes).

    Returns:
        The validated draft path, ready to promote via ``Path.replace``.

    Raises:
        OSError: Propagated from write_jsonl when the draft cannot be written.
        SystemExit: Non-zero when validation fails; the rejected draft is left
            in place for debugging.
    """
    draft_path = output_path.with_suffix(output_path.suffix + ".draft")
    write_jsonl(records, draft_path)
    errors = validator(draft_path)
    if errors:
        for error in errors:
            logger.error(f"VALIDATION FAILED: {error}")
        logger.error(
            f"{len(errors)} validation error(s); the rejected output is at {draft_path}"
        )
        sys.exit(1)
    return draft_path


def _require_writable(
    path: Path, overwrite: bool, description: str = "Output file", fate: str = "overwriting"
) -> None:
    """Enforce settings.overwrite for one published file this run would destroy.

    Every path the run may replace OR remove passes through here, so the one setting
    whose whole purpose is to stop the script destroying a published file cannot be
    honoured at one call site and forgotten at the next — which is how the stale-outputs
    removal came to bypass it. Callers check every such path before anything is
    promoted, so a refusal costs nothing that has already been published.

    Args:
        path: The file the run would replace or remove. Absent is always writable.
        overwrite: The settings.overwrite value.
        description: What the file is, for the log lines.
        fate: What the run will do to it, for the warning line.

    Raises:
        SystemExit: Non-zero if the file exists and overwrite is false.
    """
    if not path.exists():
        return
    if not overwrite:
        logger.error(f"{description} exists and overwrite is false: {path}")
        sys.exit(1)
    logger.warning(f"{description} exists, {fate}: {path}")


def main() -> None:
    """Extract SAS dataset schemas based on TOML config."""
    parser = argparse.ArgumentParser(description="Extract variable-level schema from SAS datasets")
    parser.add_argument("--config", type=Path, required=True, help="Path to TOML configuration file")
    args = parser.parse_args()

    setup_logging(log_dir="logs/sas_parsing")
    logger.info("=" * 60)

    try:
        config_path = args.config
        if not config_path.exists():
            logger.error(f"Config file not found: {args.config}")
            sys.exit(1)

        config = parse_config(config_path)

        # Determine output paths — the outputs inventory lands beside the input one
        process_name = config["settings"]["process_name"]
        overwrite = config["settings"]["overwrite"]
        output_dir = config["settings"].get("output_dir", "docs/activities/sas_conversion")
        output_path = Path(f"{output_dir}/{process_name}/input_schema.jsonl")
        outputs_path = Path(f"{output_dir}/{process_name}/output_schema.jsonl")

        _require_writable(output_path, overwrite)
        # The outputs inventory is governed by the same rule whether this run replaces
        # it (the config has [outputs]) or removes it as stale (the config does not):
        # either way a published file is destroyed. Both cases are checked here, before
        # any inventory is promoted, so a refusal never leaves a half-published run.
        if "outputs" in config:
            _require_writable(outputs_path, overwrite)
        else:
            _require_writable(
                outputs_path, overwrite, description="Stale outputs inventory", fate="removing"
            )

        records = build_records(config)
        dataset_count = sum(1 for r in records if r["record_type"] == "origin_sas_dataset")
        variable_count = sum(1 for r in records if r["record_type"] == "origin_sas_variable")
        if dataset_count == 0:
            # Per-dataset log-and-skip stands for partial failures, but zero readable
            # datasets means the config is broken (an empty [datasets] is already
            # rejected at parse): a meta-only inventory passes validation, so it would
            # otherwise be published as a successful, empty extraction for
            # sas-data-resolution to consume.
            logger.error("No dataset could be read; nothing to publish")
            sys.exit(1)
        if variable_count == 0:
            logger.warning("No variables extracted from any dataset")

        input_draft = _stage_draft(records, output_path, validate_input_schemas)

        if "outputs" in config:
            # An unreadable output dataset raises out of build_output_records and
            # fails the run — never a log-and-skip like an input (see its docstring).
            output_records = build_output_records(config)
            output_dataset_count = sum(1 for r in output_records if r["record_type"] == "origin_sas_dataset")
            output_variable_count = sum(1 for r in output_records if r["record_type"] == "origin_sas_variable")
            outputs_draft = _stage_draft(output_records, outputs_path, validate_output_schemas)
            input_draft.replace(output_path)
            outputs_draft.replace(outputs_path)
            logger.info(
                f"SUCCESS: {variable_count} input variables extracted to {output_path}; "
                f"{output_variable_count} output variables ({output_dataset_count} datasets) "
                f"inventoried to {outputs_path}"
            )
        else:
            input_draft.replace(output_path)
            # No [outputs] section: a leftover outputs inventory from a prior run is
            # stale — its config no longer stands behind it — so it must not outlive it.
            # The overwrite gate for this removal ran above, before anything was
            # promoted; reaching here means the run is allowed to destroy the file.
            if outputs_path.exists():
                outputs_path.unlink()
                logger.warning(f"Removed stale outputs inventory (config has no [outputs]): {outputs_path}")
            logger.info(f"SUCCESS: {variable_count} variables extracted to {output_path}")
    except (KeyError, ValueError) as e:
        logger.error(f"Invalid config: {e}")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"Failed: {e}")
        sys.exit(1)
    finally:
        logger.info("=" * 60)


if __name__ == "__main__":
    main()
