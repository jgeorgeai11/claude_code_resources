---
name: docstrings
description: Google-style documentation for Python functions. Use when writing or reviewing docstrings.
---

# docstrings

## Guidelines

1. **Use Google style** — Args, Returns, Raises sections
2. **All public functions need docstrings** — Brief description + sections as needed
3. **Document the "why"** — Not just what the code does
4. **Keep docstrings current** — Update docstrings when changing a function's behavior, parameters, return values, or exceptions

## Examples

```python
def process_data(
    df: pd.DataFrame,
    columns: list[str],
    threshold: float = 0.5
) -> pd.DataFrame:
    """Process DataFrame by filtering columns and applying threshold.

    Args:
        df: Input DataFrame to process.
        columns: List of column names to retain.
        threshold: Minimum value threshold. Defaults to 0.5.

    Returns:
        Processed DataFrame with filtered columns and rows.

    Raises:
        ValueError: If any column in `columns` is not in the DataFrame.
    """
```
