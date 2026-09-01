---
name: best-practices
description: SQL style and conventions for writing clean, maintainable queries. Use when writing or reviewing SQL code.
---

# best-practices

## Guidelines

1. **Explicit column references** — No SELECT * on raw inputs; qualify with table/alias in joins; use descriptive aliases (no single-letter)
2. **Explicit joins** — Use `inner join` not `join`; prefer left joins; avoid right joins
3. **Explicit group/order by** — Use column names, not positional references
4. **Prefer `union all`** — Only use `union` when deduplication is explicitly needed
5. **Data handling** — Handle NULLs explicitly (COALESCE, IS NULL); filter early in CTEs; use explicit CAST/CONVERT
6. **Prefer CTEs over nested subqueries** — Use CTEs to break complex logic into named, readable steps; direct joins are fine for simpler queries
   - 6.1. When using CTEs, end the query with `select * from <last CTE>` rather than re-listing columns; column shaping belongs in the last CTE, not in the final SELECT
   - 6.2. Don't add a no-op CTE just to name it `final` — give the last CTE a descriptive name (e.g. `joined`, `aggregated`, `filtered`) and select from it directly
7. **Formatting** — Lowercase everything; 4-space indent; max 100 chars per line
   - 7.1. SELECT: one column per line
   - 7.2. JOIN/WHERE/GROUP BY/ORDER BY/PARTITION BY: inline when there's a single condition/column or the line fits in 100 chars; one-per-line otherwise
8. **Comments** — Explain business rules, edge cases, and why (not what); place on the line above, not inline; keep up to date when modifying code
9. **Query block annotation** — Every query and every CTE gets a one-line comment above it: what it does, plus `Level = <col>` (or `Level = <col_a> - <col_b>` for composite grain). Skip trivial `select *` blocks — import CTEs at the top, and a closing `select * from <last CTE>` at the bottom.

## Examples

### Simple query (≤2 tables) - Direct joins

```sql
-- Customer order totals for current fiscal year. Level = customer_id
select
    cust.customer_id,
    cust.customer_name,
    count(ord.order_id) as order_count,
    -- Use 0 for customers with no orders to avoid NULL in reports
    coalesce(sum(ord.amount), 0) as total_amount
from staging.customers as cust
-- Left join to include customers even if they have no orders
left join staging.orders as ord on cust.customer_id = ord.customer_id
-- Only include orders from current fiscal year, plus the no-order customers
where ord.order_date >= '2024-01-01' or ord.order_id is null
group by cust.customer_id, cust.customer_name
```

### Complex query (>2 tables) - Use CTEs

```sql
with

-- Pull customer dimension. Level = customer_id
customers as (
    select
        customer_id,
        customer_name
    from staging.customers
),

-- Restrict orders to current fiscal year. Level = order_id
orders as (
    select
        order_id,
        customer_id,
        amount,
        order_date
    from staging.orders
    where order_date >= '2024-01-01'
),

-- Order-line products. Level = order_id - product_id
products as (
    select
        product_id,
        order_id,
        product_name
    from staging.order_products
),

-- Join orders with customer and product attributes. Level = order_id - product_id
joined as (
    select
        customers.customer_id,
        customers.customer_name,
        orders.order_id,
        products.product_name,
        orders.amount
    from customers
    inner join orders on customers.customer_id = orders.customer_id
    inner join products on orders.order_id = products.order_id
)

select * from joined
```
