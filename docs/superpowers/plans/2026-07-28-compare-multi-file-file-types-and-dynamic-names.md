# Compare Multi-File File Types and Dynamic Names Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand Compare tab multi-file matching so local file sets support the same parsed file formats as Run/File vs Report and support dynamic source/target filename patterns with numeric, alpha, alpha-numeric, and custom regex tokens.

**Architecture:** Keep the existing ad-hoc multi-file run path: Compare tab posts a `file_mapping`, backend creates a real `TestRun`, `CompareService.run_multi_file_compare()` persists one aggregate `TestResult`, and Reports consume the same result fields. Implement dynamic filename matching in the shared `etl_framework.reconciliation.file_mapping` compiler so saved jobs and Compare tab both benefit. Update only UI copy/placeholders for format support; do not add S3/SFTP ad-hoc run support or browser multi-upload storage.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic, pandas, Alpine.js, Playwright, pytest.

## Global Constraints

- Preserve existing pattern syntax: `{region}`, `{date:%Y%m%d}`, `*`, and `?` must remain compatible.
- Supported local file formats are those already parsed by `api/services/file_source.py`: `.csv`, `.xlsx`, `.xls`, `.json`, `.xml`, `.tsv`, `.txt` where delimiter sniffing can parse the content.
- Keep ad-hoc Compare tab multi-file sources local-only; do not add S3/SFTP selectors.
- Do not add a database migration or new result schema.
- Do not commit changes unless the user explicitly requests a commit.

---

## File Structure

- Modify `etl_framework/reconciliation/file_mapping.py`: expand `_spec_to_regex()` and add small helpers for custom regex validation.
- Modify `tests/unit/test_compare_utils.py`: add unit tests for the filename-pattern compiler.
- Modify `tests/unit/test_compare_service_multi_file.py`: add a service-level regression test proving non-CSV files and different source/target filename shapes run successfully.
- Modify `frontend/partials/tab-compare.html`: update Multi-File source/target labels, placeholders, and pattern help copy.
- Modify `tests/e2e/08g-compare-multi-file.spec.ts`: add UI coverage for non-CSV/different filename pattern preview and run.
- Add test fixtures under `tests/e2e/fixtures/data/multi_source_dynamic/` and `tests/e2e/fixtures/data/multi_target_dynamic/` if the directory exists and fixtures are not already present.

---

### Task 1: Dynamic Filename Token Compiler

**Files:**
- Modify: `etl_framework/reconciliation/file_mapping.py`
- Test: `tests/unit/test_compare_utils.py`

**Interfaces:**
- Consumes: existing `compile_token_pattern(pattern: str) -> re.Pattern[str]`.
- Produces: expanded `compile_token_pattern()` behavior for `num`, `alpha`, `alnum`, `any`, and `regex(...)` specs.

- [ ] **Step 1: Write the failing tests**

Add these tests to `tests/unit/test_compare_utils.py` near the existing compare/file-mapping tests. If the file does not import `pytest`, add `import pytest`.

```python
import pytest

from etl_framework.reconciliation.file_mapping import compile_token_pattern


def test_compile_token_pattern_supports_builtin_dynamic_specs() -> None:
    pattern = compile_token_pattern("sales_{region:alpha}_{batch:num}_{code:alnum}.{ext:alpha}")

    match = pattern.match("sales_WEST_001_A9.xlsx")

    assert match is not None
    assert match.groupdict() == {
        "region": "WEST",
        "batch": "001",
        "code": "A9",
        "ext": "xlsx",
    }
    assert pattern.match("sales_WEST_A01_A9.xlsx") is None
    assert pattern.match("sales_123_001_A9.xlsx") is None


def test_compile_token_pattern_supports_custom_regex_specs() -> None:
    pattern = compile_token_pattern(r"prod_{id:regex([A-Z]{2}\d{4})}.json")

    match = pattern.match("prod_AB1234.json")


    assert match is not None
    assert match.groupdict() == {"id": "AB1234"}
    assert pattern.match("prod_ABC123.json") is None


def test_compile_token_pattern_keeps_existing_date_and_glob_behavior() -> None:
    pattern = compile_token_pattern("sales_*_{date:%Y%m%d}.csv")

    match = pattern.match("sales_any_region_20260728.csv")


    assert match is not None
    assert match.groupdict() == {"date": "20260728"}
    assert pattern.match("sales_any_region_2026-07-28.csv") is None


def test_compile_token_pattern_rejects_invalid_custom_regex() -> None:
    with pytest.raises(ValueError, match="file pattern token 'id' has invalid regex"):
        compile_token_pattern(r"prod_{id:regex([A-Z]+}.json")


def test_compile_token_pattern_rejects_empty_custom_regex() -> None:
    with pytest.raises(ValueError, match="file pattern token 'id' has empty regex"):
        compile_token_pattern("prod_{id:regex()}.json")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_compare_utils.py -k "compile_token_pattern" -v`

Expected: FAIL because `num`, `alpha`, `alnum`, and `regex(...)` are currently treated as literal spec text.

- [ ] **Step 3: Implement the token compiler expansion**

In `etl_framework/reconciliation/file_mapping.py`, replace `_spec_to_regex()` with this implementation and add `import re` is already present at the top.

```python
_TOKEN_SPEC_REGEX_PREFIX = "regex("


def _custom_spec_to_regex(token_name: str, spec: str) -> str:
    if not spec.startswith(_TOKEN_SPEC_REGEX_PREFIX) or not spec.endswith(")"):
        return ""
    inner = spec[len(_TOKEN_SPEC_REGEX_PREFIX):-1]
    if not inner:
        raise ValueError(f"file pattern token '{token_name}' has empty regex")
    try:
        re.compile(inner)
    except re.error as exc:
        raise ValueError(
            f"file pattern token '{token_name}' has invalid regex: {exc}"
        ) from exc
    if "(?P<" in inner:
        raise ValueError(
            f"file pattern token '{token_name}' custom regex must not define named groups"
        )
    return inner


def _spec_to_regex(spec: str | None, token_name: str) -> str:
    if not spec:
        return r"[^_./\\]+"
    builtins = {
        "num": r"\d+",
        "number": r"\d+",
        "alpha": r"[A-Za-z]+",
        "alnum": r"[A-Za-z0-9]+",
        "any": r"[^/\\]+",
    }
    if spec in builtins:
        return builtins[spec]
    custom = _custom_spec_to_regex(token_name, spec)
    if custom:
        return custom
    out: list[str] = []
    i = 0
    while i < len(spec):
        two = spec[i:i + 2]
        if two in _STRFTIME_DIGIT_WIDTH:
            out.append(r"\d{%d}" % _STRFTIME_DIGIT_WIDTH[two])
            i += 2
        else:
            out.append(re.escape(spec[i]))
            i += 1
    return "".join(out)
```

Then update `compile_token_pattern()` so the token name is passed into `_spec_to_regex()`:

```python
regex_parts.append(f"(?P<{name}>{_spec_to_regex(spec, name)})")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_compare_utils.py -k "compile_token_pattern" -v`

Expected: PASS.

- [ ] **Step 5: Run existing multi-file request tests**

Run: `python -m pytest tests/unit/test_multi_file_compare_request.py tests/unit/test_compare_utils.py -v`

Expected: PASS.

---

### Task 2: Non-CSV Multi-File Service Regression

**Files:**
- Modify: `tests/unit/test_compare_service_multi_file.py`

**Interfaces:**
- Consumes: `MultiFileCompareRequest(file_mapping=...)` and `CompareService.run_multi_file_compare(req, run_id)`.
- Produces: regression coverage proving dynamic patterns and non-CSV files run through the current service.

- [ ] **Step 1: Write the failing test**

Add this test to `tests/unit/test_compare_service_multi_file.py`. Also add `from pathlib import Path` at the top if missing.

```python
from pathlib import Path


def test_run_multi_file_compare_supports_xlsx_with_different_dynamic_names(tmp_path: Path, monkeypatch) -> None:
    import pandas as pd
    from api import services
    from api.services import file_source
    from api.services.compare_service import CompareService

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()

    pd.DataFrame({"id": [1, 2], "amount": [10, 20]}).to_excel(
        source_dir / "sales_WEST_001.xlsx", index=False
    )
    pd.DataFrame({"id": [1, 2], "amount": [10, 20]}).to_excel(
        target_dir / "financials-WEST-B001.xlsx", index=False
    )

    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))
    monkeypatch.setattr(services.compare_service, "_UPLOAD_BASES", (tmp_path.resolve(),))

    db = _make_db()
    try:
        run_id = "test-run-mf-compare-dynamic-xlsx"
        RunRepository(db).create_run(
            run_id=run_id,
            source_env="Source A",
            target_env="Source B",
            run_type="multi_file",
        )
        req = MultiFileCompareRequest(file_mapping={
            "match_on": ["region", "batch"],
            "source": {
                "kind": "local",
                "root": str(source_dir),
                "pattern": "sales_{region:alpha}_{batch:num}.xlsx",
            },
            "target": {
                "kind": "local",
                "root": str(target_dir),
                "pattern": "financials-{region:alpha}-B{batch:num}.xlsx",
            },
        }, key_columns=["id"])

        CompareService(db, ConfigRepository(db)).run_multi_file_compare(req, run_id)

        run = RunRepository(db).get_run(run_id)
        assert run.status == "PASSED"
        assert len(run.results) == 1
        result = run.results[0]
        assert result.status == TestStatus.PASSED
        assert result.mismatch_summary["pairs_total"] == 1
        assert result.mismatch_summary["file_pairs"][0]["source_files"] == ["sales_WEST_001.xlsx"]
        assert result.mismatch_summary["file_pairs"][0]["target_files"] == ["financials-WEST-B001.xlsx"]
    finally:
        db.close()
```

- [ ] **Step 2: Run test to verify it fails before Task 1 implementation**

Run: `python -m pytest tests/unit/test_compare_service_multi_file.py::test_run_multi_file_compare_supports_xlsx_with_different_dynamic_names -v`

Expected before Task 1 implementation: FAIL because the patterns do not match files. Expected after Task 1 implementation: PASS.

- [ ] **Step 3: Run service-level multi-file tests**

Run: `python -m pytest tests/unit/test_compare_service_multi_file.py tests/unit/test_multi_file_compare_request.py -v`

Expected: PASS.

---

### Task 3: Compare Tab Multi-File UI Copy and Pattern Help

**Files:**
- Modify: `frontend/partials/tab-compare.html`

**Interfaces:**
- Consumes: existing Alpine state names `mfCompareSourceRoot`, `mfCompareSourcePattern`, `mfCompareTargetRoot`, `mfCompareTargetPattern`, `mfCompareMatchOnRaw`.
- Produces: clearer UI copy and placeholders without changing submitted payload shape.

- [ ] **Step 1: Update Multi-File section labels and help**

In `frontend/partials/tab-compare.html`, replace the Multi-File source/target block around the existing `Source (local only)` and `Target (local only)` labels with this markup. Keep the existing `data-testid` values unchanged.

```html
    <div class="border-t border-slate-200 pt-3">
      <p class="text-xs font-medium text-slate-500 mb-1">Source server file set</p>
      <p class="text-xs text-slate-400 mb-2">
        Root is a server-accessible directory. Supported parsed formats: CSV, Excel, JSON, XML, TSV, and delimited text.
      </p>
      <div class="grid-2">
        <input x-model="mfCompareSourceRoot" class="field-input" placeholder="/spool/exports"
               data-testid="compare-mf-source-root-input" />
        <input x-model="mfCompareSourcePattern" class="field-input" placeholder="sales_{region:alpha}_{batch:num}.xlsx"
               data-testid="compare-mf-source-pattern-input" />
      </div>
    </div>
    <div class="border-t border-slate-200 pt-3">
      <p class="text-xs font-medium text-slate-500 mb-1">Target server file set</p>
      <p class="text-xs text-slate-400 mb-2">
        Source and target filenames may differ when both patterns expose the configured match tokens.
      </p>
      <div class="grid-2">
        <input x-model="mfCompareTargetRoot" class="field-input" placeholder="/exports/finance"
               data-testid="compare-mf-target-root-input" />
        <input x-model="mfCompareTargetPattern" class="field-input" placeholder="financials-{region:alpha}-B{batch:num}.xlsx"
               data-testid="compare-mf-target-pattern-input" />
      </div>
    </div>
    <div class="rounded border border-slate-200 bg-slate-50 p-3 text-xs text-slate-500">
      <div class="font-medium text-slate-600 mb-1">Pattern tokens</div>
      <div>
        Use {region}, {batch:num}, {code:alpha}, {id:alnum}, {date:%Y%m%d}, {custom:regex([A-Z]{2}\d{4})}, * and ?.
      </div>
      <div class="mt-1">
        Explicit matching requires every token in Match On to appear in both source and target patterns. Use automated matching when names share no stable token.
      </div>
    </div>
```

- [ ] **Step 2: Build HTML bundle**

Run: `npm run build:html`

Expected: command exits 0 and updates the generated frontend bundle if this project stores a built HTML artifact.

- [ ] **Step 3: Inspect generated diff**

Run: `git diff -- frontend/partials/tab-compare.html frontend/index.html`

Expected: diff contains only Multi-File help/copy changes and generated HTML updates.

---

### Task 4: E2E Coverage for Dynamic Non-CSV Multi-File Compare

**Files:**
- Modify: `tests/e2e/08g-compare-multi-file.spec.ts`
- Create if absent: `tests/e2e/fixtures/data/multi_source_dynamic/`
- Create if absent: `tests/e2e/fixtures/data/multi_target_dynamic/`

**Interfaces:**
- Consumes: UI test IDs already used by `08g-compare-multi-file.spec.ts`.
- Produces: Playwright coverage for dynamic source/target names and non-CSV local file patterns.

- [ ] **Step 1: Add JSON fixtures**

Create `tests/e2e/fixtures/data/multi_source_dynamic/extract_AB12.json`:

```json
[
  {"id": 1, "amount": 10, "status": "ok"},
  {"id": 2, "amount": 20, "status": "ok"}
]
```

Create `tests/e2e/fixtures/data/multi_target_dynamic/prod_AB12.json`:

```json
[
  {"id": 1, "amount": 10, "status": "ok"},
  {"id": 2, "amount": 20, "status": "ok"}
]
```

- [ ] **Step 2: Add Playwright test**

Append this test inside the existing `test.describe('08g compare / multi-file', () => { ... })` block in `tests/e2e/08g-compare-multi-file.spec.ts`.

```ts
  test('previews and runs non-CSV file sets with different dynamic names', async ({ authedPage }) => {
    await openMultiFile(authedPage);

    await authedPage.locator('[data-testid="compare-mf-key-columns-input"]').fill('id');
    await authedPage.locator('[data-testid="compare-mf-match-on-input"]').fill('id');
    await authedPage.locator('[data-testid="compare-mf-source-root-input"]').fill(path.join(FIXTURE_DIR, 'multi_source_dynamic'));
    await authedPage.locator('[data-testid="compare-mf-source-pattern-input"]').fill('extract_{id:alnum}.json');
    await authedPage.locator('[data-testid="compare-mf-target-root-input"]').fill(path.join(FIXTURE_DIR, 'multi_target_dynamic'));
    await authedPage.locator('[data-testid="compare-mf-target-pattern-input"]').fill('prod_{id:regex([A-Z]{2}\\d{2})}.json');

    await authedPage.locator('[data-testid="compare-mf-preview-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-mf-preview-result"]')).toContainText('1 pair(s) matched');
    await expect(authedPage.locator('[data-testid="compare-mf-preview-pair"]')).toContainText('extract_AB12.json');
    await expect(authedPage.locator('[data-testid="compare-mf-preview-pair"]')).toContainText('prod_AB12.json');

    await authedPage.locator('[data-testid="compare-mf-run-btn"]').click();
    await expect(authedPage.locator('[data-testid="compare-mf-results"]')).toBeVisible({ timeout: 20_000 });
    await expect(authedPage.locator('[data-testid="compare-mf-results"]')).toContainText('PASSED');
    await expect(authedPage.locator('[data-testid="compare-mf-result-pair"][data-status="PASSED"]')).toContainText('extract_AB12.json');
  });
```

- [ ] **Step 3: Run targeted E2E test**

Run: `npx playwright test tests/e2e/08g-compare-multi-file.spec.ts --grep "non-CSV file sets"`

Expected: PASS. If the local Playwright server is not configured in this session, record the setup failure and run the unit tests from Tasks 1 and 2 instead.

---

### Task 5: Documentation and Final Verification

**Files:**
- Modify: `docs/multi_file_reconciliation.md`

**Interfaces:**
- Consumes: implemented token syntax from Task 1.
- Produces: updated user-facing quick reference for Compare tab and saved-job matching patterns.

- [ ] **Step 1: Update filename pattern docs**

In `docs/multi_file_reconciliation.md`, add this subsection after the minimal example or existing pattern explanation:

```markdown
## Filename pattern tokens

Multi-file reconciliation supports dynamic filename patterns on each side.
Source and target patterns may use different prefixes, suffixes, separators,
and extensions as long as the tokens listed in `match_on` appear in both
patterns.

- `{region}` captures a simple token up to `_`, `.`, `/`, or `\`.
- `{batch:num}` captures one or more digits.
- `{code:alpha}` captures one or more ASCII letters.
- `{id:alnum}` captures one or more ASCII letters or digits.
- `{date:%Y%m%d}` captures fixed-width date-style digits.
- `{custom:regex([A-Z]{2}\d{4})}` captures a custom regex.
- `*` and `?` can be used as glob wildcards outside token braces.

Example: source `sales_{region:alpha}_{batch:num}.xlsx` and target
`financials-{region:alpha}-B{batch:num}.xlsx` can be paired with
`match_on: ["region", "batch"]`.
```

- [ ] **Step 2: Update current limitations wording**

In `docs/multi_file_reconciliation.md`, adjust the Compare tab limitation so it says local-only is still true for source kind, but local multi-file compare supports all formats parsed by the shared file reader.

Use this wording:

```markdown
- The Compare tab's Multi-File sub-tab remains `kind: "local"` for both
  preview and running a comparison. Within local server-accessible directories,
  it uses the shared tabular reader for CSV, Excel, JSON, XML, TSV, and
  delimiter-sniffed text files.
```

- [ ] **Step 3: Run targeted verification**

Run: `python -m pytest tests/unit/test_compare_utils.py tests/unit/test_multi_file_compare_request.py tests/unit/test_compare_service_multi_file.py -v`

Expected: PASS.

- [ ] **Step 4: Run HTML build**

Run: `npm run build:html`

Expected: PASS.

- [ ] **Step 5: Inspect final diff**

Run: `git diff --stat; git diff -- etl_framework/reconciliation/file_mapping.py frontend/partials/tab-compare.html docs/multi_file_reconciliation.md tests/unit/test_compare_utils.py tests/unit/test_compare_service_multi_file.py tests/e2e/08g-compare-multi-file.spec.ts`

Expected: diff includes only planned files, dynamic token compiler changes, UI copy/help, docs, and tests.

---

## Plan Self-Review

- Spec coverage: Tasks 1 and 2 cover dynamic filename matching and non-CSV backend support; Task 3 covers UI clarity; Task 4 covers UI behavior; Task 5 covers docs and final verification.
- Placeholder scan: no placeholders or vague implementation instructions remain.
- Type consistency: `compile_token_pattern(pattern: str)` remains the public interface; `MultiFileCompareRequest` and `CompareService.run_multi_file_compare(req, run_id)` are used with their existing signatures.
