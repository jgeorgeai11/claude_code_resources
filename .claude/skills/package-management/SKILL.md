---
name: package-management
description: Manages Python packages using uv. Use when installing packages, running scripts, or checking dependencies.
---

# package-management

## Guidelines

1. **Use `uv` exclusively** — Never use `pip`, `python`, or `conda`
2. **Use `uv add` for packages** — Always use `uv add` instead of `uv pip install` to ensure dependencies are tracked in `pyproject.toml`
3. **Approved packages only** — See [approved_packages.txt](references/approved_packages.txt); ask user permission for others

## Examples

```bash
# Run a script
uv run script.py

# Add a package (must be approved)
uv add pandas
```
