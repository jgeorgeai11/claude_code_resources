---
name: logging
description: Logging setup and usage using logconfig.py. Use when adding logging to scripts or library modules.
---

# logging

## Guidelines

1. **Use `logconfig`, never `print()`** — Proper log levels and formatting
2. **`setup_logging` once per run** — Whatever starts the run calls it; every other module calls only `get_logger(__name__)` and inherits the configuration
   - 2.1. **Being a library is not the test** — Being the thing that started is; one module can be both, imported by one script and run as another
   - 2.2. **A repeat call is a no-op** — An imported module cannot double-register the file handler
   - 2.3. **A foreign handler does not suppress it** — Logging still reaches the file under a test runner, or after something else called `logging.basicConfig`
3. **Condense messages** — Combine related info into single messages
4. **Include context** — Variable values in log messages
5. **funcName is auto-logged** — Don't add "Entering/Exiting" messages; the log format includes `funcName` automatically
6. **Avoid duplicate logging** — If a library module logs details, the entry script shouldn't repeat them
7. **Use f-strings in log calls** — Use `logger.info(f"Count: {n}")`, not lazy `%s` formatting like `logger.info("Count: %s", n)`
8. **Use separators in entry scripts** — Add `logger.info("=" * 60)` at start/end of main execution to mark run boundaries
9. **Let the log append** — The default appends, so a retried run keeps the failed run's records, each run separable by its `run_timestamp`
   - 9.1. **`overwrite=True` is for development** — A short script re-run freely, where a fresh file each time is the convenience
10. **Timestamps are UTC** — Both fields record the same instant, so records from different machines order against one another
   - 10.1. **`asctime` is ISO-8601 with milliseconds** — `2026-08-27T16:43:49.722Z`, which `datetime.fromisoformat` reads straight
   - 10.2. **`run_timestamp` is a filename-safe label** — For grouping one run's records, not for parsing

## Reference

### Installing logconfig

`logconfig` is **copied into your project's tracked source tree**, never imported from `.claude/` — which is gitignored in most projects, is overwritten in place when you re-sync the shared resources, and only resolves when you run from the repo root. The copy is a deliberate fork: your logging stays pinned, and picking up a change becomes a PR you can review against the resources repo's `CHANGELOG.md`.

It needs Python 3.11 or later (for `datetime.UTC`) and `python-json-logger>=3.1`, which is where `pythonjsonlogger.json` lives. Two homes, best first:

| Home | How to install | Where the dependency goes |
|------|----------------|---------------------------|
| An existing library package, one with its own `pyproject.toml` | Copy `logconfig.py` in beside its other modules and import `from lib.logconfig import setup_logging, get_logger`. No `sys.path` work — skip [Importing logconfig](#importing-logconfig) | That package's dependencies |
| The source root | Copy the whole `.claude/skills/python-development/scripts/logconfig/` package to e.g. `code/logconfig/` and commit it; see [Importing logconfig](#importing-logconfig) | Wherever the project records its own |

`lib` stands throughout this page for whatever that package is called.

### Importing logconfig

Source-root install only. Put the directory *holding* `logconfig/` on `sys.path`, resolved from the importing file, never the cwd.

| When | Form |
|------|------|
| The package sits at a fixed depth from the script — beside it, or a known parent in a tree that moves as a unit | `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))`, one `.parent` per level up |
| The depth varies — scripts at different levels, files that get moved | The upward search below; a count would only be right at the depth it was written |

```python
_FILE = Path(__file__).resolve()
# Fall back to this file's own directory so a package that isn't there at all
# fails as a plain ModuleNotFoundError rather than a bare StopIteration.
_ROOT = next((p for p in _FILE.parents if (p / "logconfig").is_dir()), _FILE.parent)
sys.path.insert(0, str(_ROOT))
```

### `setup_logging` Arguments

| Argument    | Type             | Default              | Description                                    |
|-------------|------------------|----------------------|------------------------------------------------|
| `log_dir`   | `str \| Path`    | *required*           | Directory for the log file                     |
| `log_name`  | `str \| None`    | caller script name   | Log filename (without `.jsonl` extension)      |
| `level`     | `int`            | `logging.DEBUG`      | Logging level                                  |
| `overwrite` | `bool`           | `False`              | Delete the log and its backups on each run; default appends |

Creates JSON log file at `{log_dir}/{log_name}.jsonl` with run timestamps.

### Log Retention

| Behaviour | Detail |
|-----------|--------|
| Rotation | At 10 MB, keeping 3 backups (`{log_name}.jsonl.1` and so on) — roughly 40 MB per script before the oldest records drop |
| A run spanning a rotation | Records split across two files; `run_timestamp` still gathers them |
| `logs/` | Gitignored and disposable, never an archive — copy out anything worth keeping, or write it as a real output |
| One writer per file | Rotation renames the file and `overwrite=True` deletes it; on Windows both raise while another process holds it open, so give overlapping runs distinct `log_name`s |

### Log Directory

`log_dir` is required. Unless instructed otherwise, use a path that mirrors the script's location:

| Script Location                   | Log Directory                        |
|-----------------------------------|--------------------------------------|
| `module_name/`                    | `logs/module_name/`                  |
| `module_name/unit_tests/`         | `logs/module_name/unit_tests/`       |
| `module_name/data_validation/`    | `logs/module_name/data_validation/`  |

### Log Levels

| Level    | When to Use                                          |
|----------|------------------------------------------------------|
| DEBUG    | Variable values, loop progress, intermediate results |
| INFO     | Key milestones, record counts, successful completions|
| WARNING  | Unexpected but recoverable situations                |
| ERROR    | Failures that don't stop execution                   |
| CRITICAL | Application cannot continue                          |

## Examples

### Starting a run

```python
import sys

from lib.logconfig import setup_logging, get_logger

logger = get_logger(__name__)

def main():
    setup_logging(log_dir="logs/module_name")
    logger.info("=" * 60)

    try:
        result = process_data("input.csv")
        logger.info(f"Success: {result['count']:,} records")
        logger.info("=" * 60)
    except Exception as e:
        logger.error(f"Failed: {e}")
        logger.info("=" * 60)
        sys.exit(1)
```

Copied to the source root instead of into a package? Only that import line changes — use one of the two forms in [Importing logconfig](#importing-logconfig) in its place.

### Logging from an imported module

No `setup_logging` here — it already ran — so a module imported by two different scripts logs into whichever run is underway:

```python
import pandas as pd

from lib.logconfig import get_logger

logger = get_logger(__name__)

def process_data(filepath: str) -> dict:
    """Process the data file."""
    df = pd.read_csv(filepath)
    logger.info(f"Read {len(df):,} rows, {len(df.columns)} cols")

    filtered = df.query("value >= 0")
    logger.debug(f"Filtered to {len(filtered):,} rows")

    return {"count": len(filtered), "data": filtered}
```
