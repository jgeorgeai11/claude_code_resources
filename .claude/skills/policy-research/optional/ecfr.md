---
name: ecfr
description: Answer questions about federal regulations, CMS regulatory requirements, coverage rules, payment regulations, provider standards, or specific CFR citations using the eCFR API.
---

# ecfr

## Guidelines

1. **Use WebFetch** — The eCFR API is public, no auth required
2. **Scope full-text requests narrowly** — Always include `part` and `section` in the URL; broad requests get summarized instead of returned verbatim
3. **Search by keyword** — Use `/search/v1/results` with `query` and `agency_slugs[]` to find relevant sections
4. **Fetch full text** — Use `/versioner/v1/full/{date}/title-{title}.xml` with `part` and `section` to retrieve the regulation text

## Reference

### API Endpoints

Base URL: `https://www.ecfr.gov/api`

| Category | Endpoint | URL | Purpose |
|----------|----------|-----|---------|
| Primary | Search | `/search/v1/results` | Full-text keyword search across all CFR content |
| Primary | Full Text | `/versioner/v1/full/{date}/title-{title}.xml` | Retrieve actual regulation text (XML) |
| Discovery | List Titles | `/versioner/v1/titles.json` | Summary info for each title (name, dates, status) |
| Discovery | Ancestry | `/versioner/v1/ancestry/{date}/title-{title}.json` | Get ancestors from a node up through the title |
| Discovery | List Agencies | `/admin/v1/agencies.json` | List all agencies with their CFR title/chapter references |
| History | Content Versions | `/versioner/v1/versions/title-{title}.json` | List when sections were added, amended, or removed |
| History | Corrections | `/admin/v1/corrections.json` | List historical corrections to published regulations |

### Search

| Parameter | Required | Description |
|-----------|----------|-------------|
| `query` | Yes (query) | Keyword or phrase; searches headings and full text (e.g., `provider+enrollment`) |
| `agency_slugs[]` | No (query) | Limit to content associated with these agencies (e.g., `centers-for-medicare-medicaid-services`) |
| `date` | No (query) | Limit to content present on this date (e.g., `2025-01-01`) |
| `last_modified_after` | No (query) | Content last modified after this date (e.g., `2025-01-01`) |
| `last_modified_on_or_after` | No (query) | Content last modified on or after this date (e.g., `2025-01-01`) |
| `last_modified_before` | No (query) | Content last modified before this date (e.g., `2025-06-01`) |
| `last_modified_on_or_before` | No (query) | Content last modified on or before this date (e.g., `2025-06-01`) |
| `per_page` | No (query) | Results per page (e.g., `20`; max 1,000) |
| `page` | No (query) | Page number (e.g., `2`; can't paginate beyond 10,000 total results) |
| `order` | No (query) | Order of results (e.g., `relevance`) |
| `paginate_by` | No (query) | Pagination grouping (e.g., `date`); requires a `last_modified_*` filter |

### Full Text

| Parameter | Required | Description |
|-----------|----------|-------------|
| `date` | Yes (path) | Point-in-time date — use `up_to_date_as_of` from `/versioner/v1/titles.json` (404s if exceeded) |
| `title` | Yes (path) | CFR title number (e.g., `42`) |
| `subtitle` | No (query) | Uppercase letter (e.g., `A`) |
| `chapter` | No (query) | Roman numerals or digits (e.g., `IV`) |
| `subchapter` | No (query) | Requires `chapter` (e.g., `C`) |
| `part` | No (query) | Part number (e.g., `435`) |
| `subpart` | No (query) | Requires `part` (e.g., `A`) |
| `section` | No (query) | Requires `part` (e.g., `435.1010`) |
| `appendix` | No (query) | Requires `subtitle`, `chapter`, or `part` (e.g., `App. A`) |

### Content Versions

| Parameter | Required | Description |
|-----------|----------|-------------|
| `title` | Yes (path) | CFR title number (e.g., `42`) |
| `issue_date[on]` | No (query) | Content added on this date (e.g., `2025-06-15`) |
| `issue_date[lte]` | No (query) | Content added on or before this date (e.g., `2025-12-31`) |
| `issue_date[gte]` | No (query) | Content added on or after this date (e.g., `2025-01-01`) |
| `subtitle` | No (query) | Uppercase letter (e.g., `A`) |
| `chapter` | No (query) | Roman numerals or digits (e.g., `IV`) |
| `subchapter` | No (query) | Requires `chapter` (e.g., `C`) |
| `part` | No (query) | Part number (e.g., `435`) |
| `subpart` | No (query) | Requires `part` (e.g., `A`) |
| `section` | No (query) | Requires `part` (e.g., `435.1010`) |
| `appendix` | No (query) | Requires `subtitle`, `chapter`, or `part` (e.g., `App. A`) |

### Ancestry

| Parameter | Required | Description |
|-----------|----------|-------------|
| `date` | Yes (path) | Point-in-time date (e.g., `2025-12-02`) |
| `title` | Yes (path) | CFR title number (e.g., `42`) |
| `subtitle` | No (query) | Uppercase letter (e.g., `A`) |
| `chapter` | No (query) | Roman numerals or digits (e.g., `IV`) |
| `subchapter` | No (query) | Requires `chapter` (e.g., `C`) |
| `part` | No (query) | Part number (e.g., `435`) |
| `subpart` | No (query) | Requires `part` (e.g., `A`) |
| `section` | No (query) | Requires `part` (e.g., `435.1010`) |
| `appendix` | No (query) | Requires `subtitle`, `chapter`, or `part` (e.g., `App. A`) |

### Corrections

| Parameter | Required | Description |
|-----------|----------|-------------|
| `title` | No (query) | Filter by CFR title number (e.g., `42`) |
| `date` | No (query) | Snapshot date (e.g., `2025-01-01`) |
| `error_corrected_date` | No (query) | Filter by correction date (e.g., `2025-03-01`) |

### Common CFR Titles

| Title | Name | Relevance |
|-------|------|-----------|
| 42 | Public Health | Medicare, Medicaid, CMS regulations |
| 45 | Public Welfare | HHS regulations, HIPAA |
| 20 | Employees' Benefits | Social Security, SSI |
| 26 | Internal Revenue | Tax-related healthcare provisions |
| 29 | Labor | ERISA, employee benefits |

## Workflow

1. **Research** — Query the eCFR API to answer the policy question
2. **Compose** — Write response per [standards](../core/standards.md)
