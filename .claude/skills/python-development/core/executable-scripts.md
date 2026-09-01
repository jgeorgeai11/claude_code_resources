---
name: executable-scripts
description: Command-line scripts with TOML config. Use when creating entry point scripts.
---

# executable-scripts

## Guidelines

1. **Use a `main()` function** — with `if __name__ == "__main__": main()`
2. **Single `--config` argument** — `main()` uses argparse with a single `--config` argument for the TOML config path
3. **TOML configs in `config/`** — configs live in a `config/` subdirectory alongside the script (`{script_dir}/config/{name}.toml`)
4. **Defer logging setup** — after argparse so `--help` doesn't create log files

## Examples

### Script

```python
import sys
import argparse
import tomllib
from pathlib import Path

# logconfig/ is copied into the project's source root - see logging.md. Search up
# from this file, never the cwd, so the script runs from any working directory.
_FILE = Path(__file__).resolve()
_ROOT = next((p for p in _FILE.parents if (p / "logconfig").is_dir()), _FILE.parent)
sys.path.insert(0, str(_ROOT))
from logconfig import setup_logging, get_logger

logger = get_logger(__name__)


def do_work(input_file: str, output_dir: str) -> dict:
    """Main business logic."""
    # TODO: Implement
    return {"status": "complete"}


def main():
    # 1. Parse arguments
    parser = argparse.ArgumentParser(description='...')
    parser.add_argument('--config', type=str, required=True, help='Path to TOML configuration file')
    args = parser.parse_args()

    # 2. Setup logging - see logging.md (AFTER argparse so --help doesn't create log files)
    setup_logging(log_dir="logs/module_name")
    logger.info("=" * 60)

    # 3. Validate config file exists
    config_path = Path(args.config)
    if not config_path.exists():
        logger.error(f"Config file not found: {args.config}")
        sys.exit(1)

    # 4. Read TOML config
    try:
        with open(config_path, 'rb') as f:
            config = tomllib.load(f)
    except Exception as e:
        logger.error(f"Failed to read config file: {e}")
        sys.exit(1)

    # 5. Extract config fields
    try:
        input_file = config['input_file']
        output_dir = config.get('output_dir', 'data/output')
    except KeyError as e:
        logger.error(f"Missing required config field: {e}")
        sys.exit(1)

    # 6. Execute main logic
    try:
        result = do_work(input_file, output_dir)
        logger.info(f"SUCCESS: {result}")
        logger.info("=" * 60)
    except Exception as e:
        logger.error(f"Failed: {e}")
        logger.info("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

### TOML Config

```toml
# Configuration for my_script.py
#
# Usage: uv run code/module_name/my_script.py --config code/module_name/config/my_script.toml

# Required fields
input_file = "data/input.csv"

# Optional fields (defaults shown in comments)
output_dir = "data/output"  # default: "data/output"
```

### Usage

```bash
uv run code/module_name/my_script.py --config code/module_name/config/my_script.toml
```
