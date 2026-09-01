---
name: unit-tests
description: pytest patterns and conventions. Use when writing or reviewing unit tests.
---

# unit-tests

## Guidelines

1. **Use pytest** — Not unittest
2. **Naming conventions** — Name files and functions predictably
   - 2.1. **Files** — `test_<module_name>.py` (e.g., `test_process_data.py` for `process_data.py`)
   - 2.2. **Functions** — `test_<function>_<scenario>_<expected>`
3. **Structure tests clearly** — Keep each test focused on a single behavior
   - 3.1. **Use fixtures** — Share common test data via conftest.py
   - 3.2. **Arrange-Act-Assert** — Structure tests in three phases: setup, execute, verify
4. **Mock external boundaries only** — Use `mocker.patch()` for APIs, DBs, filesystem; patch where it's used, not where it's defined
5. **Use pytest utilities** — Prefer built-in pytest features over manual alternatives
   - 5.1. **Parametrize** — Use `@pytest.mark.parametrize` to run one test with multiple inputs
   - 5.2. **Test exceptions** — Use `pytest.raises(Exception, match="msg")` to verify expected errors
   - 5.3. **Compare DataFrames** — Use `pd.testing.assert_frame_equal()`, not `==`
6. **Test behavior independently** — Never depend on execution order or shared state between tests; don't assert on private attributes or internal state
7. **Ensure comprehensive coverage** — Every public function should have tests
   - 7.1. **Cover all paths** — Happy paths, edge cases, error conditions, and boundary values
   - 7.2. **Validate with pytest-cov** — Run `pytest --cov=module_name --cov-report=term-missing` after writing tests; investigate and address any uncovered lines

## Reference

### File Organization

```
code/
└── module_name/
    ├── process_data.py          # Source code
    └── unit_tests/
        ├── conftest.py          # Shared fixtures (auto-discovered by pytest)
        ├── test_process_data.py # Tests for process_data.py
        └── fixtures/            # Test data files (optional)
            └── sample_data.csv
```

### Fixture Scopes

| Scope | Lifecycle | Use For |
|-------|-----------|---------|
| function | Each test (default) | Most fixtures |
| class | Per test class | Grouped tests sharing state |
| module | Per test file | Expensive setup (DB connections) |
| session | Entire test run | Very expensive setup |

### Built-in Fixtures

| Fixture | Purpose |
|---------|---------|
| tmp_path | Temporary directory (Path) |
| capsys | Capture stdout/stderr |
| caplog | Capture log messages |
| monkeypatch | Modify objects/env vars |
