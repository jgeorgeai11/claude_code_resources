---
name: ibis
description: Ibis style guide for portable data operations between the local DuckDB backend and a target backend (Snowflake, Spark, or Polars). Load when writing Python code that must run across both without code changes.
---

# ibis

## Guidelines

1. **Write portable Ibis expressions** — use the Ibis API to express all data operations so code runs unchanged across backends (filters, joins, aggregations, window functions):
   - 1.1. **Identify the target backend** — local is always DuckDB; the target is one of Snowflake, Spark, or Polars; use the target backend column in the Portable Ibis patterns table to identify patterns that need attention
   - 1.2. **Write backend-agnostic patterns** — expression and execution code must not change across backends; do not use raw SQL strings or backend-specific Ibis operations
   - 1.3. **Consult the Portable Ibis patterns table** — use the portable pattern for the operation; if the target backend column shows an Ibis alternative, use that instead; consult the Ibis documentation for patterns not in the table
   - 1.4. **Fall back to non-Ibis backend-specific code as a last resort** — only if no portable or Ibis backend-specific pattern exists; use `con.name` to branch
   - 1.5. **Connection and data loading differ per backend** — backends with a catalog (DuckDB, Snowflake) access tables by name directly; backends without a catalog (Polars, Spark reading files) require data to be pre-loaded before connecting
2. **Call `.execute()` to materialize** — Ibis is lazy; expressions are compiled and executed against the backend only on `.execute()`
3. **Check the target dialect without the target** — `ibis.to_sql(expr, dialect="snowflake")` renders the SQL the target would run, with no connection and no data; an operation Ibis cannot translate fails here rather than after transfer

## Reference

### Ibis documentation

- [Ibis documentation](https://ibis-project.org)

### Backend connections

| Environment | Backend | Connection |
|---|---|---|
| Local | DuckDB | `ibis.duckdb.connect()` in memory, or `ibis.duckdb.connect("local.ddb")` for a file |
| Target (Snowflake) | Snowflake | `ibis.snowflake.connect(account=..., database=..., user=..., password=...)` |
| Target (Spark) | PySpark | `ibis.pyspark.connect(spark_session)` |
| Target (Polars) | Polars | `ibis.polars.connect({"table_name": df, ...})` |


### Portable Ibis patterns

Local is always DuckDB; target backend columns show alternatives when the portable pattern does not work — `—` means the portable pattern works as-is. Covers common patterns only; consult the [Ibis documentation](https://ibis-project.org) for the full API.

> These cells were authored against a Postgres local backend and have not yet been revalidated for DuckDB; treat a `—` as unconfirmed rather than proven until it has been.

| Pattern | Portable Ibis pattern | Snowflake | Spark | Polars |
|---|---|---|---|---|
| Filter rows | `.filter(...)` | — | — | — |
| Select columns | `.select(...)` | — | — | — |
| Join tables | `.join(...)` | — | — | — |
| Group and aggregate | `.group_by(...).aggregate(...)` | — | — | — |
| Order results | `.order_by(...)` | — | — | — |
| Limit rows | `.limit(...)` | — | — | — |
| Window functions | `.mutate(col=expr.over(...))` | — | — | — |
| Column expressions | `.mutate(...)` | — | — | — |
| Case/when expressions | `ibis.case().when(...).else_(...).end()` | — | — | — |
| Type casting | `.cast("type")` | — | — | — |
| Null handling | `.fillna()` / `.coalesce()` | — | — | — |
| Union | `.union()` | — | — | — |
| Distinct | `.distinct()` | — | — | — |
| Conditional aggregation | `.sum(where=...)` | — | — | — |
| Deduplication by group | `row_number().over(...).filter(rn == 0)` | — | — | — |
| Nth value in window | `row_number().over(...).filter(rn == N)` | — | — | — |
| Percentile | `.quantile()` | — | Specify `interpolation=` explicitly; default may differ from the local backend | — |
| String aggregation | `.group_concat()` | — | Use non-Ibis backend-specific code | — |
| NULL sort order | `.order_by(ibis.asc("col", nulls_first=False))` | — | — | — |
| String matching | `.like()` / `.contains()` | — | — | — |
| String formatting | `.upper()` / `.lower()` / `.strip()` | — | — | — |
| String concatenation | `col1.concat(col2)` | — | — | — |
| Date component extraction | `.year()` / `.month()` / `.day()` | — | — | — |
| Date truncation | `.truncate("M")` / `.truncate("Y")` | — | — | — |
| Date arithmetic | `ibis.interval(days=N)` | — | — | — |

## Examples

### Connecting

```python
import ibis

# Local (DuckDB)
con = ibis.duckdb.connect()  # in-memory; pass a path for a persistent file

# Target — Snowflake
con = ibis.snowflake.connect(account="account-id", database="mydb", user="user", password="pwd")

# Target — Spark
from pyspark.sql import SparkSession
spark = SparkSession.builder.getOrCreate()
con = ibis.pyspark.connect(spark)

# Target — Polars
import polars as pl
con = ibis.polars.connect({
    "claims": pl.read_parquet("claims.parquet"),
    "members": pl.read_parquet("members.parquet"),
})
```

### Backend-specific code

Use when the Portable Ibis patterns table shows no portable option for the target backend:

```python
if con.name == "pyspark":
    # Spark-specific: string aggregation
    from pyspark.sql import functions as f
    result = spark_df.groupBy("group_col").agg(
        f.array_join(f.collect_list("string_col"), ",").alias("agg_strings")
    )
else:
    # Portable Ibis pattern
    result = (
        t.group_by("group_col")
         .aggregate(agg_strings=t.string_col.group_concat(sep=","))
         .execute()
    )
```

