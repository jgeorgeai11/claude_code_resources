---
name: usc
description: Answer questions about federal statutory law — United States Code sections, definitions, and enacted provisions (select titles only).
---

# usc

## Guidelines

1. **Use the appropriate MCP tools** — Data is in the `usc` schema of the `policy_db` database
2. **Discover tables first** — Use `list_tables` and `describe_table` to discover available tables and which have vector columns before querying
3. **Prioritize semantic search** — Use embedding-based search on tables with vector columns; fall back to SQL for tables without embeddings

## Workflow

1. **Research** — Query the USC database to answer the policy question
2. **Compose** — Write response per [standards](../core/standards.md)
