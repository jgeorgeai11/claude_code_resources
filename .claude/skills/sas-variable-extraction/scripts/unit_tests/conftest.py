"""Shared fixtures and import paths for the sas-variable-extraction script unit tests.

Serves both test modules in this directory: the fixtures below are consumed by
test_extract_sas_dataset_schemas.py, and the data_validation/ path entry is what lets
test_data_val_extract_sas_dataset_schemas.py import the validator by name.
"""

import sys
from pathlib import Path

import pytest

# Add the scripts directory to the path so extract_sas_dataset_schemas resolves, and its
# data_validation/ subdirectory so the data_val module resolves the same way the source
# script imports it (extract_sas_dataset_schemas.py inserts data_validation/ on its own path)
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPTS_DIR))
sys.path.insert(0, str(_SCRIPTS_DIR / "data_validation"))


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
def fixtures_dir() -> Path:
    """Path to test fixture files.

    Returns:
        The unit_tests/fixtures directory beside this conftest.
    """
    return Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def test_config_path(fixtures_dir: Path) -> Path:
    """Path to the test TOML config file.

    Args:
        fixtures_dir: The test fixture files directory.

    Returns:
        Path to fixtures/test_config.toml.
    """
    return fixtures_dir / "test_config.toml"
