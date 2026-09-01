---
name: 20240220v01_consolidate_shared_db_helpers
goal: Replace the four drifted copies of the database connection helper with one shared implementation in `code/lib/`, so every entry point builds its connection the same way. The copies have diverged in URL construction, port validation, and error type; one builds its URL by string formatting and corrupts credentials containing reserved characters, so consolidation fixes a live defect rather than only removing duplication.
created: 2024-02-20 09:14:02
updated: 2024-02-20 09:14:02
---

## Implementation Plan

### Phase 1 — Build the shared helper

1. [pending] Create the shared connection helper - `code/lib/db.py`
   - 1.1. `get_engine(db_name: str) -> Engine` reading the four `POSTGRES_*` variables, building the URL with `sqlalchemy.engine.URL.create`, validating the port with `int()`, logging at ERROR and raising `ValueError` on a missing variable or non-integer port
   - 1.2. Adopt the behavior of `code/claims_analysis/_utils.py:60`, the copy that is already correct (rationale in decision 2)
   - 1.3. Standardize the error message; the four current copies carry three different texts

2. [pending] Create and run unit tests for the shared helper - `code/lib/unit_tests/test_db.py`
   - 2.1. Assert a password containing `@`, `/`, `?`, `#`, and `:` produces a URL that round-trips to the correct host, port, and database — the regression guard for the string-formatting defect
   - 2.2. Assert each missing `POSTGRES_*` variable raises `ValueError` naming the variable, and a non-integer port raises `ValueError` naming the port
   - 2.3. Assert no test logs a credential value
   - 2.4. Run with coverage; investigate any uncovered line

### Phase 2 — Adopt it everywhere

3. [pending] Repoint every call site to the shared helper - `code/claims_analysis/`, `code/provider_summary/`
   - 3.1. Delete the local implementations and import the shared one in: `code/claims_analysis/_utils.py:60`, `code/claims_analysis/data_validation/data_val_claims.py:44`, `code/provider_summary/summarize.py:71`, and `code/provider_summary/data_validation/data_val_summary.py:38` — the last being the string-formatted copy this replaces
   - 3.2. One copy raises `ConfigurationError` where the others raise `ValueError`; adopt `ValueError` and update its handler so the helper is swappable
   - 3.3. Run each affected module's suite

### Phase 3 — Verify

4. [pending] Run the full suite and the consistency checks
   - 4.1. `uv run pytest code` — all tests pass, from the repo root and from a non-root working directory
   - 4.2. Grep check, zero matches expected: any definition of `get_engine` outside `code/lib/db.py`
   - 4.3. Smoke-run one entry point per module against the real database, confirming exit 0 and an unchanged row count
   - 4.4. If issues found, debug and iterate

## Key Data Decisions and Considerations

1. This activity creates no new data tables or output files, so no per-output validation task is written. What is under test is behavioral: the suite passing from any working directory (task 4.1), the absence of a second `get_engine` definition (4.2), and an end-to-end smoke run whose row count is unchanged (4.3).
2. The shared helper adopts `code/claims_analysis/_utils.py:60` because it is the only copy that is already correct on all three axes: `URL.create` (which percent-encodes credentials), an `int()` port guard, and a logged `ValueError`. Each other copy drops at least one. Consolidation is therefore a bug fix, not only a tidy-up — it deletes the string-formatting defect rather than patching it in place.
3. Task 3 groups four code files into one task rather than splitting them per file. It is a single mechanical substitution — delete the local definition, import the shared one — repeated across near-identical files, so per-file tasks would obscure that it is one decision applied four times. Every affected file is named in subtask 3.1.
4. Phase 1 precedes Phase 2 so the shared helper is proven by its own tests before any call site depends on it; a defect introduced in task 1 would otherwise surface as four simultaneous failures with no obvious origin.
5. Line numbers in this plan were verified at drafting time and will drift as Phase 1 lands. Re-verify by grep before editing rather than by trust.
6. Data reachability: the database is reachable from where this is authored, so the smoke run in task 4.3 executes here rather than being documented for manual execution.
