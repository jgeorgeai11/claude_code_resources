---
name: data-validation
description: Data quality checks for outputs and inputs. Use when writing or reviewing data validation code.
---

# data-validation

## Guidelines

1. **Script naming** — Data validation scripts should start with `data_val_` (e.g., `data_val_claims.py`)

## Reference

### File Organization

```
code/
└── module_name/
    ├── process_data.py              # Source code
    └── data_validation/
        └── data_val_process_data.py # Validation for process_data.py
```
