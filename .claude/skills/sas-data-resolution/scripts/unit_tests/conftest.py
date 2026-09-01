"""Shared fixtures for the sas-data-resolution script unit tests.

The builders and the fake MCP client these fixtures stand on live in
catalog_fixtures.py, so both this file and the test modules import them by a
unique module name rather than relying on how pytest resolves `conftest`.
"""

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

# Make the scripts directory importable so tests import the modules by name.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from catalog_fixtures import (
    OCS_CLM,
    OCS_CLM_LINE,
    EDW_BENE,
    EDW_CLM,
    EDW_CLM_LINE,
    FakeMCPClient,
    column,
    concept,
    database_prose,
    deployment,
    mapping,
    relationship,
    schema_prose,
    table_prose,
    variable,
)


@pytest.fixture(autouse=True)
def _run_from_tmp_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test in this directory from its own temporary directory.

    The scripts under test call `setup_logging` with a cwd-relative `log_dir`, so
    a test that drives one through `main()` writes a real log file wherever pytest
    happened to be launched from. Anchoring the cwd here keeps that byproduct
    inside the directory pytest deletes afterwards, and covers any `main()` test
    added later without it having to remember. Every path these tests build is
    absolute -- from `tmp_path` or from `__file__` -- so nothing else depends on
    where the run stands.

    Args:
        tmp_path: The test's temporary directory.
        monkeypatch: The pytest monkeypatch fixture, which restores the cwd after.
    """
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def catalog() -> dict[str, list[Any]]:
    """A small metadata_db, modelled on the committed fixture_ocs -> fixture_edw example.

    Tests mutate this before resolving to build the scenario they need.

    Returns:
        The catalog, keyed by the fake client's row kinds. Most keys hold row dicts;
        `systems`, `data_sources`, `schemas`, and `tables` hold bare ids and back the
        coordinate check alone, with those relations' prose under `database_prose` /
        `schema_prose` / `table_prose`. A new table therefore needs a `tables` entry, a
        `table_prose` row, its `columns`, and a `deployment` row.
    """
    return {
        "systems": ["warehouse", "edw"],
        "data_sources": ["fixture_ocs", "fixture_edw"],
        "schemas": ["fixture_ocs.general", "fixture_edw.claims_vw"],
        "tables": [OCS_CLM, OCS_CLM_LINE, EDW_BENE, EDW_CLM, EDW_CLM_LINE],
        "columns": [
            column(OCS_CLM, "claim_no", "varchar(15)", is_primary_key=True),
            column(OCS_CLM, "person_key", "varchar(11)", is_primary_key=True),
            column(OCS_CLM, "bene_sex_cd", "char(1)"),
            column(OCS_CLM, "clm_from_dt", "date"),
            column(OCS_CLM, "sbmt_chrg_amt", "decimal(12,2)"),
            column(OCS_CLM_LINE, "claim_no", "varchar(15)", is_primary_key=True),
            column(OCS_CLM_LINE, "person_key", "varchar(11)", is_primary_key=True),
            column(OCS_CLM_LINE, "lineitem", "integer", is_primary_key=True),
            column(
                OCS_CLM_LINE, "hcpcs_cd", "char(5)",
                is_nullable=False,
                ref_table_id="ref.codes.hcpcs_cd",
                notes="Wide occurrence 1 of 45; deep form: clm_line.hcpcs_cd.",
            ),
            # The target side, fetched by exact id for the target_column records: the
            # columns the fixture's expressions read, plus the `id` columns the
            # relationships' join conditions test.
            column(EDW_BENE, "id", "integer", is_primary_key=True),
            column(EDW_BENE, "bene_sex_cd", "char(1)", ref_table_id="ref.codes.bene_sex_cd"),
            column(EDW_CLM, "id", "integer", is_primary_key=True),
            column(EDW_CLM, "clm_from_dt", "date", is_nullable=False),
            column(EDW_CLM, "clm_sbmt_chrg_amt", "decimal(12,2)"),
            column(EDW_CLM_LINE, "id", "integer", is_primary_key=True),
            column(EDW_CLM_LINE, "clm_line_sbmt_chrg_amt", "decimal(12,2)"),
            column(
                EDW_CLM_LINE, "clm_line_hcpcs_cd", "char(5)",
                is_nullable=False, ref_table_id="ref.codes.hcpcs_cd",
            ),
        ],
        "mappings": [
            mapping(f"{OCS_CLM}.bene_sex_cd", "default", f"{EDW_BENE}.bene_sex_cd", [EDW_BENE]),
            mapping(f"{OCS_CLM}.clm_from_dt", "default", f"{EDW_CLM}.clm_from_dt", [EDW_CLM], validated=True),
            mapping(f"{OCS_CLM}.claim_no", "default", None, [], notes="No EDW equivalent."),
            mapping(f"{OCS_CLM}.person_key", "default", None, [], notes="No reliable EDW equivalent."),
            mapping(f"{OCS_CLM_LINE}.claim_no", "default", None, [], notes="No EDW equivalent."),
            mapping(f"{OCS_CLM_LINE}.person_key", "default", None, [], notes="No reliable EDW equivalent."),
            mapping(
                f"{OCS_CLM}.sbmt_chrg_amt", "header", f"{EDW_CLM}.clm_sbmt_chrg_amt", [EDW_CLM],
                use_when="Use the claim-header charge.", validated=True,
            ),
            mapping(
                f"{OCS_CLM}.sbmt_chrg_amt", "line_rollup",
                f"SUM({EDW_CLM_LINE}.clm_line_sbmt_chrg_amt)", [EDW_CLM_LINE],
                use_when="Use to reconcile against line-level charges.",
            ),
            mapping(f"{OCS_CLM_LINE}.hcpcs_cd", "default", f"{EDW_CLM_LINE}.clm_line_hcpcs_cd", [EDW_CLM_LINE]),
        ],
        "joins": [
            relationship(OCS_CLM_LINE, OCS_CLM),
            relationship(EDW_CLM, EDW_BENE),
            relationship(EDW_CLM_LINE, EDW_CLM),
        ],
        "concepts": [
            # All four anchor depths, on both sides.
            concept("fixture_ocs.concept.claim", [f"{OCS_CLM}.claim_no"]),
            concept(f"{OCS_CLM}.concept.claim_grain", [OCS_CLM]),
            concept(f"{OCS_CLM}.claim_no.concept.claim_number", [f"{OCS_CLM}.claim_no"]),
            concept("fixture_edw.claims_vw.concept.claim", [EDW_CLM]),
            concept(f"{EDW_CLM}.concept.final_action", [EDW_CLM]),
            concept(f"{EDW_BENE}.bene_sex_cd.concept.sex_coding", [f"{EDW_BENE}.bene_sex_cd"]),
            # A sibling schema of an in-play database: no variable resolves under it, so
            # it must stay out however the scope is matched.
            concept("fixture_edw.other_schema.concept.unrelated", []),
        ],
        "table_prose": [
            table_prose(OCS_CLM, "One row per claim segment; a claim may span several."),
            table_prose(OCS_CLM_LINE, "One row per claim line."),
            table_prose(EDW_BENE), table_prose(EDW_CLM), table_prose(EDW_CLM_LINE),
        ],
        "schema_prose": [
            schema_prose("fixture_ocs.general"),
            schema_prose("fixture_edw.claims_vw"),
        ],
        "database_prose": [
            database_prose("fixture_ocs"), database_prose("fixture_edw"),
        ],
        # clm is copied into edw as well as living in warehouse; clm_line is warehouse-only.
        # The code sets live on the catalog's own instance -- a nowhere-deployed code
        # set is a catalog gap, so every fixture pointer must resolve to an address.
        "deployment": [
            deployment(EDW_BENE, "edw"),
            deployment(EDW_CLM, "edw"),
            deployment(EDW_CLM_LINE, "edw"),
            deployment(OCS_CLM, "warehouse"),
            deployment(OCS_CLM, "edw"),
            deployment(OCS_CLM_LINE, "warehouse"),
            deployment("ref.codes.hcpcs_cd", "metadata_db"),
            deployment("ref.codes.bene_sex_cd", "metadata_db"),
        ],
    }


@pytest.fixture
def client(catalog: dict[str, list[Any]]) -> FakeMCPClient:
    """A fake MCP client backed by the catalog fixture.

    Args:
        catalog: The in-memory catalog.

    Returns:
        The fake client.
    """
    return FakeMCPClient(catalog)


@pytest.fixture
def run_query(client: FakeMCPClient) -> Callable[[str], list[dict[str, Any]]]:
    """A query callable the resolver can be driven with.

    Args:
        client: The fake MCP client.

    Returns:
        A callable taking SQL and returning rows.
    """
    return lambda sql: client.run_sql("metadata_db", sql)


@pytest.fixture
def meta() -> dict[str, Any]:
    """The inventory meta record for a OCS -> EDW conversion.

    Returns:
        The meta record.
    """
    return {
        "record_type": "meta",
        "process_name": "ocs_claims",
        "origin_system": "warehouse",
        "dest_system": "edw",
        "origin_data_scope": ["fixture_ocs.general"],
        "dest_data_scope": ["fixture_edw"],
    }


@pytest.fixture
def datasets() -> list[dict[str, Any]]:
    """Two inventory dataset records, the second narrowing origin_data_scope to one table.

    Returns:
        The dataset records.
    """
    return [
        {"record_type": "origin_sas_dataset", "dataset": "SRCLIB.OCS_CLAIMS", "filepath": "data/sas/ocs_claims.sas7bdat"},
        {
            "record_type": "origin_sas_dataset",
            "dataset": "SRCLIB.OCS_MEMBERS",
            "filepath": "data/sas/ocs_members.sas7bdat",
            "origin_data_scope": [OCS_CLM],
        },
    ]


@pytest.fixture
def variables() -> list[dict[str, Any]]:
    """Inventory variable records across both datasets.

    Returns:
        The variable records.
    """
    return [
        variable("SRCLIB.OCS_CLAIMS", "claim_no"),
        variable("SRCLIB.OCS_CLAIMS", "bene_sex_cd"),
        variable("SRCLIB.OCS_CLAIMS", "hcpcs_cd"),
        variable("SRCLIB.OCS_MEMBERS", "bene_sex_cd"),
    ]
