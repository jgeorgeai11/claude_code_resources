---
name: federal-register
description: Answer questions about recent CMS rulemaking, proposed rules, final rules, Requests for Information (RFIs), comment periods, or Federal Register citations using the Federal Register API.
---

# federal-register

## Guidelines

1. **Use WebFetch** — The Federal Register API is public, no auth required
2. **Use the `fields` parameter** — Always specify only the fields you need; this reduces response size and prevents WebFetch from summarizing away important details
3. **Search documents** — Use `/documents.json` with `conditions[term]`, `conditions[agencies][]`, and `fields[]` to find relevant documents
4. **Fetch full details** — Use `/documents/{document_number}.json` with specific `fields[]` to retrieve document details

## Reference

### API Endpoints

Base URL: `https://www.federalregister.gov/api/v1`

| Category | Endpoint | URL | Format | Purpose |
|----------|----------|-----|--------|---------|
| Documents | Search | `/documents.{format}` | json, csv | Search all Federal Register documents published since 1994 |
| Documents | Get Document(s) | `/documents/{document_number(s)}.{format}` | json, csv | Retrieve one or more documents by number (comma-separated) |
| Documents | Issue TOC | `/issues/{publication_date}.{format}` | json, csv | Table of contents for a print edition issue |
| Agencies | List All | `/agencies` | json | List all agencies with details |

### Search

| Parameter | Required | Description |
|-----------|----------|-------------|
| `conditions[term]` | No (query) | Keyword search (e.g., `Medicare+provider+enrollment`) |
| `conditions[agencies][]` | No (query) | Agency slug filter (e.g., `centers-for-medicare-medicaid-services`) |
| `conditions[type][]` | No (query) | `RULE` (final/interim/corrections), `PRORULE` (proposed), `NOTICE`, `PRESDOCU` (executive orders) |
| `conditions[publication_date][gte]` | No (query) | Published on or after date (e.g., `2025-01-01`) |
| `conditions[publication_date][lte]` | No (query) | Published on or before date |
| `conditions[effective_date][gte]` | No (query) | Effective on or after date |
| `conditions[effective_date][lte]` | No (query) | Effective on or before date |
| `conditions[cfr][title]` | No (query) | CFR title number (e.g., `42`) |
| `conditions[cfr][part]` | No (query) | CFR part number (e.g., `424`) |
| `conditions[docket_id]` | No (query) | Regulatory docket ID (e.g., `CMS-1808-F`) |
| `fields[]` | No (query) | Specify returned fields (repeatable) — see Document Fields |
| `per_page` | No (query) | Results per page (default 20, max 1000) |
| `page` | No (query) | Page number |
| `order` | No (query) | Sort order: `relevance`, `newest`, `oldest`, `executive_order_number` |

### Get Document(s)

| Parameter | Required | Description |
|-----------|----------|-------------|
| `document_number(s)` | Yes (path) | One or more document numbers, comma-separated (e.g., `2025-21767` or `2025-21767,2025-06008`) |
| `fields[]` | No (query) | Specify returned fields (repeatable) — see Document Fields |

### Issue TOC

| Parameter | Required | Description |
|-----------|----------|-------------|
| `publication_date` | Yes (path) | Date of the print edition (e.g., `2025-12-02`) |

### Document Fields

Use with `fields[]=field_name` to control what's returned from document endpoints.

| Field | Description |
|-------|-------------|
| `document_number` | Federal Register document number |
| `title` | Document title |
| `type` | Document type (Rule, Proposed Rule, Notice, Presidential Document) |
| `abstract` | Summary of the document |
| `action` | Regulatory action (e.g., "Final rule", "Proposed rule") |
| `citation` | Federal Register citation (e.g., "90 FR 12345") |
| `publication_date` | Date published in the Federal Register |
| `effective_on` | Effective date of the rule |
| `comments_close_on` | Comment period deadline |
| `html_url` | Link to the full document on federalregister.gov |
| `pdf_url` | Link to the PDF version |
| `full_text_xml_url` | Link to the XML version |
| `raw_text_url` | Link to the plain text version |
| `cfr_references` | CFR parts affected |
| `agencies` | Issuing agencies |
| `docket_ids` | Associated docket IDs |
| `dates` | Key dates described in the document |
| `correction_of` | Document this corrects (if applicable) |
| `body_html_url` | URL to the full HTML body content |
| `start_page` / `end_page` | Print edition page range |

## Workflow

1. **Research** — Query the Federal Register API to answer the policy question
2. **Compose** — Write response per [standards](../core/standards.md)
