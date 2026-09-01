# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pandas",
#     "pyreadstat",
#     "python-json-logger",
# ]
# ///
"""Generate synthetic .xpt test fixture files for extract_sas_dataset_schemas tests.

Uses SAS XPORT v8 format (.xpt) because no Python package can write .sas7bdat.
pyreadstat reads both formats identically, returning the same metadata structure.
"""

import sys
import argparse
import tomllib
from pathlib import Path

import pandas as pd
import pyreadstat

# logconfig ships in this skill's scripts/ folder. Resolve against this file, never
# the cwd, exactly as the two sibling scripts in this skill do.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parents[1]))
from logconfig import setup_logging, get_logger

logger = get_logger(__name__)


def generate_medical_claims(output_dir: Path) -> Path:
    """Generate medical_claims.xpt with 5 rows and labeled columns.

    Args:
        output_dir: Directory to write the file.

    Returns:
        Path to the generated file.
    """
    filepath = output_dir / "medical_claims.xpt"
    df = pd.DataFrame({
        "member_id": ["M001", "M002", "M003", "M004", "M005"],
        "claim_id": ["CLM000000000001", "CLM000000000002", "CLM000000000003", "CLM000000000004", "CLM000000000005"],
        "amount": [150.00, 275.50, 89.99, 1200.00, 45.75],
        "dx_code": ["J06900", "E119000", "M545000", "I10X000", "Z000000"],
        "proc_code": ["99213", "99214", "99203", "99215", "99211"],
        "service_date": [20240101.0, 20240115.0, 20240201.0, 20240210.0, 20240301.0],
        "provider_id": ["PRV0000001", "PRV0000002", "PRV0000003", "PRV0000001", "PRV0000004"],
    })
    column_labels = {
        "member_id": "Member Identifier",
        "claim_id": "Claim Identifier",
        "amount": "Claim Amount",
        "dx_code": "Diagnosis Code",
        "proc_code": "Procedure Code",
        "service_date": "Service Date",
        "provider_id": "Provider Identifier",
    }
    pyreadstat.write_xport(df, str(filepath), column_labels=column_labels)
    logger.info(f"Generated {filepath.name}: {len(df)} rows, {len(df.columns)} cols")
    return filepath


def generate_pharmacy_claims(output_dir: Path) -> Path:
    """Generate pharmacy_claims.xpt with 5 rows and labeled columns.

    Args:
        output_dir: Directory to write the file.

    Returns:
        Path to the generated file.
    """
    filepath = output_dir / "pharmacy_claims.xpt"
    df = pd.DataFrame({
        "member_id": ["M001", "M002", "M003", "M004", "M005"],
        "claim_id": ["RX0000000000001", "RX0000000000002", "RX0000000000003", "RX0000000000004", "RX0000000000005"],
        "amount": [25.00, 150.75, 12.50, 300.00, 45.99],
        "ndc_code": ["00000000001", "00000000002", "00000000003", "00000000004", "00000000005"],
        "fill_date": [20240105.0, 20240120.0, 20240205.0, 20240215.0, 20240305.0],
        "provider_id": ["PRV0000001", "PRV0000005", "PRV0000003", "PRV0000006", "PRV0000002"],
    })
    column_labels = {
        "member_id": "Member Identifier",
        "claim_id": "Claim Identifier",
        "amount": "Prescription Amount",
        "ndc_code": "National Drug Code",
        "fill_date": "Fill Date",
        "provider_id": "Provider Identifier",
    }
    pyreadstat.write_xport(df, str(filepath), column_labels=column_labels)
    logger.info(f"Generated {filepath.name}: {len(df)} rows, {len(df.columns)} cols")
    return filepath


def generate_split_lines(output_dir: Path) -> list[Path]:
    """Generate split_lines_00.xpt and split_lines_01.xpt with an identical shape.

    Stands in for a dataset split across numbered files: the two members carry the same
    columns, labels, and types and differ only in their rows, which is what lets the
    extractor inventory the whole dataset from whichever member it reads.

    Args:
        output_dir: Directory to write the files.

    Returns:
        Paths to the generated files, in name order.
    """
    column_labels = {
        "member_id": "Member Identifier",
        "claim_id": "Claim Identifier",
        "lineitem": "Line Item Number",
        "paid_amount": "Line Paid Amount",
    }
    rows_by_member = {
        "00": {
            "member_id": ["M001", "M002", "M003"],
            "claim_id": ["CL0000000000001", "CL0000000000002", "CL0000000000003"],
            "lineitem": [1.0, 2.0, 1.0],
            "paid_amount": [10.00, 20.50, 30.25],
        },
        "01": {
            "member_id": ["M101", "M102"],
            "claim_id": ["CL0000000000101", "CL0000000000102"],
            "lineitem": [1.0, 3.0],
            "paid_amount": [40.75, 50.00],
        },
    }

    filepaths = []
    for suffix, rows in rows_by_member.items():
        filepath = output_dir / f"split_lines_{suffix}.xpt"
        df = pd.DataFrame(rows)
        pyreadstat.write_xport(df, str(filepath), column_labels=column_labels)
        logger.info(f"Generated {filepath.name}: {len(df)} rows, {len(df.columns)} cols")
        filepaths.append(filepath)
    return filepaths


def generate_member_enrollment(output_dir: Path) -> Path:
    """Generate member_enrollment.xpt with 5 rows and labeled columns.

    Args:
        output_dir: Directory to write the file.

    Returns:
        Path to the generated file.
    """
    filepath = output_dir / "member_enrollment.xpt"
    df = pd.DataFrame({
        "member_id": ["M001", "M002", "M003", "M004", "M005"],
        "plan_code": ["HMO01", "PPO02", "HMO01", "EPO03", "PPO02"],
        "age": [45.0, 32.0, 58.0, 27.0, 71.0],
        "gender": ["F", "M", "F", "M", "F"],
        "enrollment_start": [20240101.0, 20240101.0, 20240101.0, 20240201.0, 20240101.0],
        "enrollment_end": [20241231.0, 20241231.0, 20240630.0, 20241231.0, 20241231.0],
    })
    column_labels = {
        "member_id": "Member Identifier",
        "plan_code": "Plan Code",
        "age": "Member Age",
        "gender": "Member Gender",
        "enrollment_start": "Enrollment Start Date",
        "enrollment_end": "Enrollment End Date",
    }
    pyreadstat.write_xport(df, str(filepath), column_labels=column_labels)
    logger.info(f"Generated {filepath.name}: {len(df)} rows, {len(df.columns)} cols")
    return filepath


def generate_empty_dataset(output_dir: Path) -> Path:
    """Generate empty_dataset.xpt with 0 rows and no labels.

    Args:
        output_dir: Directory to write the file.

    Returns:
        Path to the generated file.
    """
    filepath = output_dir / "empty_dataset.xpt"
    df = pd.DataFrame({"id": pd.Series(dtype="object"), "value": pd.Series(dtype="float64")})
    pyreadstat.write_xport(df, str(filepath))
    logger.info(f"Generated {filepath.name}: {len(df)} rows, {len(df.columns)} cols")
    return filepath


def generate_test_config(output_dir: Path) -> Path:
    """Generate test_config.toml pointing to the fixture .xpt files.

    Args:
        output_dir: Directory containing the fixture files.

    Returns:
        Path to the generated config file.
    """
    filepath = output_dir / "test_config.toml"
    # Use forward slashes for cross-platform TOML paths
    fixtures_dir = output_dir.as_posix()
    config_content = f'''# Test configuration for extract_sas_dataset_schemas.py

[settings]
process_name = "test_process"
overwrite    = true
origin_system = "warehouse"
dest_system = "edw"
origin_data_scope  = ["fixture_ocs.general"]
dest_data_scope  = ["fixture_edw"]

[datasets]
"RAW.MEDICAL_CLAIMS"    = {{ path = "{fixtures_dir}/medical_claims.xpt" }}
"RAW.PHARMACY_CLAIMS"   = {{ path = "{fixtures_dir}/pharmacy_claims.xpt", origin_data_scope = ["fixture_ocs.general.clm"] }}
"RAW.MEMBER_ENROLLMENT" = {{ path = "{fixtures_dir}/member_enrollment.xpt" }}
"RAW.EMPTY_DATASET"     = {{ path = "{fixtures_dir}/empty_dataset.xpt" }}
# A dataset split across numbered members of identical shape: the `*` marks both the
# LIBNAME.DATASET key and the path, so the canonical config exercises the split path
# parse_config and the extractor support, not only the single-file one.
"RAW.SPLIT_LINES_*"     = {{ path = "{fixtures_dir}/split_lines_*.xpt" }}
'''
    filepath.write_text(config_content, encoding="utf-8")
    logger.info(f"Generated {filepath.name}")
    return filepath


def main() -> None:
    """Generate all test fixture files."""
    parser = argparse.ArgumentParser(description="Generate synthetic .xpt test fixtures")
    parser.add_argument("--config", type=Path, required=True, help="Path to TOML configuration file")
    args = parser.parse_args()

    # A fixture regenerator re-run freely during development: a fresh log
    # each time is more useful here than the history of past runs.
    setup_logging(log_dir="logs/sas_parsing/unit_tests/fixtures", overwrite=True)
    logger.info("=" * 60)

    config_path = args.config
    if not config_path.exists():
        logger.error(f"Config file not found: {args.config}")
        sys.exit(1)

    try:
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
    except Exception as e:
        logger.error(f"Failed to read config file: {e}")
        sys.exit(1)

    try:
        output_dir = Path(config["output_dir"])
    except KeyError as e:
        logger.error(f"Missing required config field: {e}")
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Output directory: {output_dir}")

    try:
        generate_medical_claims(output_dir)
        generate_pharmacy_claims(output_dir)
        generate_split_lines(output_dir)
        generate_member_enrollment(output_dir)
        generate_empty_dataset(output_dir)
        generate_test_config(output_dir)
        logger.info("All fixtures generated successfully")
        logger.info("=" * 60)
    except Exception as e:
        logger.error(f"Failed to generate fixtures: {e}")
        logger.info("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    main()
