---
name: comments
description: Code commenting patterns. Use when writing or reviewing comments in Python code.
---

# comments

## Guidelines

1. **Comment the "why", not the "what"** — Code shows what; comments explain why
2. **Use comment chains for DataFrame operations** — Explain each transformation step
3. **Keep comments current** — Outdated comments are worse than none

## Examples

```python
result = (
    df
    # Filter to completed transactions only
    .query("status == 'completed'")
    # Calculate revenue including 8% tax
    .assign(total_revenue=lambda x: x['quantity'] * x['price'] * 1.08)
    # Aggregate to daily level for trend analysis
    .groupby(pd.Grouper(key='date', freq='D'))
    .agg(orders=('order_id', 'nunique'), revenue=('total_revenue', 'sum'))
)
```
