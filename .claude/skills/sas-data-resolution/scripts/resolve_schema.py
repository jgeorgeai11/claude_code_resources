"""Resolve a SAS variable inventory against metadata_db into input_schema_resolution.jsonl.

Deterministic implementation of the sas-data-resolution skill: it consumes the
`input_schema.jsonl` produced by sas-variable-extraction and, using only queries against
metadata_db (through the MCP HTTP server), writes the resolution the skill
specifies -- origin columns, mapping candidates and statuses, the tables in play
with their physical addresses and grain, joins, and concepts.

The script decides nothing the catalog cannot decide. A variable matching several
tables carries every match, and a column with several mappings carries every
candidate; choosing between them is the planning step's job. That is what makes a
scripted run interchangeable with the agent-driven one, and it is why the same
inventory always yields a byte-identical file.

A published resolution is a complete account of the SAS input: every variable
carries at least one origin column, every in-transition column carries at least one
usable candidate, and every table in play is deployed where the process needs it.
When the catalog cannot fully account for the input, the run fails once with every
gap named, writing the machine-readable work order `input_schema_catalog_gaps.jsonl` beside the
never-written resolution.


Usage:
    uv run .claude/skills/sas-data-resolution/scripts/resolve_schema.py \
        --config .claude/skills/sas-data-resolution/scripts/config/resolve_schema.toml
"""

import os
import re
import sys
import json
import argparse
import tomllib
from pathlib import Path
from collections.abc import Callable
from typing import Any
from collections import defaultdict

from dotenv import load_dotenv

# Everything this script imports -- logconfig included -- ships beside it in this
# skill, so one anchored path reaches all of it. Resolve against this file, never
# the cwd, so this module imports from any working directory.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from logconfig import setup_logging, get_logger
from mcp_client import MCPClient

sys.path.insert(0, str(_HERE / "data_validation"))
from data_val_schema_resolution import validate_schema_resolution
from data_val_catalog_gaps import validate_catalog_gaps

logger = get_logger(__name__)

# The four catalog coordinates, in the order they appear on the meta record. The
# systems are process-wide only (meta declares them; a dataset record carrying one is
# rejected) and BOTH are required -- deployment cannot be resolved against an
# undeclared system. The data scopes may be narrowed per dataset.
COORDINATES = ("origin_system", "dest_system", "origin_data_scope", "dest_data_scope")
SYSTEM_COORDINATES = ("origin_system", "dest_system")
DATA_SCOPE_COORDINATES = ("origin_data_scope", "dest_data_scope")

# Catalog id segments are lowercase `[a-z0-9_-]`, so anything else cannot resolve and
# must never reach a query: values are interpolated as SQL literals (run_sql takes no
# bind parameters), so this is the injection guard as well as a shape check. Anchored
# with \A and \Z, not ^ and $: `$` also matches before a trailing newline, so "ocs\n"
# would clear the guard and reach the database as a literal.
_SEGMENT = re.compile(r"\A[a-z0-9_-]+\Z")
MAX_SCOPE_SEGMENTS = 3

# Config defaults. input_schema and overwrite must be stated; output_dir defaults to
# the conversion's own folder, where the SAS documentation and the plan also live.
REQUIRED_CONFIG_FIELDS = ("input_schema", "overwrite")
DEFAULT_OUTPUT_DIR = "docs/activities/sas_conversion"
DEFAULT_MCP_URL = "http://localhost:8002/mcp"
DEFAULT_MCP_TIMEOUT_S = 60.0
DEFAULT_MCP_DATABASE = "metadata_db"
DEFAULT_MCP_TOKEN_ENV = "MCP_METADATA_DB_TOKEN"

OUTPUT_FILENAME = "input_schema_resolution.jsonl"
GAPS_FILENAME = "input_schema_catalog_gaps.jsonl"


class CoordinateError(ValueError):
    """Raised when a system or data scope is missing, malformed, or unresolvable.

    One raise can name several coordinates, so the individual problems are carried
    structurally rather than left to be recovered by splitting the rendered message.

    Attributes:
        problems: One entry per coordinate problem the message summarizes.
    """

    def __init__(self, message: str, problems: list[str] | None = None) -> None:
        """Initialize the error with its individual coordinate problems.

        Args:
            message: The human-readable summary, naming every problem.
            problems: One entry per problem. Defaults to the single-problem
                `[message]`, which is the shape of most raises.
        """
        super().__init__(message)
        self.problems = [message] if problems is None else problems


class InventoryError(ValueError):
    """Raised when the input inventory is missing or malformed."""


class CatalogGapError(ValueError):
    """Raised when the catalog cannot fully account for the SAS input.

    A gap is a variable matching no origin column, an in-transition column with no
    usable candidate (including one whose only mappings are broken catalog rows, or
    read a dest column the catalog does not document), or a table in play undeployed
    where the process needs it. Gaps accumulate across the whole run and raise once,
    so a single failure names the complete catalog work order.

    Attributes:
        gaps: The structured gap records destined for input_schema_catalog_gaps.jsonl.
    """

    def __init__(self, message: str, gaps: list[dict[str, Any]]) -> None:
        """Initialize the error with its structured work order.

        Args:
            message: The human-readable summary, carrying counts and every gap.
            gaps: One record per gap (missing_variable / missing_candidate /
                missing_deployment).
        """
        super().__init__(message)
        self.gaps = gaps


# --- Config ---


def parse_config(config_path: Path) -> dict[str, Any]:
    """Parse and validate the TOML config file.

    Args:
        config_path: Path to the TOML config file.

    Returns:
        The config with MCP defaults filled in.

    Raises:
        tomllib.TOMLDecodeError: If the config file is not valid TOML.
        OSError: If the config file cannot be read.
        ValueError: If a required config field is missing.
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

    for field in REQUIRED_CONFIG_FIELDS:
        if field not in config:
            raise ValueError(f"Missing required config field: {field}")

    config.setdefault("output_dir", DEFAULT_OUTPUT_DIR)
    config.setdefault("mcp_url", DEFAULT_MCP_URL)
    config.setdefault("mcp_timeout_s", DEFAULT_MCP_TIMEOUT_S)
    config.setdefault("mcp_database", DEFAULT_MCP_DATABASE)
    config.setdefault("mcp_token_env", DEFAULT_MCP_TOKEN_ENV)

    logger.info(
        f"Config loaded: input_schema={config['input_schema']}, "
        f"output_dir={config['output_dir']}, mcp_url={config['mcp_url']}"
    )
    return config


# --- Inventory ---


def load_inventory(input_schema: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Read the extraction inventory into its meta, dataset, and variable records.

    Args:
        input_schema: Path to the input_schema.jsonl produced by sas-variable-extraction.

    Returns:
        Tuple of (meta record, origin_sas_dataset records, origin_sas_variable records).

    Raises:
        InventoryError: If the file is missing, unparseable, has no meta record, or
            carries no dataset or variable record.
        OSError: If the file cannot be read.
    """
    if not input_schema.exists():
        raise InventoryError(f"Input schema not found: {input_schema}")

    records: list[dict[str, Any]] = []
    with input_schema.open("r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise InventoryError(f"{input_schema} line {line_num}: invalid JSON - {e}") from e

    metas = [r for r in records if r.get("record_type") == "meta"]
    if len(metas) != 1:
        raise InventoryError(f"Expected exactly one meta record in {input_schema}, found {len(metas)}")
    datasets = [r for r in records if r.get("record_type") == "origin_sas_dataset"]
    variables = [r for r in records if r.get("record_type") == "origin_sas_variable"]
    if not datasets:
        raise InventoryError(f"Inventory has no origin_sas_dataset records: {input_schema}")
    # An inventory of empty datasets is a producible input -- the extractor logs
    # "No variables extracted from any dataset" as a WARNING and publishes anyway -- and
    # resolving one would publish a byte-stable, validated file that accounts for
    # nothing, contradicting the completeness contract. There is no partial success.
    if not variables:
        raise InventoryError(f"Inventory has no origin_sas_variable records: {input_schema}")

    logger.info(f"Loaded inventory: {len(datasets)} datasets, {len(variables)} variables")
    return metas[0], datasets, variables


def resolve_coordinates(meta: dict[str, Any], dataset: dict[str, Any]) -> dict[str, Any]:
    """Resolve one dataset's effective data scopes.

    A dataset-level value REPLACES the meta default rather than merging with it, so a
    dataset can narrow the process-wide scope. Absent on both sides resolves to None,
    which for dest_data_scope means "no data transition". Only the data scopes resolve
    per dataset: the systems are process-wide and live on the meta record alone.

    Args:
        meta: The inventory's meta record, holding the process-wide defaults.
        dataset: One inventory origin_sas_dataset record.

    Returns:
        A mapping of the two data scopes to their effective values.
    """
    return {
        field: dataset[field] if field in dataset else meta.get(field)
        for field in DATA_SCOPE_COORDINATES
    }


def require_meta_only_systems(dataset_records: list[dict[str, Any]]) -> None:
    """Reject an inventory whose dataset records carry system coordinates.

    Deployment is resolved once over the pooled dest tables, so a process has exactly
    one system pair by construction; a per-dataset system could only contradict it. The
    extractor emits none, so this is a backstop for inventories produced by any other
    means.

    Args:
        dataset_records: The inventory's origin_sas_dataset records.

    Raises:
        InventoryError: If any dataset record carries origin_system or dest_system.
    """
    for record in dataset_records:
        for field in SYSTEM_COORDINATES:
            if field in record:
                raise InventoryError(
                    f"Dataset '{record.get('dataset')}' carries '{field}': systems are "
                    f"process-wide and belong on the meta record alone"
                )


def _scope_key(scope: list[str] | None) -> tuple[str, ...]:
    """Reduce a data scope list to a canonical, order-independent key.

    Args:
        scope: An origin_data_scope / dest_data_scope list, or None.

    Returns:
        The scope's distinct entries, sorted; empty when the scope is absent.
    """
    return tuple(sorted(set(scope or ())))


def validate_scope_entry(label: str, entry: Any) -> None:
    """Enforce that a coordinate value is a well-formed catalog id or prefix.

    Values are interpolated into SQL literals, so this runs before any query is built.

    Args:
        label: Human-readable identifier for the coordinate, used in error messages.
        entry: The value to check.

    Raises:
        CoordinateError: If the value is not 1-3 lowercase `[a-z0-9_-]` segments.
    """
    if not isinstance(entry, str) or not entry:
        raise CoordinateError(f"{label} must be a non-empty string, got {entry!r}")
    segments = entry.split(".")
    if len(segments) > MAX_SCOPE_SEGMENTS:
        raise CoordinateError(
            f"{label} '{entry}' has {len(segments)} segments; at most {MAX_SCOPE_SEGMENTS} "
            f"are allowed ({{data_source}}[.{{schema}}[.{{table}}]])"
        )
    for segment in segments:
        if not _SEGMENT.match(segment):
            raise CoordinateError(
                f"{label} '{entry}' has an invalid segment '{segment}': "
                f"catalog id segments are lowercase [a-z0-9_-]"
            )


def partition_variable_names(
    variable_records: list[dict[str, Any]], coordinates_by_dataset: dict[str, dict[str, Any]]
) -> tuple[dict[tuple[str, ...], list[str]], list[str]]:
    """Pool the SQL-eligible variable names per origin_data_scope, and name the exclusions.

    The columns query is filtered to these names, so they are interpolated as SQL
    literals -- and unlike the coordinates, the inventory is not this script's to
    validate: SAS permits a variable name to contain anything under
    `validvarname=any`. A name is eligible only when it matches the catalog id segment
    charset, which is exactly the set a catalog column name can be drawn from.

    An ineligible name is EXCLUDED from the predicate rather than raising. It could
    never have matched a catalog column, so leaving it out of the query resolves it to
    no origin column -- the ordinary zero-match path, surfacing as a `missing_variable`
    catalog gap, which is what such a name produces today. Raising instead would invent
    a failure mode for an inventory that is a faithful record of its source dataset.

    Names are pooled as the union across the datasets sharing a scope, which is what
    keeps one columns query per distinct origin_data_scope: the query builds a search
    space, and `match_origin_columns` still decides per variable what it matched, so a
    name pooled in on another dataset's behalf cannot leak into this one's resolution.

    Args:
        variable_records: The inventory's origin_sas_variable records. A record's
            `variable` is a string by contract -- `match_origin_columns` reads it as
            one -- so it is used as one here rather than coerced, which would only
            postpone a non-string to the matching pass and spend a query on the way.
        coordinates_by_dataset: Effective data scopes per dataset name. A variable
            naming a dataset absent from it is skipped; the matching loop raises on it.

    Returns:
        Tuple of (eligible names by scope key -- lowercased, deduplicated, and sorted so
        the statement is byte-stable across runs; the excluded names, sorted).
    """
    pooled: dict[tuple[str, ...], set[str]] = defaultdict(set)
    excluded: set[str] = set()
    for record in variable_records:
        coords = coordinates_by_dataset.get(record["dataset"])
        if coords is None:
            continue
        name = record["variable"]
        lowered = name.casefold()
        if _SEGMENT.match(lowered):
            pooled[_scope_key(coords["origin_data_scope"])].add(lowered)
        else:
            excluded.add(name)
    return {scope: sorted(names) for scope, names in pooled.items()}, sorted(excluded)


def collect_coordinates(
    meta: dict[str, Any], coordinates_by_dataset: dict[str, dict[str, Any]]
) -> tuple[list[str], list[str]]:
    """Collect the distinct systems and data scope entries to check.

    The systems come from the meta record alone — they are process-wide — and BOTH
    must be declared: deployment cannot be resolved against an undeclared system, and
    every published file carries both. The data scope entries pool across every
    dataset's effective scopes.

    Args:
        meta: The inventory's meta record, holding the process-wide systems.
        coordinates_by_dataset: Effective data scopes per dataset name.

    Returns:
        Tuple of (sorted distinct systems, sorted distinct data scope entries).

    Raises:
        CoordinateError: If either system is undeclared, a coordinate is malformed,
            or a dataset has no effective origin data scope.
    """
    systems: set[str] = set()
    for field in SYSTEM_COORDINATES:
        value = meta.get(field)
        if not value:
            raise CoordinateError(
                f"meta {field} is required: deployment cannot be resolved against an "
                f"undeclared system, and every published file carries both systems"
            )
        validate_scope_entry(f"meta {field}", value)
        systems.add(value)

    scopes: set[str] = set()
    for name, coords in coordinates_by_dataset.items():
        for field in DATA_SCOPE_COORDINATES:
            value = coords.get(field)
            if value is None:
                continue
            if not isinstance(value, list) or not value:
                raise CoordinateError(
                    f"dataset '{name}' {field} must be a non-empty list of ltree prefixes"
                )
            for entry in value:
                validate_scope_entry(f"dataset '{name}' {field} entry", entry)
                scopes.add(entry)
        if not coords.get("origin_data_scope"):
            raise CoordinateError(f"dataset '{name}' has no effective origin_data_scope to resolve against")
    return sorted(systems), sorted(scopes)


# --- Query building ---


def _sql_literals(values: list[str]) -> str:
    """Render values as a comma-separated list of SQL string literals.

    Three classes of value reach this function, and each is quote-free before it does:
    a coordinate that passed validate_scope_entry, an id the catalog itself returned
    (ltree ids and regex-extracted column names cannot contain quotes), and a SAS
    variable name that passed the eligibility guard in `partition_variable_names` --
    the inventory is the one source this script does not control, so its names are
    admitted only when they match the catalog id segment charset.

    Args:
        values: The already-validated values.

    Returns:
        The literal list, e.g. `'a','b'`.
    """
    return ",".join(f"'{value}'" for value in values)


def build_coordinate_check_sql(systems: list[str], coordinates: list[str]) -> str:
    """Build the coordinate-check query.

    Args:
        systems: The distinct origin and destination systems.
        coordinates: The distinct origin_data_scope and dest_data_scope entries.

    Returns:
        The SQL statement.
    """
    system_array = f"array[{_sql_literals(systems)}]::text[]"
    coordinate_array = f"array[{_sql_literals(coordinates)}]::text[]"
    return (
        f"select 'system' as kind, system::text as id from catalog.systems "
        f"where system::text = any({system_array}) "
        f"union all "
        f"select 'data_source', data_source_id::text from catalog.data_sources "
        f"where data_source_id::text = any({coordinate_array}) "
        f"union all "
        f"select 'schema', schema_id::text from catalog.schemas "
        f"where schema_id::text = any({coordinate_array}) "
        f"union all "
        f"select 'table', table_id::text from catalog.tables "
        f"where table_id::text = any({coordinate_array})"
    )


def build_systems_sql(systems: list[str]) -> str:
    """Build the system-prose query over the declared systems.

    The systems are coordinates of every conversion, so their catalog meaning travels
    with the file rather than living only in skill documentation.

    Args:
        systems: The distinct declared systems, sorted.

    Returns:
        The SQL statement.
    """
    return (
        f"select system::text as system, description, notes "
        f"from catalog.systems "
        f"where system::text = any(array[{_sql_literals(systems)}]::text[])"
    )


def build_origin_columns_sql(origin_data_scope: tuple[str, ...], variable_names: list[str]) -> str:
    """Build the origin-columns query for one effective origin_data_scope.

    The scope bounds where a column may live; the names bound which columns are worth
    fetching at all. Filtering on the names server-side is what keeps the result sized
    by the inventory rather than by the scope's width -- a single cataloged table can
    run to thousands of columns, well past the MCP server's row cap, while an inventory
    asks about a handful of names.

    Matching itself stays in `match_origin_columns`: this predicate is an optimization,
    so it is deliberately looser than the Python match (`lower()` here against a
    casefolded name there, which agree over the catalog's lowercase ASCII names). A
    catalog name this returns but Python would not match is simply dropped; the reverse
    -- a name the predicate hides from a match Python would make -- is what `lower()`
    prevents.

    Args:
        origin_data_scope: The scope's entries, canonically ordered.
        variable_names: The eligible variable names of every dataset sharing this
            scope, lowercased, deduplicated, and sorted (see `partition_variable_names`).

    Returns:
        The SQL statement.
    """
    # `<@` and `ltree` are schema-qualified: the extension lives in the catalog schema and the MCP
    # connection sets no search_path, so an unqualified `::ltree` fails.
    return (
        f"select column_id::text as column_id, table_id::text as table_id, column_name, "
        f"data_type, is_nullable, is_primary_key, ref_table_id::text as ref_table_id, "
        f"description, notes "
        f"from catalog.columns "
        f"where column_id operator(catalog.<@) any(array[{_sql_literals(list(origin_data_scope))}]::catalog.ltree[]) "
        f"and lower(column_name) = any(array[{_sql_literals(variable_names)}]::text[])"
    )


def build_dest_columns_sql(column_ids: list[str]) -> str:
    """Build the exact-id columns query for the dest columns the emitted code reads.

    The ids are recovered from the surviving expressions, the dest joins' conditions,
    and the dest tables' primary keys (see `resolve`), so this is an exact fetch,
    never a scope scan: nothing the emitted code does not read can come back.

    Args:
        column_ids: The referenced dest column ids, sorted.

    Returns:
        The SQL statement.
    """
    return (
        f"select column_id::text as column_id, table_id::text as table_id, column_name, "
        f"data_type, is_nullable, is_primary_key, ref_table_id::text as ref_table_id, "
        f"description, notes "
        f"from catalog.columns "
        f"where column_id::text = any(array[{_sql_literals(column_ids)}]::text[])"
    )


def build_primary_keys_sql(tables: list[str]) -> str:
    """Build the primary-key columns query over a table set.

    Feeds `primary_key_columns` on every table record, so grain is mechanical rather
    than prose; the dest tables' keys also join the dest-column collection.

    Args:
        tables: The table ids whose flagged key columns to fetch, sorted.

    Returns:
        The SQL statement.
    """
    return (
        f"select table_id::text as table_id, column_name "
        f"from catalog.columns "
        f"where is_primary_key and table_id::text = any(array[{_sql_literals(tables)}]::text[])"
    )


def build_column_mappings_sql(origin_column_ids: list[str]) -> str:
    """Build the column-mappings query over the origin columns the variables matched.

    An exact fetch, never a scope scan: a column id is globally unique, so once matching
    has resolved which columns are in play the scope adds nothing, and the mappings of
    every transitioning dataset can be pooled into one query. Only the matched columns'
    mappings are ever read (`origin_columns[].candidates[]` is keyed on the column), so
    fetching a scope's worth would be fetching rows to discard.

    Args:
        origin_column_ids: The matched origin column ids of the transitioning datasets,
            sorted.

    Returns:
        The SQL statement.
    """
    return (
        f"select source_column_id::text as source_column_id, mapping_name, target_expression, "
        f"array(select t::text from unnest(target_tables_referenced) t) as target_tables_referenced, "
        f"use_when, notes, validated "
        f"from catalog.column_mappings "
        f"where source_column_id::text = any(array[{_sql_literals(origin_column_ids)}]::text[])"
    )


def build_joins_sql(tables: list[str]) -> str:
    """Build the joins query over a table set.

    Args:
        tables: The table ids both endpoints must be drawn from, sorted.

    Returns:
        The SQL statement.
    """
    # Endpoints are compared as text: `=` on ltree is a prod-schema operator too, and
    # the MCP connection sets no search_path, so `ltree = ltree` fails to resolve. An
    # exact text comparison of two canonical ids is equivalent.
    table_array = f"array[{_sql_literals(tables)}]::text[]"
    return (
        f"select table_a_id::text as table_a_id, table_b_id::text as table_b_id, "
        f"relationship_name, join_condition, cardinality, use_when, notes, validated "
        f"from catalog.table_relationships "
        f"where table_a_id::text = any({table_array}) and table_b_id::text = any({table_array})"
    )


def build_tables_sql(tables: list[str]) -> str:
    """Build the tables query over a table set.

    Table prose is what tells a reader the grain a dataset sits at, which the converted
    code has to reproduce, and which of several same-named columns a variable came from.

    Args:
        tables: The table ids to describe, sorted.

    Returns:
        The SQL statement.
    """
    return (
        f"select table_id::text as table_id, description, notes "
        f"from catalog.tables "
        f"where table_id::text = any(array[{_sql_literals(tables)}]::text[])"
    )


def build_schemas_sql(schemas: list[str]) -> str:
    """Build the schemas query over a schema set.

    Args:
        schemas: The 2-segment schema ids in play, sorted.

    Returns:
        The SQL statement.
    """
    return (
        f"select schema_id::text as schema_id, description, notes "
        f"from catalog.schemas "
        f"where schema_id::text = any(array[{_sql_literals(schemas)}]::text[])"
    )


def build_data_sources_sql(data_sources: list[str]) -> str:
    """Build the data-sources query over a data-source set.

    `owner` is deliberately not selected: it routes review, and says nothing about the
    data a conversion is reading.

    Args:
        data_sources: The 1-segment data source ids in play, sorted.

    Returns:
        The SQL statement.
    """
    return (
        f"select data_source_id::text as data_source_id, description, notes "
        f"from catalog.data_sources "
        f"where data_source_id::text = any(array[{_sql_literals(data_sources)}]::text[])"
    )


def build_concepts_sql(objects: list[str]) -> str:
    """Build the concepts query over the objects the conversion touches.

    A concept is in scope when the object it is anchored to -- its namespace, the
    path before `.concept.` -- is one of those objects. This is an exact match at every
    depth: a data source, a schema, a table, or a column. Matching everything beneath a
    schema would pull in concepts for tables and columns the conversion never reads.

    Args:
        objects: The catalog objects in play, sorted (see `objects_in_play`).

    Returns:
        The SQL statement.
    """
    return (
        f"with c as (select concept_id, label, definition, notes, related_object_ids, "
        f"split_part(concept_id::text, '.concept.', 1) as ns from catalog.concepts) "
        f"select concept_id::text as concept_id, label, definition, notes, "
        f"array(select o::text from unnest(related_object_ids) o) as related_object_ids "
        f"from c where ns = any(array[{_sql_literals(objects)}]::text[])"
    )


def build_deployment_sql(tables: list[str]) -> str:
    """Build the deployment query over the tables in play.

    Args:
        tables: The tables-in-play ids, sorted.

    Returns:
        The SQL statement.
    """
    return (
        f"select table_id::text as table_id, system::text as system, "
        f"physical_database_name, physical_schema_name, physical_table_name "
        f"from catalog.deployment_tables "
        f"where table_id::text = any(array[{_sql_literals(tables)}]::text[])"
    )


# --- Resolution steps ---


def check_coordinates(rows: list[dict[str, Any]], systems: list[str], coordinates: list[str]) -> None:
    """Confirm every system and data scope entry resolves to a catalog row.

    Extraction validates only a coordinate's shape, so an uncaught typo would otherwise
    yield a plausible, wrong file. This runs before any resolution query.

    Args:
        rows: The coordinate-check query's rows.
        systems: The distinct systems that were checked.
        coordinates: The distinct data scope entries that were checked.

    Raises:
        CoordinateError: Listing every coordinate that did not resolve.
    """
    found_systems = {r.get("id") for r in rows if r.get("kind") == "system"}
    found_data = {r.get("id") for r in rows if r.get("kind") != "system"}

    unresolved = [f"system '{s}' is not a systems row" for s in systems if s not in found_systems]
    unresolved += [
        f"data scope '{c}' is not a data_sources, schemas, or tables row"
        for c in coordinates
        if c not in found_data
    ]
    if unresolved:
        raise CoordinateError("; ".join(unresolved), unresolved)
    logger.info(f"Coordinates resolved: {len(systems)} system(s), {len(coordinates)} data scope entries")


def build_system_records(
    system_rows: list[dict[str, Any]], origin_system: str, dest_system: str
) -> list[dict[str, Any]]:
    """Build the system-prose records, per the collapse rule.

    A `dest_system` record always publishes; an `origin_system` record exactly when
    the systems differ -- the same collapse every other record type obeys.

    Args:
        system_rows: The system-prose query's rows.
        origin_system: The process-wide origin system.
        dest_system: The process-wide destination system.

    Returns:
        The system records, origin before dest.
    """
    prose = {row["system"]: row for row in system_rows}

    def record(record_type: str, system: str) -> dict[str, Any]:
        """Build one system record, with null prose when the catalog carries none.

        Args:
            record_type: Either "origin_system" or "dest_system".
            system: The system id.

        Returns:
            The system record.
        """
        row = prose.get(system, {})
        return {
            "record_type": record_type,
            "system": system,
            "description": row.get("description"),
            "notes": row.get("notes"),
        }

    records = []
    if origin_system != dest_system:
        records.append(record("origin_system", origin_system))
    records.append(record("dest_system", dest_system))
    return records


def match_origin_columns(column_rows: list[dict[str, Any]], variable: str) -> list[dict[str, Any]]:
    """Find every catalog column whose name matches a SAS variable.

    SAS input datasets are usually derived subsets of several cataloged parent tables,
    so a name can match in more than one table. Every match is kept: the catalog cannot
    choose between them, and neither may this script. Zero matches is never a legal
    outcome -- an origin SAS dataset's variables are all documented, so the caller
    records an unmatched variable as a catalog gap and the run fails with the complete
    list.

    This remains the whole of the matching rule even though the columns query now
    pre-filters on the same names: the rows arrive pooled across the datasets sharing a
    scope, so this is what keeps a variable's matches its own, and holding the rule here
    keeps it in tested Python rather than in a SQL predicate.

    Args:
        column_rows: The origin-columns query's rows for the variable's scope.
        variable: The SAS variable name.

    Returns:
        The matching rows, sorted by column_id.
    """
    name = variable.casefold()
    matches = [r for r in column_rows if str(r.get("column_name", "")).casefold() == name]
    return sorted(matches, key=lambda r: r["column_id"])


def filter_candidates(
    mapping_rows: list[dict[str, Any]], dest_data_scope: list[str] | None
) -> list[dict[str, Any]]:
    """Keep the mappings a converted read of dest_data_scope can use.

    A candidate survives when every table it references falls under a dest_data_scope
    prefix. No-equivalent mappings (null target_expression) are always kept regardless
    of the filter -- they describe the origin column, not a target -- and carry their
    notes, which planning needs as rationale.

    Args:
        mapping_rows: One origin column's mapping rows.
        dest_data_scope: The dataset's effective dest_data_scope.

    Returns:
        The surviving rows, sorted by mapping_name.
    """
    scopes = dest_data_scope or []
    survivors = [
        row for row in mapping_rows
        if row.get("target_expression") is None
        or all(_under_any(table, scopes) for table in (row.get("target_tables_referenced") or []))
    ]
    return sorted(survivors, key=lambda r: str(r.get("mapping_name") or ""))


def _under_any(dotted: str, scopes: list[str]) -> bool:
    """Return whether an id is a descendant-or-self of any scope prefix.

    Mirrors ltree `<@`: a prefix matches only at a segment boundary, so 'a.bc' is not
    under 'a.b'.

    Args:
        dotted: The id to test.
        scopes: The scope prefixes to test against.

    Returns:
        True when the id falls under at least one scope.
    """
    return any(dotted == prefix or dotted.startswith(prefix + ".") for prefix in scopes)


def derive_mapping_status(candidates: list[dict[str, Any]], in_transition: bool) -> str | None:
    """Derive one origin column's mapping status from its surviving candidates.

    Status is per origin column, never per variable: two columns behind one variable
    can carry two different statuses. Each published status states what the catalog
    knows: `mapped` -- an equivalent is documented; `no_equivalent` -- the catalog
    affirmatively documents that none exists; `not_applicable` -- the question was
    never asked, because the dataset does not change data sources. An in-transition
    column with no surviving candidate has no status at all: it is a catalog gap the
    caller records, and the run fails before anything publishes.

    Args:
        candidates: The candidates that SURVIVED the dest_data_scope filter.
        in_transition: Whether the column's dataset carries dest_data_scope.

    Returns:
        One of 'mapped', 'no_equivalent', or 'not_applicable'; None for the gap case.
    """
    if not in_transition:
        return "not_applicable"
    if not candidates:
        return None
    if any(c.get("target_expression") is not None for c in candidates):
        return "mapped"
    return "no_equivalent"


def build_origin_column(
    column_row: dict[str, Any], mapping_rows: list[dict[str, Any]], dest_data_scope: list[str] | None
) -> dict[str, Any]:
    """Build one origin_columns entry, with its candidates and status.

    Fields lead with the container id (`table_id` before the column id), matching
    every other place a column appears.

    Args:
        column_row: The matched origin-columns row.
        mapping_rows: That column's mapping rows (empty when dest_data_scope is absent).
        dest_data_scope: The dataset's effective dest_data_scope.

    Returns:
        The origin_columns object. A None mapping_status marks an in-transition column
        with no usable candidate -- a catalog gap the caller records; it never
        publishes, because the run fails first.
    """
    candidates = [
        {
            "mapping_name": row.get("mapping_name"),
            "target_expression": row.get("target_expression"),
            "target_tables_referenced": list(row.get("target_tables_referenced") or []),
            "use_when": row.get("use_when"),
            "notes": row.get("notes"),
            "validated": bool(row.get("validated")),
        }
        for row in filter_candidates(mapping_rows, dest_data_scope)
    ]
    return {
        "table_id": column_row["table_id"],
        "origin_column_id": column_row["column_id"],
        "data_type": column_row.get("data_type"),
        # Nullability drives missing-value handling: SAS missing values and SQL NULL are
        # not the same thing, and the plan has to say which it means.
        "is_nullable": bool(column_row.get("is_nullable")),
        "is_primary_key": bool(column_row.get("is_primary_key")),
        # The coded column's value domain, when one is documented -- how a reader finds
        # what the codes mean without guessing from the prose.
        "ref_table_id": column_row.get("ref_table_id"),
        "description": column_row.get("description"),
        "notes": column_row.get("notes"),
        "mapping_status": derive_mapping_status(candidates, bool(dest_data_scope)),
        "candidates": candidates,
    }


def compute_dest_tables(
    variable_records: list[dict[str, Any]], coordinates_by_dataset: dict[str, dict[str, Any]]
) -> tuple[list[str], list[str]]:
    """Determine the dest tables and the SAS parents, pooled across datasets.

    The dest tables are what the converted code will actually read: the target tables
    the surviving candidates reference when dest_data_scope is present, else the
    dataset's own origin tables. No-equivalent mappings contribute nothing -- they
    reference no target. Resolving joins and deployment over this one set is what
    makes both cases identical.

    Args:
        variable_records: The built variable records.
        coordinates_by_dataset: Effective coordinates per dataset name.

    Returns:
        Tuple of (sorted dest tables, sorted SAS parents).
    """
    dest_tables: set[str] = set()
    sas_parents: set[str] = set()
    for record in variable_records:
        coords = coordinates_by_dataset.get(record["dataset"], {})
        in_transition = bool(coords.get("dest_data_scope"))
        for column in record["origin_columns"]:
            sas_parents.add(column["table_id"])
            if not in_transition:
                dest_tables.add(column["table_id"])
                continue
            for candidate in column["candidates"]:
                if candidate["target_expression"] is None:
                    continue
                dest_tables.update(candidate["target_tables_referenced"])
    return sorted(dest_tables), sorted(sas_parents)


def dest_columns_referenced(expression: str | None, tables: list[str]) -> set[str]:
    """Return the dest columns an expression reads.

    The catalog stores which TABLES a translation reads, not which columns -- the loader
    extracts the column references to derive the tables and then discards them. The
    expression itself is carried on the candidate though, so the columns are recoverable
    here: a column reference is one of the known table ids followed by a column name.
    Matching against the ids the candidate already names, rather than parsing SQL, keeps
    this exact -- nothing that is not a documented table can match.

    The scan folds case on both sides, and must: a `target_expression` is authored SQL,
    where case is free, while catalog ids are lowercase. A case-sensitive scan derives
    nothing from `EDWC_PRD.….BENE.BENE_SEX_CD`, so the emitted code would read a column
    the resolution never documents -- silently, since "no match" reads as "no column
    referenced". `data_val_schema_resolution._dest_columns` is the deliberate copy of
    this function and must not drift from it: it re-derives the same set to check the
    `dest_column` records cover it, so a blind spot here becomes a blind spot in the
    gate that exists to catch this one.

    Args:
        expression: The candidate's target expression (or a join condition), or None
            for a no-equivalent mapping.
        tables: The table ids the expression is already known to reference.

    Returns:
        The referenced column ids, in the catalog's lowercase form.
    """
    if not expression:
        return set()
    found: set[str] = set()
    for table_id in tables:
        # The lookbehind keeps a table id from matching as the dotted suffix of a
        # longer identifier, which would yield a phantom column id.
        for column in re.findall(
            rf"(?<![a-z0-9_.-]){re.escape(table_id)}\.([a-z0-9_-]+)", expression
        ):
            found.add(f"{table_id}.{column}")
    return found


def referenced_dest_columns(
    variable_records: list[dict[str, Any]], dest_joins: list[dict[str, Any]]
) -> list[str]:
    """The dest columns the expressions and the dest joins read.

    The surviving expressions' columns plus the dest joins' condition columns -- the
    generated join is written against the latter, so they are documented at the same
    grain as the expressions'. The third collection source, the dest tables'
    primary keys, is added by the caller from the primary-key fetch.

    Args:
        variable_records: The built variable records.
        dest_joins: The built dest_join records.

    Returns:
        The distinct column ids, sorted.
    """
    columns: set[str] = set()
    for record in variable_records:
        for column in record["origin_columns"]:
            for candidate in column["candidates"]:
                columns |= dest_columns_referenced(
                    candidate["target_expression"], candidate["target_tables_referenced"]
                )
    for join in dest_joins:
        columns |= dest_columns_referenced(
            join.get("join_condition"), [join["table_a_id"], join["table_b_id"]]
        )
    return sorted(columns)


def build_dest_column_records(column_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build one dest_column record per column the emitted code reads.

    The origin-column shape minus the mapping machinery: the mapping is the authored
    contract, so a dest column carries no status and no candidates -- just the
    catalog metadata (type, nullability, grain flag, prose, code-set pointer) the
    planner writes code against.

    Args:
        column_rows: The exact-id columns query's rows.

    Returns:
        The dest_column records, sorted by column_id.
    """
    return [
        {
            "record_type": "dest_column",
            "table_id": row["table_id"],
            "column_id": row["column_id"],
            "data_type": row.get("data_type"),
            "is_nullable": bool(row.get("is_nullable")),
            "is_primary_key": bool(row.get("is_primary_key")),
            "ref_table_id": row.get("ref_table_id"),
            "description": row.get("description"),
            "notes": row.get("notes"),
        }
        for row in sorted(column_rows, key=lambda r: r["column_id"])
    ]


def _primary_key_map(pk_rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Index the primary-key rows as table_id -> sorted leaf column names.

    Args:
        pk_rows: The primary-keys query's rows.

    Returns:
        The index. A table with no flagged keys is simply absent (publishes as []).
    """
    grouped: dict[str, list[str]] = defaultdict(list)
    for row in pk_rows:
        grouped[row["table_id"]].append(row["column_name"])
    return {table_id: sorted(names) for table_id, names in grouped.items()}


def objects_in_play(
    variable_records: list[dict[str, Any]], tables: list[str], dest_columns: list[str]
) -> list[str]:
    """Every catalog object this conversion touches, at all four depths.

    A concept is anchored to one object, so this is what decides which concepts
    belong in the resolution: the data sources and schemas of the tables in play, those
    tables, the origin columns the variables matched, and the dest columns the
    emitted code reads (the expressions', the dest joins', and the dest tables'
    grain). Pulling everything beneath a schema instead would sweep in concepts for
    tables and columns the conversion never touches -- on a view as wide as the EDW
    claim views, that is most of them. Grain widens the scope only through the dest
    tables, whose keys are dest columns; the keys of transition-case SAS parents and
    of code sets stay out.

    Args:
        variable_records: The built variable records.
        tables: Every table in play -- the dest tables, the SAS parents, and the
            code sets the columns point at.
        dest_columns: The dest column ids (see `resolve`).

    Returns:
        The distinct object ids, sorted.
    """
    objects: set[str] = set(tables) | set(dest_columns)
    for table in tables:
        objects |= _prefixes(table)
    for record in variable_records:
        for column in record["origin_columns"]:
            objects.add(column["origin_column_id"])
    return sorted(objects)


def concept_scope(tables: list[str]) -> list[str]:
    """Build the 1- and 2-segment prefixes of the tables in play.

    These are the data sources and schemas that get their own records, and they also
    decide which side each record lands on. The DECLARED origin_data_scope is
    deliberately not a source here: an entry no variable matched under would add a
    record for a schema the conversion never reads.

    Args:
        tables: The dest tables plus the SAS parents.

    Returns:
        The distinct prefixes, sorted.
    """
    scope: set[str] = set()
    for table in tables:
        scope |= _prefixes(table)
    return sorted(scope)


def _prefixes(dotted: str) -> set[str]:
    """Return the 1- and 2-segment (data source and schema) prefixes of a dotted id.

    Args:
        dotted: A dotted ltree id or prefix.

    Returns:
        Its `{data_source}` and, when present, `{data_source}.{schema}` prefixes.
    """
    parts = dotted.split(".")
    return {parts[0]} | ({".".join(parts[:2])} if len(parts) >= 2 else set())


def _placements(deployment_rows: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    """Index the deployment rows as table_id -> system -> row.

    Args:
        deployment_rows: The deployment query's rows.

    Returns:
        The nested index.
    """
    by_table: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in deployment_rows:
        by_table[row["table_id"]][row["system"]] = row
    return by_table


def _table_record(
    record_type: str,
    table_id: str,
    prose: dict[str, dict[str, Any]],
    primary_keys: dict[str, list[str]],
    placement: dict[str, Any],
) -> dict[str, Any]:
    """Assemble one table record: prose, grain, and a physical address.

    Args:
        record_type: origin_table, dest_table, or ref_table.
        table_id: The table's catalog id.
        prose: The tables query's rows, indexed by table_id.
        primary_keys: The primary-key index (table_id -> sorted leaf names).
        placement: The deployment row supplying the address.

    Returns:
        The table record.
    """
    described = prose.get(table_id, {})
    return {
        "record_type": record_type,
        "table_id": table_id,
        "description": described.get("description"),
        "notes": described.get("notes"),
        # The table's grain, mechanical rather than prose; an undocumented grain
        # publishes honestly as the empty list (the flags are still being authored).
        "primary_key_columns": primary_keys.get(table_id, []),
        "physical_database_name": placement.get("physical_database_name"),
        "physical_schema_name": placement.get("physical_schema_name"),
        "physical_table_name": placement.get("physical_table_name"),
    }


def build_dest_tables(
    dest_tables: list[str],
    deployment_rows: list[dict[str, Any]],
    prose_rows: list[dict[str, Any]],
    primary_keys: dict[str, list[str]],
    dest_system: str,
) -> list[dict[str, Any]]:
    """Give every dest table its prose, grain, and address in the destination system.

    Deployment is resolved, never assumed: the deployment gate has already failed the
    run if a dest table is missing from the destination system, so a published record
    always carries a full address.

    Args:
        dest_tables: The dest table ids, sorted.
        deployment_rows: The deployment query's rows.
        prose_rows: The tables query's rows (description and notes).
        primary_keys: The primary-key index.
        dest_system: The process-wide destination system.

    Returns:
        One dest_table record per dest table, sorted by table_id.
    """
    by_table = _placements(deployment_rows)
    prose = {row["table_id"]: row for row in prose_rows}
    return [
        _table_record(
            "dest_table", table_id, prose, primary_keys,
            by_table.get(table_id, {}).get(dest_system, {}),
        )
        for table_id in dest_tables
    ]


def build_origin_tables(
    origin_table_ids: list[str],
    deployment_rows: list[dict[str, Any]],
    prose_rows: list[dict[str, Any]],
    primary_keys: dict[str, list[str]],
    origin_system: str,
) -> list[dict[str, Any]]:
    """Describe the origin tables, addressed in the origin system.

    The id list is the pairing rule's (see `origin_table_set`): every SAS parent that
    is not a dest table, plus -- when the systems differ -- every parent that is, so
    the id-matched origin_table/dest_table pair carries the address the SAS process
    read beside the address the converted code will read. The deployment gate has
    already failed the run if an origin table is missing from the origin system, so a
    published record always carries a full address.

    Args:
        origin_table_ids: The origin table ids per the pairing rule, sorted.
        deployment_rows: The deployment query's rows.
        prose_rows: The tables query's rows (description and notes).
        primary_keys: The primary-key index.
        origin_system: The process-wide origin system.

    Returns:
        One origin_table record per id, sorted by table_id.
    """
    by_table = _placements(deployment_rows)
    prose = {row["table_id"]: row for row in prose_rows}
    return [
        _table_record(
            "origin_table", table_id, prose, primary_keys,
            by_table.get(table_id, {}).get(origin_system, {}),
        )
        for table_id in origin_table_ids
    ]


def origin_table_set(
    sas_parents: list[str], dest_tables: list[str], origin_system: str, dest_system: str
) -> list[str]:
    """The tables that take the origin_table form, per the pairing rule.

    An origin_table exists for every SAS parent that is not a dest table, plus --
    when the systems differ -- every parent that is: the SAS process read one
    physical copy and the converted code will read the other, and the id-matched
    pair carries both addresses. Parents that are dest tables under equal systems
    stay collapsed into their dest_table record, and dest tables the SAS process
    never read (transition targets) never take the origin form.

    Args:
        sas_parents: The tables the matched origin columns live on, sorted.
        dest_tables: The dest table ids, sorted.
        origin_system: The process-wide origin system.
        dest_system: The process-wide destination system.

    Returns:
        The origin table ids, sorted.
    """
    dest = set(dest_tables)
    ids = {t for t in sas_parents if t not in dest}
    if origin_system != dest_system:
        ids |= {t for t in sas_parents if t in dest}
    return sorted(ids)


def build_ref_tables(
    ref_table_ids: list[str],
    deployment_rows: list[dict[str, Any]],
    prose_rows: list[dict[str, Any]],
    primary_keys: dict[str, list[str]],
) -> list[dict[str, Any]]:
    """Resolve the code sets the columns point at, on both sides.

    A column's `ref_table_id` names the catalog table enumerating its valid values. The
    pointer alone is not followable, so the table it names is resolved here the way any
    other table is: its prose, grain, and the physical address of the instance hosting
    it. These tables take no origin/dest form -- a code set is a lookup a reader
    consults, not something the SAS process read or the converted code writes.

    Args:
        ref_table_ids: The distinct code sets the origin and dest columns point at,
            sorted.
        deployment_rows: The deployment query's rows.
        prose_rows: The tables query's rows (description and notes).
        primary_keys: The primary-key index.

    Returns:
        One ref_table record per distinct referenced code set, sorted by table_id.
    """
    by_table = _placements(deployment_rows)
    prose = {row["table_id"]: row for row in prose_rows}

    records = []
    for table_id in ref_table_ids:
        deployments = by_table.get(table_id, {})
        systems = sorted(deployments)
        # A ref table is deployed to exactly one system by design (the catalog's own
        # instance); take that address deterministically. A catalog that violates the
        # assumption still resolves, but says so rather than picking in silence.
        if len(systems) > 1:
            logger.warning(
                f"Code set '{table_id}' is deployed to {len(systems)} systems "
                f"({', '.join(systems)}); using '{systems[0]}'"
            )
        placement = deployments.get(systems[0], {}) if systems else {}
        records.append(_table_record("ref_table", table_id, prose, primary_keys, placement))
    return records


def collect_missing_deployments(
    dest_tables: list[str],
    origin_table_ids: list[str],
    ref_table_ids: list[str],
    deployment_rows: list[dict[str, Any]],
    origin_system: str,
    dest_system: str,
) -> list[dict[str, Any]]:
    """Collect every table in play undeployed where the process needs it.

    Three sides of the same gap. An origin table missing from the origin system means
    the SAS process read a table that exists nowhere it ran (the past). A dest table
    missing from the destination system means the conversion as declared cannot run
    (the future). A code set deployed nowhere is an address-less pointer, recorded
    with a null system. Both systems are always declared (Step 1 fails otherwise), so
    every side is always checked.

    Args:
        dest_tables: The dest table ids, sorted.
        origin_table_ids: The origin table ids per the pairing rule, sorted.
        ref_table_ids: The referenced code sets, sorted.
        deployment_rows: The deployment query's rows.
        origin_system: The process-wide origin system.
        dest_system: The process-wide destination system.

    Returns:
        One missing_deployment record per hole, sorted by (table_id, system).
    """
    by_table = _placements(deployment_rows)
    gaps: list[dict[str, Any]] = [
        {"record_type": "missing_deployment", "table_id": table_id, "system": origin_system}
        for table_id in origin_table_ids
        if origin_system not in by_table.get(table_id, {})
    ]
    gaps += [
        {"record_type": "missing_deployment", "table_id": table_id, "system": dest_system}
        for table_id in dest_tables
        if dest_system not in by_table.get(table_id, {})
    ]
    gaps += [
        {"record_type": "missing_deployment", "table_id": table_id, "system": None}
        for table_id in ref_table_ids
        if not by_table.get(table_id)
    ]
    return sorted(gaps, key=lambda g: (g["table_id"], g["system"] or ""))


def describe_gap(gap: dict[str, Any]) -> str:
    """Render one catalog gap record as a log-ready sentence.

    Args:
        gap: A missing_variable, missing_candidate, or missing_deployment record.

    Returns:
        The description.
    """
    if gap["record_type"] == "missing_variable":
        return (
            f"variable '{gap['origin_sas_dataset']}.{gap['origin_sas_variable']}' matches "
            f"no column under origin_data_scope {gap['origin_data_scope']}"
        )
    if gap["record_type"] == "missing_candidate":
        return (
            f"column '{gap['origin_column_id']}' "
            f"('{gap['origin_sas_dataset']}.{gap['origin_sas_variable']}') "
            f"has no usable mapping into dest_data_scope {gap['dest_data_scope']}"
        )
    if gap["system"] is None:
        return f"code set '{gap['table_id']}' is deployed nowhere"
    return f"table '{gap['table_id']}' is not deployed in system '{gap['system']}'"


def build_join_records(
    dest_join_rows: list[dict[str, Any]], origin_join_rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split the join rows into origin and dest joins.

    A dest_join records what the converted code emits; an origin_join records how the
    SAS input was assembled from parent tables. The two sets can overlap, since a
    mapping may target a table in its own data source, so dest takes precedence and a
    relationship already emitted as a dest_join is not repeated.

    Args:
        dest_join_rows: The joins query's rows over the dest tables.
        origin_join_rows: The joins query's rows over the SAS parents.

    Returns:
        Tuple of (origin_join records, dest_join records), each sorted.
    """
    def key(row: dict[str, Any]) -> tuple[str, str, str]:
        """Identify a relationship, for both deduplication and deterministic sorting.

        Args:
            row: A joins query row.

        Returns:
            Tuple of (table_a_id, table_b_id, relationship_name), the name emptied to
            a string so unnamed relationships still sort.
        """
        return (row["table_a_id"], row["table_b_id"], str(row.get("relationship_name") or ""))

    def record(row: dict[str, Any], record_type: str) -> dict[str, Any]:
        """Build one join record from a joins query row.

        Args:
            row: A joins query row.
            record_type: Either "origin_join" or "dest_join".

        Returns:
            The join record.
        """
        return {
            "record_type": record_type,
            "table_a_id": row["table_a_id"],
            "table_b_id": row["table_b_id"],
            "relationship_name": row.get("relationship_name"),
            "join_condition": row.get("join_condition"),
            "cardinality": row.get("cardinality"),
            "use_when": row.get("use_when"),
            # Where a join's grain caveats live: when it fans out, and what to filter.
            "notes": row.get("notes"),
            "validated": bool(row.get("validated")),
        }

    dest_keys = {key(row) for row in dest_join_rows}
    dest_joins = [record(row, "dest_join") for row in sorted(dest_join_rows, key=key)]
    origin_joins = [
        record(row, "origin_join")
        for row in sorted(origin_join_rows, key=key)
        if key(row) not in dest_keys
    ]
    return origin_joins, dest_joins


def build_concept_records(
    concept_rows: list[dict[str, Any]], dest_objects: set[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split the concept rows into origin and dest concepts.

    The side follows the anchor: a concept anchored to an object appearing only on
    the origin side publishes as origin_concept; every other anchor -- dest-side or
    shared -- publishes as dest_concept, per the collapse rule. Without a data source
    change the origin objects ARE dest objects, so every concept lands dest-side.

    Args:
        concept_rows: The concepts query's rows.
        dest_objects: The dest-side object ids (dest tables and code sets, their
            data-source and schema prefixes, and the dest columns).

    Returns:
        Tuple of (origin_concept records, dest_concept records), each sorted.
    """
    def record(row: dict[str, Any], record_type: str) -> dict[str, Any]:
        """Build one concept record from a concepts query row.

        Args:
            row: A concepts query row.
            record_type: Either "origin_concept" or "dest_concept".

        Returns:
            The concept record.
        """
        return {
            "record_type": record_type,
            "concept_id": row["concept_id"],
            "label": row.get("label"),
            "definition": row.get("definition"),
            # Provenance for the definition: where it was written down and from what.
            "notes": row.get("notes"),
            "related_object_ids": list(row.get("related_object_ids") or []),
        }

    origin_concepts, dest_concepts = [], []
    for row in sorted(concept_rows, key=lambda r: r["concept_id"]):
        anchor = row["concept_id"].split(".concept.")[0]
        if anchor in dest_objects:
            dest_concepts.append(record(row, "dest_concept"))
        else:
            origin_concepts.append(record(row, "origin_concept"))
    return origin_concepts, dest_concepts


def build_scope_records(
    schema_rows: list[dict[str, Any]],
    data_source_rows: list[dict[str, Any]],
    dest_scope: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Build the data-source and schema records, split by side and level.

    Scope prose orients a reader in what the data IS before they read its columns: which
    file settings a schema covers, what a data source holds and where it came from. The
    four groups are returned separately because the write order descends the hierarchy
    (data sources before schemas), origin before dest within each level.

    Args:
        schema_rows: The schemas query's rows.
        data_source_rows: The data-sources query's rows.
        dest_scope: The 1- and 2-segment prefixes of the dest tables.

    Returns:
        Tuple of (origin_data_source, dest_data_source, origin_schema, dest_schema
        records), each sorted by id.
    """
    origin_data_sources, dest_data_sources = [], []
    for row in sorted(data_source_rows, key=lambda r: r["data_source_id"]):
        is_dest = _under_any(row["data_source_id"], dest_scope)
        (dest_data_sources if is_dest else origin_data_sources).append({
            "record_type": "dest_data_source" if is_dest else "origin_data_source",
            "data_source_id": row["data_source_id"],
            "description": row.get("description"),
            "notes": row.get("notes"),
        })
    origin_schemas, dest_schemas = [], []
    for row in sorted(schema_rows, key=lambda r: r["schema_id"]):
        is_dest = _under_any(row["schema_id"], dest_scope)
        (dest_schemas if is_dest else origin_schemas).append({
            "record_type": "dest_schema" if is_dest else "origin_schema",
            "schema_id": row["schema_id"],
            "description": row.get("description"),
            "notes": row.get("notes"),
        })
    return origin_data_sources, dest_data_sources, origin_schemas, dest_schemas


# --- Orchestration ---


class QueryRunner:
    """Runs catalog queries through an MCP client, memoized per statement.

    Two datasets sharing a scope produce the same statement, so the cache is what keeps
    "once per distinct origin_data_scope" true without the caller tracking groups.

    Attributes:
        calls: Statements actually sent, in order, for the run summary.
    """

    def __init__(self, client: MCPClient, database: str) -> None:
        """Initialize the runner.

        Args:
            client: The MCP client to issue queries through.
            database: The database the MCP server should target.
        """
        self._client = client
        self._database = database
        self._cache: dict[str, list[dict[str, Any]]] = {}
        self.calls: list[str] = []

    def __call__(self, sql: str) -> list[dict[str, Any]]:
        """Run a statement, returning a cached result when it has been run before.

        Args:
            sql: The SQL statement.

        Returns:
            The result rows.
        """
        if sql in self._cache:
            return self._cache[sql]
        rows = self._client.run_sql(self._database, sql)
        self._cache[sql] = rows
        self.calls.append(sql)
        return rows


def resolve(
    meta: dict[str, Any],
    dataset_records: list[dict[str, Any]],
    variable_records: list[dict[str, Any]],
    run_query: Callable[[str], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Resolve an inventory against the catalog into the full ordered record list.

    Derivation order differs from write order: variables resolve first, the dest
    tables follow from their candidates, and deployment -- written early -- is
    resolved late, because it needs the dest tables. Table prose is deferred past
    the deployment gate, so a run that fails there never spends it.

    Args:
        meta: The inventory's meta record.
        dataset_records: The inventory's origin_sas_dataset records.
        variable_records: The inventory's origin_sas_variable records.
        run_query: Callable taking SQL and returning rows.

    Returns:
        The output records, grouped and sorted per Reference -> Output Records.

    Raises:
        CoordinateError: If a system is undeclared, or a coordinate is malformed or
            does not resolve.
        InventoryError: If a dataset record carries a system, or a variable references
            an unknown dataset.
        CatalogGapError: If the catalog cannot fully account for the input -- an
            unmatched variable, a silent in-transition column, a mapping reading a
            dest column the catalog does not document, or a missing deployment --
            carrying the complete gap list of the gate that failed.
    """
    require_meta_only_systems(dataset_records)
    coordinates_by_dataset = {r["dataset"]: resolve_coordinates(meta, r) for r in dataset_records}

    # Guideline 3: nothing is resolved until every coordinate is known to be real.
    # Both systems are required here (Step 1), before any query is built.
    systems, coordinates = collect_coordinates(meta, coordinates_by_dataset)
    check_coordinates(run_query(build_coordinate_check_sql(systems, coordinates)), systems, coordinates)

    # The systems are meta-only, fixing the single pair deployment resolves against.
    origin_system, dest_system = meta["origin_system"], meta["dest_system"]

    # The declared systems' prose publishes with the file (queries table row 2),
    # fetched before the gap check so a gap-failing run still spends only the cheap
    # opening queries.
    system_records = build_system_records(run_query(build_systems_sql(systems)), origin_system, dest_system)

    # The eligible variable names, pooled per distinct origin_data_scope: the columns
    # query is filtered to them, so what comes back is sized by the inventory rather
    # than by the scope's width.
    names_by_scope, excluded_names = partition_variable_names(variable_records, coordinates_by_dataset)
    if excluded_names:
        # Logged so the resulting gap is traceable to the guard rather than read as
        # missing catalog documentation.
        logger.warning(
            f"{len(excluded_names)} variable name(s) cannot enter a SQL predicate and will "
            f"resolve to no origin column: {', '.join(excluded_names)}"
        )

    # One columns query per distinct origin_data_scope; datasets sharing a scope share
    # the result, and the runner's cache collapses identical statements. A scope is
    # handled whole: one query covers all its prefixes.
    columns_by_scope: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for coords in coordinates_by_dataset.values():
        origin_key = _scope_key(coords["origin_data_scope"])
        if origin_key in columns_by_scope:
            continue
        names = names_by_scope.get(origin_key, [])
        # No eligible name under this scope means no row could match, so the query is
        # skipped rather than issued with an empty predicate; every variable there
        # resolves to no origin column and the gap check below reports it.
        columns_by_scope[origin_key] = (
            run_query(build_origin_columns_sql(origin_key, names)) if names else []
        )

    # Matching precedes the mappings fetch, which is keyed on the columns it matched:
    # each variable's matched rows are held, and the transitioning datasets' matched ids
    # are pooled for a single exact-id fetch.
    matched_variables: list[tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]] = []
    mapping_column_ids: set[str] = set()
    for record in sorted(variable_records, key=lambda r: (r["dataset"], r["variable"])):
        coords = coordinates_by_dataset.get(record["dataset"])
        if coords is None:
            raise InventoryError(
                f"Variable '{record['variable']}' references dataset '{record['dataset']}', "
                f"which has no origin_sas_dataset record"
            )
        origin_key = _scope_key(coords["origin_data_scope"])
        column_rows = match_origin_columns(columns_by_scope[origin_key], record["variable"])
        matched_variables.append((record, coords, column_rows))
        # Extraction guideline 4.5: no dest_data_scope means the data source does not
        # change, so column_mappings is never consulted for this dataset's columns.
        if coords["dest_data_scope"]:
            mapping_column_ids.update(row["column_id"] for row in column_rows)

    # One mappings query for the whole run, pooled across every transitioning dataset.
    # Skipped when nothing transitions, and equally when nothing matched -- a run headed
    # for a missing-variable failure spends nothing here.
    mappings_by_column: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if mapping_column_ids:
        for row in run_query(build_column_mappings_sql(sorted(mapping_column_ids))):
            mappings_by_column[row["source_column_id"]].append(row)

    # Gaps accumulate across every dataset and variable rather than failing on the
    # first, so a single run names the complete catalog work order.
    missing_variables: list[dict[str, Any]] = []
    missing_candidates: list[dict[str, Any]] = []
    resolved_variables = []
    for record, coords, column_rows in matched_variables:
        origin_key = _scope_key(coords["origin_data_scope"])
        # The transition gate again, per dataset rather than per fetch: two datasets can
        # share a matched column while only one transitions, and the pooled fetch is
        # keyed on the column id alone. Without this gate the non-transitioning dataset
        # would inherit the other's mappings -- no-equivalent mappings survive any
        # filter -- and read as `no_equivalent` instead of `not_applicable`.
        mappings = mappings_by_column if coords["dest_data_scope"] else {}
        origin_columns = [
            build_origin_column(
                column_row, mappings.get(column_row["column_id"], []), coords["dest_data_scope"]
            )
            for column_row in column_rows
        ]
        # An origin SAS dataset's variables are all documented, so zero matches means
        # missing catalog documentation or a wrong origin_data_scope -- never a legal
        # outcome.
        if not origin_columns:
            missing_variables.append({
                "record_type": "missing_variable",
                "origin_sas_dataset": record["dataset"],
                "origin_sas_variable": record["variable"],
                "origin_data_scope": list(origin_key),
            })
        # An in-transition column with no surviving candidate is a mapping gap: the
        # catalog documented no mapping at all, or only mappings targeting outside
        # dest_data_scope. Either way the remedy is catalog-side.
        if coords["dest_data_scope"]:
            for column in origin_columns:
                if not column["candidates"]:
                    missing_candidates.append({
                        "record_type": "missing_candidate",
                        "origin_sas_dataset": record["dataset"],
                        "origin_sas_variable": record["variable"],
                        "origin_column_id": column["origin_column_id"],
                        "dest_data_scope": list(_scope_key(coords["dest_data_scope"])),
                    })
        resolved_variables.append({
            "record_type": "origin_sas_variable",
            "dataset": record["dataset"],
            "variable": record["variable"],
            # SAS metadata is carried verbatim; nothing from the inventory is lost.
            "type": record.get("type"),
            "format": record.get("format"),
            "length": record.get("length"),
            "label": record.get("label"),
            "origin_columns": origin_columns,
        })

    # Both lists are final here, and nothing later can add to them -- so the raise sits
    # before the dest-table, join, deployment, scope, and concept queries, which would
    # be wasted work on a run that cannot publish. The summary counts only this gate's
    # own gap types; deployment counts belong to the later gate.
    if missing_variables or missing_candidates:
        gaps = missing_variables + missing_candidates
        raise CatalogGapError(
            f"{len(missing_variables)} missing variable(s), "
            f"{len(missing_candidates)} missing mapping(s): "
            + "; ".join(describe_gap(g) for g in gaps),
            gaps,
        )

    dest_tables, sas_parents = compute_dest_tables(resolved_variables, coordinates_by_dataset)

    dest_join_rows = run_query(build_joins_sql(dest_tables)) if dest_tables else []
    # Origin joins describe how the SAS input was assembled, which the converted code
    # reproduces from parent tables. Without a transition the SAS parents ARE the dest
    # tables, so dest_join already covers them.
    in_transition = any(c.get("dest_data_scope") for c in coordinates_by_dataset.values())
    origin_join_rows = run_query(build_joins_sql(sas_parents)) if (in_transition and sas_parents) else []
    origin_joins, dest_joins = build_join_records(dest_join_rows, origin_join_rows)

    # Every record type splits by side. The dest scope is what the converted code
    # reads; without a data source change it is also the origin scope, which is what
    # puts every record in its dest form in that case.
    all_tables = sorted(set(dest_tables) | set(sas_parents))
    dest_scope = concept_scope(dest_tables)
    scope = concept_scope(all_tables)
    schema_ids = sorted(s for s in scope if s.count(".") == 1)
    data_source_ids = sorted(s for s in scope if "." not in s)
    schema_rows = run_query(build_schemas_sql(schema_ids)) if schema_ids else []
    data_source_rows = run_query(build_data_sources_sql(data_source_ids)) if data_source_ids else []
    origin_data_sources, dest_data_sources, origin_schemas, dest_schemas = build_scope_records(
        schema_rows, data_source_rows, dest_scope
    )

    # The primary-key fetch (queries table row 9) runs over the tables known so far --
    # the dest tables, the SAS parents, and the code sets the origin columns point at.
    # It feeds primary_key_columns on every table record, and the dest tables' keys
    # join the dest-column collection: grain columns get read in practice, as join
    # keys, partition filters, and GROUP BY columns.
    origin_ref_ids = {
        column["ref_table_id"]
        for record in resolved_variables
        for column in record["origin_columns"]
        if column.get("ref_table_id")
    }
    known_tables = sorted(set(all_tables) | origin_ref_ids)
    pk_rows = run_query(build_primary_keys_sql(known_tables)) if known_tables else []
    primary_keys = _primary_key_map(pk_rows)

    # The dest columns the emitted code reads, collected three ways: the surviving
    # expressions' columns, the dest joins' condition columns, and the dest tables'
    # primary keys -- fetched by exact id and documented at the same grain as the
    # origin side.
    dest_column_ids = sorted(
        set(referenced_dest_columns(resolved_variables, dest_joins))
        | {
            f"{table_id}.{name}"
            for table_id in dest_tables
            for name in primary_keys.get(table_id, [])
        }
    )
    dest_column_rows = run_query(build_dest_columns_sql(dest_column_ids)) if dest_column_ids else []

    dest_column_records = build_dest_column_records(dest_column_rows)

    # The code sets the columns point at -- on either side -- are tables in play like
    # any other. A code set a dest column reveals arrived after the first primary-key
    # fetch, so that query re-issues once over the late arrivals (row 9's "plus once
    # more").
    ref_table_ids = sorted(
        origin_ref_ids | {r["ref_table_id"] for r in dest_column_records if r.get("ref_table_id")}
    )
    late_ref_ids = sorted(set(ref_table_ids) - set(known_tables))
    if late_ref_ids:
        primary_keys.update(_primary_key_map(run_query(build_primary_keys_sql(late_ref_ids))))

    tables_in_play = sorted(set(all_tables) | set(ref_table_ids))
    deployment_rows = run_query(build_deployment_sql(tables_in_play)) if tables_in_play else []

    # The deployment gate: every table in play must be deployed where the process
    # needs it, or the run fails naming every hole at once. The origin side checks
    # the pairing rule's id list, so a paired parent needs both addresses.
    origin_table_ids = origin_table_set(sas_parents, dest_tables, origin_system, dest_system)
    missing_deployments = collect_missing_deployments(
        dest_tables, origin_table_ids, ref_table_ids, deployment_rows, origin_system, dest_system
    )
    if missing_deployments:
        raise CatalogGapError(
            f"{len(missing_deployments)} missing deployment(s): "
            + "; ".join(describe_gap(g) for g in missing_deployments),
            missing_deployments,
        )

    # With the gate passed, the deferred table prose (row 12) is fetched and the table
    # records assemble -- prose, grain, address.
    prose_rows = run_query(build_tables_sql(tables_in_play)) if tables_in_play else []
    origin_tables = build_origin_tables(
        origin_table_ids, deployment_rows, prose_rows, primary_keys, origin_system
    )
    dest_table_records = build_dest_tables(
        dest_tables, deployment_rows, prose_rows, primary_keys, dest_system
    )
    ref_tables = build_ref_tables(ref_table_ids, deployment_rows, prose_rows, primary_keys)

    # Concepts are matched against the objects actually touched, which is only known
    # once the code sets are resolved -- they are tables in play like any other. The
    # side follows the anchor: origin-only anchors publish origin_concept, every
    # other anchor dest_concept.
    touched = objects_in_play(resolved_variables, tables_in_play, dest_column_ids)
    concept_rows = run_query(build_concepts_sql(touched)) if touched else []
    dest_objects = set(dest_tables) | set(ref_table_ids) | set(dest_column_ids)
    for table_id in list(dest_tables) + list(ref_table_ids):
        dest_objects |= _prefixes(table_id)
    origin_concepts, dest_concepts = build_concept_records(concept_rows, dest_objects)

    # Write order: descend the catalog hierarchy, origin before dest within each
    # level, the SAS input last -- every pointer a variable carries is defined before
    # the bulk payload arrives.
    return [
        build_meta_record(meta),
        *system_records,
        *origin_data_sources,
        *dest_data_sources,
        *origin_schemas,
        *dest_schemas,
        *origin_tables,
        *dest_table_records,
        *ref_tables,
        *dest_column_records,
        *origin_joins,
        *dest_joins,
        *origin_concepts,
        *dest_concepts,
        *build_dataset_records(dataset_records, coordinates_by_dataset),
        *resolved_variables,
    ]


def build_meta_record(meta: dict[str, Any]) -> dict[str, Any]:
    """Build the output meta record: what was DECLARED process-wide, carried through.

    The systems are always present (Step 1 fails a meta missing either). Absent data
    scopes are written as explicit nulls so every record carries the same field set;
    a null dest_data_scope is how "no data transition" is stated.

    Args:
        meta: The inventory's meta record.

    Returns:
        The meta record.
    """
    return {
        "record_type": "meta",
        "process_name": meta.get("process_name"),
        **{field: meta.get(field) for field in COORDINATES},
    }


def build_dataset_records(
    dataset_records: list[dict[str, Any]], coordinates_by_dataset: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Build the output origin_sas_dataset records with fully resolved data scopes.

    These state what actually applied to the dataset, which may differ from meta -- meta
    is what was declared. Only the data scopes appear: the systems are process-wide, so
    a dataset system would be trivially derivable from the header.

    Args:
        dataset_records: The inventory's origin_sas_dataset records.
        coordinates_by_dataset: Effective data scopes per dataset name.

    Returns:
        One record per input dataset, sorted by dataset.
    """
    return [
        {
            "record_type": "origin_sas_dataset",
            "dataset": record["dataset"],
            "filepath": record.get("filepath"),
            **coordinates_by_dataset[record["dataset"]],
        }
        for record in sorted(dataset_records, key=lambda r: r["dataset"])
    ]


def log_summary(records: list[dict[str, Any]]) -> None:
    """Log the run summary the skill's report step needs.

    Args:
        records: The full output record list.
    """
    variables = [r for r in records if r["record_type"] == "origin_sas_variable"]
    dest_tables = [r for r in records if r["record_type"] == "dest_table"]

    statuses: dict[str, int] = defaultdict(int)
    ambiguous = []
    for record in variables:
        columns = record["origin_columns"]
        for column in columns:
            statuses[column["mapping_status"]] += 1
        if len(columns) > 1:
            ambiguous.append(f"{record['dataset']}.{record['variable']}")

    # A published file contains no variable without an origin column and no undeployed
    # table in play -- both fail the run as catalog gaps -- so neither is counted here.
    by_status = ", ".join(f"{status}={statuses[status]}" for status in sorted(statuses)) or "none"

    logger.info(f"Origin columns by mapping status: {by_status}")
    logger.info(f"Ambiguous variables ({len(ambiguous)}): {', '.join(ambiguous) or 'none'}")
    logger.info(
        f"Dest tables: {len(dest_tables)}; dest columns: "
        f"{sum(1 for r in records if r['record_type'] == 'dest_column')}"
    )
    logger.info(
        f"Joins: {sum(1 for r in records if r['record_type'] == 'origin_join')} origin, "
        f"{sum(1 for r in records if r['record_type'] == 'dest_join')} dest"
    )
    logger.info(
        f"Concepts: {sum(1 for r in records if r['record_type'] == 'origin_concept')} origin, "
        f"{sum(1 for r in records if r['record_type'] == 'dest_concept')} dest"
    )
    logger.info(
        f"Scope: {sum(1 for r in records if r['record_type'].endswith('_data_source'))} data source(s), "
        f"{sum(1 for r in records if r['record_type'].endswith('_schema'))} schema(s), "
        f"{sum(1 for r in records if r['record_type'] == 'origin_table')} origin table(s), "
        f"{sum(1 for r in records if r['record_type'] == 'ref_table')} code set(s)"
    )


def write_jsonl(records: list[dict[str, Any]], output_path: Path) -> None:
    """Write records to a JSONL file.

    Args:
        records: The output records, already ordered.
        output_path: Path to the output JSONL file.

    Raises:
        OSError: If the output directory cannot be created or the file cannot be written.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")
    logger.info(f"Wrote {len(records)} records to {output_path}")


def remove_stale_artifact(path: Path, description: str) -> None:
    """Remove an artifact left by an earlier run, if one is there.

    A run's outcome must be legible from the files it leaves behind: a failure that
    left a prior run's resolution in place, or a success that left the prior work
    order, would let a downstream reader pair a fresh artifact with a stale one.

    Args:
        path: Path to the artifact; a no-op when it does not exist.
        description: What the artifact is, for the log line.

    Raises:
        OSError: If the file exists but cannot be removed.
    """
    if path.exists():
        path.unlink()
        logger.info(f"Removed the stale {description}: {path}")


def main() -> None:
    """Resolve a SAS variable inventory against metadata_db based on a TOML config."""
    parser = argparse.ArgumentParser(description="Resolve a SAS variable inventory against metadata_db")
    parser.add_argument("--config", type=Path, required=True, help="Path to TOML configuration file")
    args = parser.parse_args()

    setup_logging(log_dir="logs/sas_parsing")
    logger.info("=" * 60)

    try:
        if not args.config.exists():
            logger.error(f"Config file not found: {args.config}")
            sys.exit(1)
        config = parse_config(args.config)

        input_schema = Path(config["input_schema"])
        meta, dataset_records, variable_records = load_inventory(input_schema)
        process_name = meta.get("process_name")
        if not process_name:
            raise InventoryError("Inventory meta record has no process_name")

        output_path = Path(config["output_dir"]) / str(process_name) / OUTPUT_FILENAME
        gaps_path = output_path.parent / GAPS_FILENAME
        if output_path.exists() and not config["overwrite"]:
            logger.error(f"Output file exists and overwrite is false: {output_path}")
            sys.exit(1)
        elif output_path.exists():
            logger.warning(f"Output file exists, overwriting: {output_path}")

        # A draft belongs to the run that wrote it. Clearing a previous run's draft here
        # means a .draft on disk always came from this run, whatever its outcome: only
        # the validation step below writes one, so a run that fails earlier (a catalog
        # gap, an unresolvable coordinate, a transport failure) would otherwise leave an
        # older run's rejected output beside its own fresh artifacts.
        draft_path = output_path.with_suffix(output_path.suffix + ".draft")
        remove_stale_artifact(draft_path, "rejected draft from an earlier run")

        # The token is read from the environment, never the committed config, so no
        # secret is checked in; .env is loaded first for local runs.
        load_dotenv()
        token = os.environ.get(str(config["mcp_token_env"]))
        if not token:
            logger.warning(
                f"{config['mcp_token_env']} is not set; the MCP server will reject the "
                f"request with HTTP 401"
            )

        client = MCPClient(str(config["mcp_url"]), timeout_s=float(config["mcp_timeout_s"]), token=token)
        runner = QueryRunner(client, str(config["mcp_database"]))
        records = resolve(meta, dataset_records, variable_records, runner)

        logger.info(f"Ran {len(runner.calls)} catalog queries")
        log_summary(records)
        # Validate before the file reaches its final path: sas-conversion-planning reads
        # it, so an invalid resolution must never be mistakable for a usable one. The
        # rejected draft is left beside it for debugging.
        write_jsonl(records, draft_path)
        errors = validate_schema_resolution(draft_path, input_schema)
        if errors:
            for error in errors:
                logger.error(f"VALIDATION FAILED: {error}")
            # A rejected run publishes nothing, so a prior run's resolution goes too:
            # sas-conversion-planning must never read an outdated resolution as current.
            # A prior run's work order goes as well: every catalog gate passed before the
            # draft was written, so the gaps it recorded are fixed, and leaving it beside
            # no resolution would misrepresent this failure as a catalog gap.
            remove_stale_artifact(output_path, "resolution from an earlier run")
            remove_stale_artifact(gaps_path, "catalog work order")
            logger.error(
                f"{len(errors)} validation error(s); the rejected output is at {draft_path}"
            )
            sys.exit(1)
        draft_path.replace(output_path)
        # A successful run removes a leftover work order from a prior failed run, so a
        # stale gaps file never outlives its fix.
        remove_stale_artifact(gaps_path, "catalog work order")
        logger.info(f"SUCCESS: {len(records)} records written to {output_path}")
    except CatalogGapError as e:
        # The catalog cannot fully account for the SAS input. No draft exists -- the
        # raise precedes the output write, and any earlier run's draft was cleared at
        # startup -- so the failure's artifact is the
        # machine-readable work order, never a resolution: a prior run's resolution is
        # removed so planning cannot read it beside a fresh work order. The write is
        # guarded on its own, since an OSError raised in a handler escapes the
        # statement's other handlers and would surface as a raw traceback.
        try:
            remove_stale_artifact(output_path, "resolution from an earlier run")
            write_jsonl(e.gaps, gaps_path)
        except OSError as write_error:
            logger.error(f"Failed to write the catalog work order to {gaps_path}: {write_error}")
            sys.exit(1)
        for error in validate_catalog_gaps(gaps_path):
            logger.error(f"VALIDATION FAILED: {error}")
        for gap in e.gaps:
            logger.error(f"CATALOG GAP: {describe_gap(gap)}")
        totals: dict[str, int] = defaultdict(int)
        for gap in e.gaps:
            totals[gap["record_type"]] += 1
        # The two gates never mix records, so the summary counts only the failing
        # gate's own gap types (variables and mappings, or deployments).
        if totals["missing_deployment"]:
            summary = f"{totals['missing_deployment']} missing deployment(s)"
        else:
            summary = (
                f"{totals['missing_variable']} missing variable(s), "
                f"{totals['missing_candidate']} missing mapping(s)"
            )
        logger.error(
            f"{summary}; work order written to {gaps_path} -- document the gaps in the "
            f"catalog or fix the coordinates; never trim the inventory to pass"
        )
        sys.exit(1)
    except CoordinateError as e:
        # Guideline 3: an unresolvable coordinate stops the run rather than producing a
        # plausible, wrong file.
        for problem in e.problems:
            logger.error(f"UNRESOLVABLE COORDINATE: {problem}")
        sys.exit(1)
    except ValueError as e:
        # InventoryError and the config-field check both subclass/raise ValueError;
        # the more specific CatalogGapError and CoordinateError are caught above.
        logger.error(f"Invalid input: {e}")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"Failed: {e}")
        sys.exit(1)
    finally:
        logger.info("=" * 60)


if __name__ == "__main__":
    main()
