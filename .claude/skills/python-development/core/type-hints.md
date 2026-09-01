---
name: type-hints
description: Type annotations for Python functions. Use when writing or reviewing type hints.
---

# type-hints

## Guidelines

1. **All functions require type hints** — Parameters and return types
2. **Use modern syntax** — `list[str]` not `List[str]`, `str | None` not `Optional[str]`
3. **Be specific** — `pd.DataFrame` not `Any`
4. **Keep type hints current** — Update annotations when changing a function's parameters, return values, or types

## Examples

```python
from pathlib import Path
import pandas as pd

def load_data(filepath: Path, columns: list[str] | None = None) -> pd.DataFrame:
    """Load data from file."""
    ...

def validate_data(df: pd.DataFrame, rules: list[str]) -> tuple[bool, list[str]]:
    """Validate data against rules. Returns (is_valid, errors)."""
    ...
```
