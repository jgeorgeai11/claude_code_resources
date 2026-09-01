---
name: exception-handling
description: Error handling patterns for Python. Use when writing or reviewing try/except blocks.
---

# exception-handling

## Guidelines

1. **Catch specific exceptions** — Never use bare `except:`
2. **Provide context** — Include variable values in error messages
3. **Chain exceptions** — Use `raise ... from e` to preserve stack trace
4. **Fail fast** — Don't silently swallow errors
5. **Log at every stage** — try, except, else, and finally should all include appropriate logging
6. **Preserve exception types when re-raising**
   - 6.1. Use `raise` alone to propagate the original exception unchanged
   - 6.2. Use `raise SpecificError(...) from e` to wrap in a domain-appropriate type
   - 6.3. Never wrap in generic `Exception` - it obscures the original exception type

## Examples

### Error Handling Pattern

```python
def load_data(filepath: Path) -> pd.DataFrame:
    """Load data from CSV with error handling."""
    logger.info(f"Loading data from: {filepath}")

    try:
        df = pd.read_csv(filepath)
        logger.debug(f"CSV parsed successfully: {len(df)} rows")
    except pd.errors.ParserError as e:
        logger.error(f"Failed to parse CSV: {filepath} - {e}")
        raise ValueError(f"Invalid CSV format in {filepath}") from e
    except FileNotFoundError as e:
        logger.error(f"File not found: {filepath}")
        raise
    else:
        logger.info(f"Loaded {len(df):,} records from {filepath}")
        return df
    finally:
        logger.debug(f"Finished load_data for {filepath}")
```

### Re-raising Exceptions

```python
# Re-raise unchanged (preserves original type)
except Exception as e:
    logger.error(f"Operation failed: {e}")
    raise

# Wrap in more specific type when appropriate
except KeyError as e:
    raise ValueError(f"Missing required config key: {e}") from e
```

## Anti-patterns

```python
# BAD: Bare except catches everything including Ctrl+C and sys.exit()
except:
    pass

# BAD: Wraps in generic Exception, obscures original type
except Exception as e:
    raise Exception(f"Operation failed: {e}") from e
```
