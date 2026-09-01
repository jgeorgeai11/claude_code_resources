---
name: pyspark
description: PySpark/Databricks style guide. Use when writing or reviewing PySpark code.
---

# pyspark

Based on [Palantir's PySpark Style Guide](https://github.com/palantir/pyspark-style-guide).

## Guidelines

1. **Prefer DataFrames over RDDs** — DataFrames use the Catalyst optimizer for automatic query optimization
2. **Chain transformations** — Spark uses lazy evaluation; avoid unnecessary intermediate operations
3. **Use actions sparingly** — Trigger `collect()`, `count()`, `show()` only when necessary
4. **Use `F.col('name')` for columns** — Not direct DataFrame access (`df.col`); limit logical operations to 3 expressions max
5. **Use `select()` for schema contracts** — At start/end of transforms; one function per column + optional `.alias()`
6. **Use `.alias()` over `withColumnRenamed()`** — Use `select()` for type casting, not chained `withColumn()`
7. **Empty columns use `F.lit(None)`** — Never `F.lit('')` or `F.lit('NA')`
8. **Avoid UDFs** — Use native PySpark functions; if unavoidable, use Pandas UDFs
9. **Joins**
   - 9.1. Always specify join type: `df.join(other, 'key', how='inner')`
   - 9.2. Avoid `right` joins — swap order and use `left`
   - 9.3. Use aliases for overlapping columns
   - 9.4. Verify key uniqueness on right side to avoid join explosions
   - 9.5. Don't use `.dropDuplicates()` to mask join issues
10. **Window functions**
    - 10.1. Always specify explicit frame to avoid unexpected behavior
    - 10.2. Use `partitionBy()` to minimize data processed per window
11. **Performance**
    - 11.1. Cache DataFrames accessed multiple times; `unpersist()` when done
    - 11.2. Use `coalesce(n)` to reduce partitions (no shuffle); `repartition(n)` to increase (causes shuffle)
    - 11.3. Use `broadcast()` for small lookup tables (< 10MB)
    - 11.4. Filter early to reduce data before joins/aggregations
    - 11.5. Avoid `collect()` on large datasets — brings all data to driver

## Reference

### Storage Levels

| Level | Description |
|-------|-------------|
| `MEMORY_ONLY` | Fast but may cause OOM |
| `MEMORY_AND_DISK` | Spills to disk if needed |

### Configuration

| Setting | Purpose |
|---------|---------|
| `spark.executor.memory` / `spark.executor.cores` | Size based on cluster resources |
| `spark.sql.shuffle.partitions` | Default 200; aim for 100-200MB per partition |

## Examples

### Imports

```python
from pyspark.sql import functions as F, types as T
```

### Column Selection

```python
is_active = F.col('status') == 'Active'
has_value = F.col('amount').isNotNull()
result = F.when(is_active & has_value, 'valid')
```

### Joins with Aliases

```python
flights = flights.alias('flights')
parking = parking.alias('parking')
flights.join(parking, 'code', how='left').select(
    F.col('flights.start_time').alias('flight_start'),
    F.col('parking.total_time')
)
```

### Window Functions

```python
w = Window.partitionBy('key').orderBy('num') \
    .rowsBetween(Window.unboundedPreceding, 0)
```
