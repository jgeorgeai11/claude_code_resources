"""Shared catalog builders and the fake MCP client for the resolver unit tests.

The resolver's only boundary is the MCP client, so the fixtures here stand up a
small in-memory catalog modelled on fixture_ocs (warehouse) -> fixture_edw (edw) and
a fake client that answers each query by filtering it, the way Postgres would.
Filtering rather than replaying canned rows keeps the tests honest: a test that
narrows a scope really does see fewer rows.
"""

import copy
import re
from typing import Any


def column(
    table_id: str,
    name: str,
    data_type: str,
    is_primary_key: bool = False,
    is_nullable: bool = True,
    ref_table_id: str | None = None,
    description: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Build a catalog.columns row.

    Args:
        table_id: The owning table's id.
        name: The column name.
        data_type: The column's catalog data type.
        is_primary_key: Whether the column is part of the primary key.
        is_nullable: Whether the column admits nulls.
        ref_table_id: The code set enumerating this column's values, when documented.
        description: What the column is.
        notes: Anything beyond the description -- aliases, storage, wide/deep form.

    Returns:
        The row as run_sql would return it.
    """
    return {
        "column_id": f"{table_id}.{name}",
        "table_id": table_id,
        "column_name": name,
        "data_type": data_type,
        "is_nullable": is_nullable,
        "is_primary_key": is_primary_key,
        "ref_table_id": ref_table_id,
        "description": description if description is not None else f"The {name} column.",
        "notes": notes,
    }


def mapping(
    source_column_id: str,
    mapping_name: str,
    target_expression: str | None,
    target_tables_referenced: list[str],
    use_when: str | None = None,
    notes: str | None = None,
    validated: bool = False,
) -> dict[str, Any]:
    """Build a catalog.column_mappings row.

    Args:
        source_column_id: The mapped source column.
        mapping_name: The mapping's name, unique per source column.
        target_expression: The target expression, or None for a drop.
        target_tables_referenced: The target tables the expression reads.
        use_when: When to prefer this candidate; required when a column has several.
        notes: Rationale; the catalog guarantees it on a no-equivalent mapping.
        validated: Whether the mapping has been validated.

    Returns:
        The row as run_sql would return it.
    """
    return {
        "source_column_id": source_column_id,
        "mapping_name": mapping_name,
        "target_expression": target_expression,
        "target_tables_referenced": target_tables_referenced,
        "use_when": use_when,
        "notes": notes,
        "validated": validated,
    }


def relationship(
    table_a_id: str,
    table_b_id: str,
    name: str = "default",
    notes: str | None = None,
    cardinality: str = "many_to_one",
    validated: bool = False,
) -> dict[str, Any]:
    """Build a catalog.table_relationships row.

    Args:
        table_a_id: The many-side endpoint.
        table_b_id: The one-side endpoint.
        name: The relationship name.
        notes: Grain caveats -- when the join fans out, and what to filter.
        cardinality: The join's cardinality, as the catalog spells it.
        validated: Whether the relationship has been validated.

    Returns:
        The row as run_sql would return it.
    """
    return {
        "table_a_id": table_a_id,
        "table_b_id": table_b_id,
        "relationship_name": name,
        "join_condition": f"{table_a_id}.id = {table_b_id}.id",
        "cardinality": cardinality,
        "use_when": None,
        "notes": notes,
        "validated": validated,
    }


def deployment(table_id: str, system: str) -> dict[str, Any]:
    """Build a catalog.deployment_tables row.

    The physical name follows the fixture's naming rule: edw is the catalog-native
    deployment, so its physical database name is the catalog id unchanged, while every
    other system exposes the same database under a `{database}_{system}` alias. That is
    what makes a twice-deployed table resolve to `fixture_ocs` in edw and
    `fixture_ocs_warehouse` in warehouse.

    Args:
        table_id: The deployed table.
        system: The system it is deployed in.

    Returns:
        The row as run_sql would return it.
    """
    database, schema, table = table_id.split(".")
    return {
        "table_id": table_id,
        "system": system,
        "physical_database_name": f"{database}_{system}" if system != "edw" else database,
        "physical_schema_name": schema,
        "physical_table_name": table,
    }


def concept(concept_id: str, related: list[str]) -> dict[str, Any]:
    """Build a catalog.concepts row.

    Args:
        concept_id: The concept id, containing a '.concept.' marker. The anchor may be a
            database, a schema, a table, or a column.
        related: The related object ids.

    Returns:
        The row as run_sql would return it.
    """
    return {
        "concept_id": concept_id,
        "label": concept_id.split(".concept.")[-1].replace("_", " ").title(),
        "definition": f"Definition of {concept_id}.",
        "notes": f"Source: the {concept_id.split('.')[0]} documentation.",
        "related_object_ids": related,
    }


def table_prose(table_id: str, description: str | None = None, notes: str | None = None) -> dict[str, Any]:
    """Build a catalog.tables row (the prose a table carries).

    Args:
        table_id: The table's id.
        description: What the table is, including the grain one row sits at.
        notes: Anything beyond the description.

    Returns:
        The row as run_sql would return it.
    """
    return {
        "table_id": table_id,
        "description": description if description is not None else f"One row per {table_id.split('.')[-1]}.",
        "notes": notes,
    }


def schema_prose(schema_id: str, description: str | None = None, notes: str | None = None) -> dict[str, Any]:
    """Build a catalog.schemas row.

    Args:
        schema_id: The 2-segment schema id.
        description: What the schema covers.
        notes: Anything beyond the description.

    Returns:
        The row as run_sql would return it.
    """
    return {
        "schema_id": schema_id,
        "description": description if description is not None else f"The {schema_id} schema.",
        "notes": notes,
    }


def database_prose(data_source_id: str, description: str | None = None, notes: str | None = None) -> dict[str, Any]:
    """Build a catalog.data_sources row. `owner` is not selected by the resolver.

    Args:
        data_source_id: The 1-segment data source id.
        description: What the data source holds.
        notes: Anything beyond the description.

    Returns:
        The row as run_sql would return it.
    """
    return {
        "data_source_id": data_source_id,
        "description": description if description is not None else f"The {data_source_id} data source.",
        "notes": notes,
    }


OCS_CLM = "fixture_ocs.general.clm"
OCS_CLM_LINE = "fixture_ocs.general.clm_line"
EDW_BENE = "fixture_edw.claims_vw.bene"
EDW_CLM = "fixture_edw.claims_vw.clm"
EDW_CLM_LINE = "fixture_edw.claims_vw.clm_line"


def _under_any(dotted: str, scopes: list[str]) -> bool:
    """Return whether an id is a descendant-or-self of any prefix, as ltree `<@` does.

    Args:
        dotted: The id to test.
        scopes: The prefixes to test against.

    Returns:
        True when the id falls under at least one prefix.
    """
    return any(dotted == p or dotted.startswith(p + ".") for p in scopes)


def _array_literals(sql: str, index: int = 0) -> list[str]:
    """Extract the string literals from the nth `array[...]` in a statement.

    Args:
        sql: The statement the resolver built.
        index: Which array to read, in source order.

    Returns:
        The literal values, or an empty list when the statement holds no such array.
    """
    arrays = re.findall(r"array\[(.*?)\]", sql)
    return re.findall(r"'([^']*)'", arrays[index]) if index < len(arrays) else []


class FakeMCPClient:
    """An MCP client that answers the resolver's queries from an in-memory catalog.

    Attributes:
        catalog: The in-memory catalog answered from, keyed by the fake's own row
            kinds; tests mutate it to build scenarios. Most keys hold row dicts;
            `systems`, `data_sources`, `schemas`, and `tables` hold bare ids and back
            the coordinate check alone, with the same relations' prose under
            `table_prose` / `schema_prose` / `database_prose`.
        calls: Every (kind, sql) pair the resolver issued, in order.
    """

    def __init__(self, catalog: dict[str, list[Any]]) -> None:
        """Initialize the fake.

        Args:
            catalog: The in-memory catalog to answer from.
        """
        self.catalog = catalog
        self.calls: list[tuple[str, str]] = []

    def kinds(self) -> list[str]:
        """Return just the kinds of the queries issued, in order.

        Returns:
            The query kinds.
        """
        return [kind for kind, _ in self.calls]

    def run_sql(self, database: str, sql: str) -> list[dict[str, Any]]:
        """Answer one query by filtering the in-memory catalog.

        Rows are deep-copied on the way out, the way the real client returns freshly
        deserialized rows: a caller that mutates a returned row must not reach back
        into the catalog and change what later queries in the same test see. The copy
        has to be deep because list-valued cells such as `related_object_ids` and
        `target_tables_referenced` would otherwise still alias the catalog's own lists.

        Args:
            database: Ignored; accepted only for interface parity with MCPClient.
            sql: The statement to answer.

        Returns:
            The matching rows.

        Raises:
            AssertionError: If the resolver issues a statement this fake cannot classify.
        """
        return [copy.deepcopy(r) for r in self._run_sql(database, sql)]

    def _run_sql(self, database: str, sql: str) -> list[dict[str, Any]]:
        """Classify one query and filter the in-memory catalog for it.

        Args:
            database: Ignored; accepted only for interface parity with MCPClient.
            sql: The statement to answer.

        Returns:
            The matching rows, which may alias the catalog's own row dicts.

        Raises:
            AssertionError: If the resolver issues a statement this fake cannot classify.
        """
        if "catalog.systems" in sql and "union all" in sql:
            self.calls.append(("coordinates", sql))
            return self._coordinates(sql)
        if "from catalog.systems" in sql:
            # The declared systems' prose (origin_system / dest_system records). A test
            # that cares about the prose supplies `system_prose` rows -- including an
            # empty list, for a declared system the catalog carries no prose for;
            # otherwise one default row per declared system is synthesized.
            self.calls.append(("system_prose", sql))
            wanted = set(_array_literals(sql))
            prose = self.catalog.get("system_prose")
            if prose is None:
                prose = [
                    {"system": s, "description": f"The {s} system.", "notes": None}
                    for s in self.catalog["systems"]
                ]
            return [r for r in prose if r["system"] in wanted]
        if "from catalog.columns" in sql and "where is_primary_key" in sql:
            # The primary-key fetch feeding primary_key_columns and the grain fold.
            self.calls.append(("primary_keys", sql))
            wanted = set(_array_literals(sql))
            return [
                {"table_id": r["table_id"], "column_name": r["column_name"]}
                for r in self.catalog["columns"]
                if r["is_primary_key"] and r["table_id"] in wanted
            ]
        if "from catalog.columns" in sql and "operator(catalog.<@)" in sql:
            # Both predicates are applied, the way Postgres would: the scope bounds
            # where a column may live, the name array bounds which are fetched at all.
            # A test that widens a scope but not the names really does see no more rows.
            self.calls.append(("columns", sql))
            scopes = _array_literals(sql, 0)
            names = {name.lower() for name in _array_literals(sql, 1)}
            return [
                r for r in self.catalog["columns"]
                if _under_any(r["column_id"], scopes) and r["column_name"].lower() in names
            ]
        if "from catalog.columns" in sql:
            # The exact-id fetch for the dest columns the emitted code reads.
            self.calls.append(("dest_columns", sql))
            wanted = set(_array_literals(sql))
            return [r for r in self.catalog["columns"] if r["column_id"] in wanted]
        if "catalog.column_mappings" in sql:
            # An exact-id fetch over the matched origin columns, not a scope scan.
            self.calls.append(("mappings", sql))
            wanted = set(_array_literals(sql))
            return [r for r in self.catalog["mappings"] if r["source_column_id"] in wanted]
        if "catalog.table_relationships" in sql:
            self.calls.append(("joins", sql))
            tables = set(_array_literals(sql))
            return [
                r for r in self.catalog["joins"]
                if r["table_a_id"] in tables and r["table_b_id"] in tables
            ]
        if "catalog.concepts" in sql:
            self.calls.append(("concepts", sql))
            # Mirrors the resolver's rule: the namespace -- the path before
            # '.concept.' -- equals a scope entry exactly, at whatever depth.
            scope = set(_array_literals(sql))
            return [
                r for r in self.catalog["concepts"]
                if r["concept_id"].split(".concept.")[0] in scope
            ]
        if "from catalog.tables " in sql:
            self.calls.append(("tables", sql))
            wanted = set(_array_literals(sql))
            return [r for r in self.catalog.get("table_prose", []) if r["table_id"] in wanted]
        if "from catalog.schemas " in sql:
            self.calls.append(("schemas", sql))
            wanted = set(_array_literals(sql))
            return [r for r in self.catalog.get("schema_prose", []) if r["schema_id"] in wanted]
        if "from catalog.data_sources " in sql:
            self.calls.append(("databases", sql))
            wanted = set(_array_literals(sql))
            return [
                r for r in self.catalog.get("database_prose", [])
                if r["data_source_id"] in wanted
            ]
        if "catalog.deployment_tables" in sql:
            self.calls.append(("deployment", sql))
            tables = set(_array_literals(sql))
            return [r for r in self.catalog["deployment"] if r["table_id"] in tables]
        raise AssertionError(f"Unclassifiable query: {sql}")

    def _coordinates(self, sql: str) -> list[dict[str, Any]]:
        """Answer the coordinate-check query.

        Args:
            sql: The coordinate-check statement.

        Returns:
            One row per system and data scope entry that exists in the catalog.
        """
        systems = _array_literals(sql, 0)
        coordinates = _array_literals(sql, 1)
        rows = [{"kind": "system", "id": s} for s in systems if s in self.catalog["systems"]]
        for kind, key in (("data_source", "data_sources"), ("schema", "schemas"), ("table", "tables")):
            rows += [{"kind": kind, "id": c} for c in coordinates if c in self.catalog[key]]
        return rows


def variable(dataset: str, name: str, sas_type: str = "char", length: int = 8) -> dict[str, Any]:
    """Build an inventory variable record.

    Args:
        dataset: The owning SAS dataset.
        name: The SAS variable name.
        sas_type: The SAS type.
        length: The SAS storage width.

    Returns:
        The variable record.
    """
    return {
        "record_type": "origin_sas_variable",
        "dataset": dataset,
        "variable": name,
        "type": sas_type,
        "format": "$CHAR8." if sas_type == "char" else "BEST12.",
        "length": length,
        "label": name.replace("_", " ").title(),
    }
