# Full HTML Report Download Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Download Full HTML Report" action to the History tab that produces a single, self-contained HTML file containing the run's *entire* comparison (no `mismatch_row_limit` truncation), searchable/filterable offline via the report's existing client-side toolbar.

**Architecture:** Reuses the existing async export-job machinery (`DifferenceExportJob`, `POST /exports` → poll → `GET /exports/{id}/download`) that already powers "All differences" CSV/Parquet downloads, adding `"html"` as a new job format. A new `write_full_html_report()` recomputes the complete difference set (same recompute-from-source path already used for CSV/Parquet/JSON when DB-stored rows are incomplete) and renders it through the existing `report.html.j2` template — the template's truncation banners and "Load all" buttons disappear automatically once every result's `mismatches` list already equals its `total_issues` count, so no new template flag is needed.

**Tech Stack:** FastAPI, SQLAlchemy, Jinja2, vanilla JS (Alpine.js), Playwright e2e.

**Reference:** design spec at `docs/superpowers/specs/2026-07-18-full-html-report-download-design.md`.

---

## Before you start

Read these existing files in full before touching anything — every task below assumes you already know their current shape:
- `api/services/difference_export.py`
- `api/services/run_report.py`
- `etl_framework/reporting/generator.py`
- `etl_framework/reporting/templates/report.html.j2` (lines 280-520 especially)
- `frontend/features/compare.js` (lines 700-812)

---

### Task 1: Fix the `test_name` fallback mismatch between compare success path and recompute path

**Why first:** `write_full_html_report` (Task 3) groups recomputed rows by `test_name` and matches them against each `ReportResult.query_name`. For SQL and File compares, the real success path (`api/services/compare_service.py:361`, inside `_run_tabular_file_compare`) names the result `req.label_a or "file_a"` — but the recompute path in `difference_export.py` currently uses different fallback strings (`"sql_comparison"`, `"recon_file"`). When a user runs a SQL or File compare *without* setting a label, this mismatch means recomputed rows silently fail to group with their result (empty mismatch list) — the same bug already silently breaks the existing "Load all differences" button in the report template today. Fixing this is a correctness prerequisite for Task 3, not a drive-by refactor.

**Files:**
- Modify: `api/services/difference_export.py:379-405` (`_write_sql_compare`), `:423-438` (`_write_recon_file_compare`)
- Test: `tests/unit/test_full_differences_export.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_full_differences_export.py` (after the existing `_create_run_with_result` helper, before `test_full_difference_download_streams_stored_rows_when_complete`):

```python
def test_write_recomputed_differences_sql_compare_uses_file_a_fallback(tmp_path, monkeypatch):
    """Recompute must group rows under the SAME test_name the real compare success
    path uses (req.label_a or "file_a" -- see compare_service.py:361), or the
    grouping in write_full_html_report (and the report's "Load all" button) silently
    drops every row when label_a is unset."""
    import pandas as pd
    from api.schemas import SQLCompareRequest
    from api.services.difference_export import _write_sql_compare, DifferenceWriter

    class _FakeConfig:
        config_json = {}
        env_name = "dev"

    class _FakeConfigRepo:
        def get(self, config_id):
            return _FakeConfig()

    class _FakeEngine:
        def __init__(self, df):
            self._df = df
        def dispose(self):
            pass

    monkeypatch.setattr("api.services.difference_export.ConfigRepository", lambda db: _FakeConfigRepo())
    monkeypatch.setattr("api.services.difference_export.resolve_connection", lambda *a, **kw: object())
    monkeypatch.setattr("api.services.difference_export.DBEngine", lambda env: _FakeEngine(None))
    monkeypatch.setattr(
        "api.services.difference_export._load_in_chunks",
        lambda engine, query, keys, chunk_size: (
            pd.DataFrame({"id": [1], "amount": [10]}) if engine._df is None else engine._df
        ),
    )

    req = SQLCompareRequest(
        config_id_a=1, config_id_b=2,
        connection_a="dev", connection_b="dev",
        query_a="select 1", query_b="select 1",
        key_columns=["id"], label_a="", label_b="",
    )
    path = tmp_path / "rows.jsonl"
    with DifferenceWriter(path, "json") as writer:
        _write_sql_compare(None, req.model_dump(), writer)

    import json
    lines = [json.loads(l) for l in path.read_text(encoding="utf-8").strip().splitlines()]
    # amount differs source(10) vs target(10) via the same fake loader -> force a
    # target that differs so at least one row is written
    assert all(row["test_name"] == "file_a" for row in lines) or lines == []
```

- [ ] **Step 2: Run test to verify current behavior**

Run: `python -m pytest tests/unit/test_full_differences_export.py::test_write_recomputed_differences_sql_compare_uses_file_a_fallback -v`

This test is deliberately loose (the fake loader returns identical frames, so there may be zero mismatch rows — the `or lines == []` clause absorbs that). Its real purpose is documentation-by-test of the fallback string. Skip ahead: instead of relying on the loose fake-data test above, replace it with the simpler, deterministic assertion below — delete the test you just wrote and write this one instead, which tests the fallback logic directly without mocking the whole SQL load pipeline:

```python
def test_recon_file_and_sql_compare_recompute_use_file_a_fallback():
    """Both _write_sql_compare and _write_recon_file_compare must fall back to
    "file_a" (not "sql_comparison"/"recon_file") when label_a is unset, matching
    the real compare success path's fallback in compare_service.py:361
    (_run_tabular_file_compare -> reconciler.reconcile(_SENTINEL_QUERY, req.label_a or "file_a"))."""
    import inspect
    from api.services import difference_export as de

    sql_src = inspect.getsource(de._write_sql_compare)
    assert 'req.label_a or "file_a"' in sql_src or "req.label_a or 'file_a'" in sql_src

    recon_src = inspect.getsource(de._write_recon_file_compare)
    assert 'req.label_a or "file_a"' in recon_src or "req.label_a or 'file_a'" in recon_src
```

Run: `python -m pytest tests/unit/test_full_differences_export.py::test_recon_file_and_sql_compare_recompute_use_file_a_fallback -v`
Expected: FAIL (current source has `"sql_comparison"` / `"recon_file"`)

- [ ] **Step 3: Fix the fallback strings**

In `api/services/difference_export.py`, in `_write_sql_compare` (around line 402):

```python
    _write_tabular_differences(
        df_a,
        df_b,
        key_columns=req.key_columns or [],
        exclude_columns=req.exclude_columns or [],
        options=req.advanced,
        test_name=req.label_a or "file_a",
        writer=writer,
    )
```

(change `"sql_comparison"` → `"file_a"`)

In `_write_recon_file_compare` (around line 435):

```python
        _write_tabular_differences(
            source_a,
            source_b,
            key_columns=req.key_columns or [],
            exclude_columns=req.exclude_columns or [],
            options=req.advanced,
            test_name=req.label_a or "file_a",
            writer=writer,
        )
```

(change `"recon_file"` → `"file_a"`)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_full_differences_export.py -v`
Expected: all PASS, including the new test

- [ ] **Step 5: Commit**

```bash
git add api/services/difference_export.py tests/unit/test_full_differences_export.py
git commit -m "fix(export): align SQL/File compare recompute test_name fallback with the real compare success path

Both _write_sql_compare and _write_recon_file_compare used a different
fallback string ("sql_comparison"/"recon_file") than the actual compare
success path (_run_tabular_file_compare, which falls back to "file_a").
When label_a is unset, recomputed rows silently failed to group with
their TestResult -- breaking the report's Load-all-differences button
and, without this fix, the upcoming full-HTML-report grouping too."
```

---

### Task 2: Let `ReportGenerator.generate()` write to a caller-chosen filename

**Why:** The full HTML report is an export-job artifact living at `reports/exports/{run_id}/report_{run_id}_full_{export_id}.html`, not `ReportGenerator`'s fixed `report_{run_id}.html`. Rather than duplicating `generate()`'s atomic-write logic, give it an optional `filename` override.

**Files:**
- Modify: `etl_framework/reporting/generator.py:45-73`
- Test: `tests/unit/test_reporting_generator.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_reporting_generator.py`:

```python
def test_report_generator_accepts_filename_override(tmp_path):
    from etl_framework.reporting.generator import ReportGenerator
    import types

    suite = types.SimpleNamespace(
        run_id="run-xyz",
        started_at=None, source_env="dev", target_env="prod",
        test_cases=[], reconciliation_results=[],
        total_passed=0, total_failed=0, total_skipped=0, total_issues=0,
    )
    gen = ReportGenerator(output_dir=str(tmp_path))
    path = gen.generate(suite, filename="custom_name.html")

    assert path.endswith("custom_name.html")
    assert (tmp_path / "custom_name.html").exists()
    # default (no filename passed) still uses report_{run_id}.html
    default_path = gen.generate(suite)
    assert default_path.endswith("report_run-xyz.html")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_reporting_generator.py::test_report_generator_accepts_filename_override -v`
Expected: FAIL with `TypeError: generate() got an unexpected keyword argument 'filename'`

- [ ] **Step 3: Add the parameter**

In `etl_framework/reporting/generator.py`, change the `generate` method signature and the `report_path` line:

```python
    def generate(self, suite_result, filename: str | None = None) -> str:
        """
        Renders template with suite_result context.
        Creates output_dir if missing.
        Writes file to {output_dir}/{filename or report_{run_id}.html}.
        Returns the file path written.
        """
        try:
            self._output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            try:
                from etl_framework.exceptions import ReportOutputError
                raise ReportOutputError(str(self._output_dir), e) from e
            except ImportError:
                raise RuntimeError(f"Failed to create output directory {self._output_dir}: {e}") from e

        template = self._jinja_env.get_template(self.TEMPLATE_NAME)
        html_content = template.render(suite=suite_result)

        run_id = getattr(suite_result, "run_id", "unknown_run")
        report_path = self._output_dir / (filename or f"report_{run_id}.html")
```

(everything below that line is unchanged)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_reporting_generator.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add etl_framework/reporting/generator.py tests/unit/test_reporting_generator.py
git commit -m "feat(reporting): let ReportGenerator.generate() take an explicit filename

Needed so export-job artifacts (report_{run_id}_full_{export_id}.html)
can reuse the existing atomic-write logic instead of duplicating it."
```

---

### Task 3: Add `write_full_html_report()` and wire it into the export-job pipeline

**Files:**
- Modify: `api/services/difference_export.py`
- Test: create `tests/unit/test_full_html_report.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_full_html_report.py`:

```python
from __future__ import annotations

import json
from datetime import datetime, timezone

from etl_framework.reconciliation.models import MismatchRecord, ReconciliationResult
from etl_framework.repository import database as _db_module
from etl_framework.repository.repository import RunRepository
from etl_framework.runner.state import TestStatus


def _create_run_with_result(total_issues: int, stored_rows: int, query_name: str = "orders") -> str:
    with _db_module.SessionLocal() as db:
        repo = RunRepository(db)
        run = repo.create_run(
            run_id=f"run-full-html-{total_issues}-{stored_rows}",
            source_env="dev",
            target_env="prod",
            config_snapshot={"compare_request_type": "unknown", "request": {}},
        )
        result = ReconciliationResult(
            query_name=query_name,
            source_env="dev",
            target_env="prod",
            source_row_count=10,
            target_row_count=10,
            matched_count=10,
            missing_in_target_count=0,
            missing_in_source_count=0,
            value_mismatch_count=total_issues,
            mismatches=[],
            status=TestStatus.FAILED if total_issues else TestStatus.PASSED,
            executed_at=datetime.now(timezone.utc),
            duration_seconds=0.1,
        )
        tr = repo.add_test_result(run.run_id, result)
        repo.add_mismatch_details(tr.id, [
            MismatchRecord(
                key_values={"id": idx + 1},
                column_name="amount",
                source_value=idx,
                target_value=idx + 10,
                mismatch_type="value_diff",
                delta=10.0,
                relative_delta=None,
            )
            for idx in range(stored_rows)
        ])
        return run.run_id


def test_write_full_html_report_includes_rows_beyond_the_stored_cap(tmp_path, monkeypatch):
    """total_issues=5, only 2 stored -- write_full_html_report must recompute and
    bake in all 5, not just the 2 that made it into mismatch_details."""
    from api.services import difference_export as de
    from etl_framework.repository.models import TestRun

    run_id = _create_run_with_result(total_issues=5, stored_rows=2)

    def _fake_recompute(db, run, fmt, path):
        rows = [
            {
                "test_name": "orders", "key_values": json.dumps({"id": i}),
                "column_name": "amount", "source_value": str(i), "target_value": str(i + 100),
                "mismatch_type": "value_diff", "delta": 100.0, "relative_delta": None,
            }
            for i in range(5)
        ]
        with path.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        return len(rows)

    monkeypatch.setattr(de, "write_recomputed_differences", _fake_recompute)

    with _db_module.SessionLocal() as db:
        run = db.query(TestRun).filter(TestRun.run_id == run_id).first()
        out_path = tmp_path / "report_full.html"
        row_count = de.write_full_html_report(db, run, out_path)

    assert row_count == 5
    html = out_path.read_text(encoding="utf-8")
    # count actual rendered rows (`<tr data-mismatch`), not the substring
    # "data-mismatch" alone -- that also appears inside the template's JS as
    # `'tr[data-mismatch]'` selector strings, which would inflate a bare count
    assert html.count("<tr data-mismatch") == 5
    # nothing left to load -- the global "Load all differences" button must not render
    assert "load-all-btn-global" not in html


def test_write_full_html_report_key_values_round_trip_as_dict_not_double_encoded_string(tmp_path, monkeypatch):
    """DifferenceWriter pre-serializes key_values to a JSON string (_json_text); the
    recompute reader must json.loads it back to a dict, or the template's
    `mm.key_values | tojson` double-encodes it into an unusable string literal."""
    from api.services import difference_export as de
    from etl_framework.repository.models import TestRun

    run_id = _create_run_with_result(total_issues=1, stored_rows=0)

    def _fake_recompute(db, run, fmt, path):
        row = {
            "test_name": "orders", "key_values": json.dumps({"id": 42}),
            "column_name": "amount", "source_value": "1", "target_value": "2",
            "mismatch_type": "value_diff", "delta": 1.0, "relative_delta": None,
        }
        with path.open("w", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        return 1

    monkeypatch.setattr(de, "write_recomputed_differences", _fake_recompute)

    with _db_module.SessionLocal() as db:
        run = db.query(TestRun).filter(TestRun.run_id == run_id).first()
        out_path = tmp_path / "report_full.html"
        de.write_full_html_report(db, run, out_path)

    html = out_path.read_text(encoding="utf-8")
    import re
    from html import unescape
    match = re.search(r'data-key="([^"]*)"', html)
    assert match is not None
    parsed = json.loads(unescape(match.group(1)))
    # single-encoded dict, not a JSON string containing an escaped JSON string
    assert parsed == {"id": 42}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_full_html_report.py -v`
Expected: FAIL with `AttributeError: module 'api.services.difference_export' has no attribute 'write_full_html_report'`

- [ ] **Step 3: Implement `write_full_html_report`**

Add to `api/services/difference_export.py`, after `write_recomputed_differences` (after line 376):

```python
def write_full_html_report(db: Session, run: TestRun, path: Path) -> int:
    """Recompute the run's complete difference set and render it into a single,
    self-contained HTML report at `path` -- no live API calls needed to view the
    whole comparison, unlike the capped report the "Report" view/download produces.
    """
    from api.services.artifact_service import _current_app_timezone
    from api.services.run_report import build_run_report_snapshot
    from etl_framework.reporting.generator import ReportGenerator

    tmp_path = path.with_suffix(".rows.jsonl")
    row_count = write_recomputed_differences(db, run, "json", tmp_path)

    rows_by_test: dict[str, list[dict[str, Any]]] = {}
    with tmp_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            key_values = row.get("key_values")
            if isinstance(key_values, str):
                try:
                    key_values = json.loads(key_values)
                except (TypeError, ValueError):
                    key_values = {}
            rows_by_test.setdefault(row.get("test_name") or "", []).append({
                "column_name": row.get("column_name"),
                "mismatch_type": row.get("mismatch_type"),
                "key_values": key_values,
                "source_value": row.get("source_value"),
                "target_value": row.get("target_value"),
            })
    tmp_path.unlink(missing_ok=True)

    snapshot = build_run_report_snapshot(run, include_mismatches=False)
    for result in snapshot.results:
        result.mismatches = rows_by_test.get(result.query_name, [])
        result.total_issues_override = len(result.mismatches)

    generator = ReportGenerator(output_dir=str(path.parent), timezone=_current_app_timezone())
    generator.generate(snapshot, filename=path.name)
    return row_count
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_full_html_report.py -v`
Expected: all PASS

- [ ] **Step 5: Wire `"html"` into `run_difference_export_job`, `export_filename`, `media_type_for`**

In `api/services/difference_export.py`, `export_filename` (around line 226):

```python
def export_filename(run_id: str, fmt: str, export_id: str | None = None) -> str:
    if fmt == "parquet":
        suffix = "parquet"
    elif fmt == "json":
        suffix = "jsonl"
    elif fmt == "html":
        suffix = "html"
    else:
        suffix = "csv"
    stem = f"all_differences_{run_id}"
    if export_id:
        stem += f"_{export_id}"
    return f"{stem}.{suffix}"
```

`media_type_for` (around line 218):

```python
def media_type_for(fmt: str) -> str:
    if fmt == "parquet":
        return "application/vnd.apache.parquet"
    if fmt == "json":
        return "application/x-ndjson"
    if fmt == "html":
        return "text/html"
    return "text/csv"
```

`run_difference_export_job` (around lines 333-334) — replace:

```python
        path = export_dir(job.run_id) / export_filename(job.run_id, job.format, job.export_id)
        row_count = write_recomputed_differences(db, run, job.format, path)
```

with:

```python
        path = export_dir(job.run_id) / export_filename(job.run_id, job.format, job.export_id)
        if job.format == "html":
            row_count = write_full_html_report(db, run, path)
        else:
            row_count = write_recomputed_differences(db, run, job.format, path)
```

- [ ] **Step 6: Add coverage for the new format helpers**

Add to `tests/unit/test_difference_export.py`:

```python
def test_media_type_for_html():
    from api.services.difference_export import media_type_for

    assert media_type_for("html") == "text/html"


def test_export_filename_html_uses_html_suffix():
    from api.services.difference_export import export_filename

    name = export_filename("run-1", "html", "exp-1")
    assert name.endswith(".html")
    assert "run-1" in name and "exp-1" in name
```

- [ ] **Step 7: Run the full difference-export test suite**

Run: `python -m pytest tests/unit/test_difference_export.py tests/unit/test_full_differences_export.py tests/unit/test_full_html_report.py -v`
Expected: all PASS

- [ ] **Step 8: Commit**

```bash
git add api/services/difference_export.py tests/unit/test_full_html_report.py tests/unit/test_difference_export.py
git commit -m "feat(export): add write_full_html_report, wire html into the export-job pipeline

Recomputes the run's complete difference set (same path already used
for CSV/Parquet/JSON when DB-stored rows are truncated) and renders it
through report.html.j2 -- baking every mismatch into one self-contained
file instead of the DB-row-limit-capped subset the normal HTML report
download produces."
```

---

### Task 4: Accept `"html"` as a valid export-job format end to end

**Files:**
- Modify: `api/schemas.py` (`DifferenceExportRequest`, `DifferenceExportStatusOut`)
- Modify: `api/routes/runs.py:788-802` (`create_difference_export`)
- Test: `tests/unit/test_difference_export.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_difference_export.py` (mirrors the existing `test_create_export_job_accepts_json_format`):

```python
def test_create_export_job_accepts_html_format(monkeypatch):
    from fastapi.testclient import TestClient

    from api.main import app
    from etl_framework.repository.database import Base, get_db
    from etl_framework.repository import database as _db_module
    import etl_framework.repository.models  # noqa: F401
    from etl_framework.repository.repository import RunRepository, TokenRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr("api.routes.runs.run_difference_export_job", lambda export_id: None)

    def override_get_db():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_db] = override_get_db
    try:
        with Session(engine) as db:
            raw, _ = TokenRepository(db).create("test-runner")
            run_id = str(uuid.uuid4())
            RunRepository(db).create_run(run_id, "dev", "qa", {})

        client = TestClient(app, headers={"Authorization": f"Bearer {raw}"})
        resp = client.post(f"/api/runs/{run_id}/exports", json={"format": "html"})
        assert resp.status_code == 202
        data = resp.json()
        assert data["format"] == "html"
    finally:
        app.dependency_overrides.pop(get_db, None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_difference_export.py::test_create_export_job_accepts_html_format -v`
Expected: FAIL with 422 (Literal rejects `"html"`)

- [ ] **Step 3: Extend the schema Literals**

In `api/schemas.py`, find `class DifferenceExportRequest` (around line 822) and `class DifferenceExportStatusOut` (around line 826):

```python
class DifferenceExportRequest(BaseModel):
    format: Literal["csv", "parquet", "json", "html"] = "csv"


class DifferenceExportStatusOut(BaseModel):
    export_id: str
    run_id: str
    format: Literal["csv", "parquet", "json", "html"]
    status: Literal["PENDING", "RUNNING", "COMPLETED", "FAILED"]
    row_count: int = 0
    error_message: str | None = None
    artifact_path: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    recomputed_at: datetime | None = None
    metadata: dict[str, Any] | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_difference_export.py -v`
Expected: all PASS

Note: `create_difference_export` (`api/routes/runs.py:789-802`) calls `validate_difference_format(body.format)`, and `validate_difference_format` (in `difference_export.py`) still only accepts `{"csv", "parquet", "json"}` — leave it that way (it's also used by `GET /differences/download`, which must keep rejecting `"html"`: that route's fast/stored path goes through `DifferenceWriter`, which has no `"html"` branch and would raise `ValueError` if it got one). Since `body.format` is already constrained by the `Literal` above by the time it reaches the route, replace the redundant validation call with the already-validated value directly.

In `api/routes/runs.py`, `create_difference_export` (around line 795):

```python
@router.post("/{run_id}/exports", response_model=DifferenceExportStatusOut, status_code=202)
def create_difference_export(
    run_id: str,
    body: DifferenceExportRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    fmt = body.format
    repo = RunRepository(db)
    if repo.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    job, created = create_or_reuse_export_job(db, run_id, fmt)
    if created and job.status == "PENDING":
        background_tasks.add_task(run_difference_export_job, job.export_id)
    return export_status_out(job)
```

(only the `fmt = body.format` line changed — was `fmt = validate_difference_format(body.format)`)

Since `validate_difference_format` is no longer called from this route, remove it from this file's import list only if it's otherwise unused here — check first:

Run: `grep -n "validate_difference_format" api/routes/runs.py`

If the only remaining use is in `download_all_differences` (the `GET /differences/download` route), leave the import as-is (still needed there).

- [ ] **Step 5: Run the full route test suite for this area**

Run: `python -m pytest tests/unit/test_difference_export.py tests/unit/test_full_differences_export.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add api/schemas.py api/routes/runs.py tests/unit/test_difference_export.py
git commit -m "feat(api): accept html as a difference-export job format

DifferenceExportRequest/StatusOut now allow format=html. The
GET /differences/download fast-path deliberately still rejects it
(no DifferenceWriter html branch there) -- only the async /exports
job path supports it, via write_full_html_report."
```

---

### Task 5: Add `GET /{run_id}/differences/summary`

**Files:**
- Modify: `api/routes/runs.py` (add route near `download_all_differences`, after line 785)
- Test: `tests/unit/test_full_differences_export.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_full_differences_export.py`:

```python
def test_differences_summary_reports_stored_vs_total(client):
    run_id = _create_run_with_result(total_issues=5, stored_rows=2)

    resp = client.get(f"/api/runs/{run_id}/differences/summary")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_issues"] == 5
    assert body["stored_rows"] == 2


def test_differences_summary_404s_for_missing_run(client):
    resp = client.get("/api/runs/does-not-exist/differences/summary")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_full_differences_export.py::test_differences_summary_reports_stored_vs_total -v`
Expected: FAIL with 404 (route doesn't exist / method not allowed)

- [ ] **Step 3: Add the route**

In `api/routes/runs.py`, right after `download_all_differences` (after line 785, before `create_difference_export`):

```python
@router.get("/{run_id}/differences/summary")
def get_differences_summary(
    run_id: str,
    db: Session = Depends(get_session),
):
    """Total mismatch count vs. what's actually stored -- used by the frontend to
    show a size estimate before starting a full-differences export/report."""
    repo = RunRepository(db)
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return stored_completeness_summary(db, run)
```

(`stored_completeness_summary` is already imported in this file's `from api.services.difference_export import (...)` block)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_full_differences_export.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add api/routes/runs.py tests/unit/test_full_differences_export.py
git commit -m "feat(api): add GET /runs/{run_id}/differences/summary

Lightweight endpoint the frontend uses to show a mismatch-count/size
estimate before the user confirms a full HTML report download."
```

---

### Task 6: Debounce the report's search input

**Files:**
- Modify: `etl_framework/reporting/templates/report.html.j2:312-313`
- Test: `tests/unit/test_report_template.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_report_template.py`, inside `class TestReportTemplateSmoke`:

```python
    def test_filter_search_is_debounced(self, tmp_path):
        html = _render(_make_suite(), tmp_path)
        assert "setTimeout" in html.split('id="filter-search"')[1][:400]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_report_template.py::TestReportTemplateSmoke::test_filter_search_is_debounced -v`
Expected: FAIL (no `setTimeout` near the search input currently)

- [ ] **Step 3: Add the debounce**

In `etl_framework/reporting/templates/report.html.j2`, replace lines 312-313:

```html
        <input id="filter-search" type="text" placeholder="Search key values… (/ to focus)"
               oninput="filterState.search=this.value;applyFilters()">
```

with:

```html
        <input id="filter-search" type="text" placeholder="Search key values… (/ to focus)"
               oninput="clearTimeout(window.__filterSearchDebounce); var _v=this.value; window.__filterSearchDebounce=setTimeout(function(){filterState.search=_v;applyFilters();}, 200);">
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_report_template.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add etl_framework/reporting/templates/report.html.j2 tests/unit/test_report_template.py
git commit -m "fix(report): debounce the mismatch search input

Un-debounced oninput re-scanned every tr[data-mismatch] on each
keystroke -- fine at the usual ~100-row truncated view, but laggy on
the new full-HTML-report path where a section can hold tens of
thousands of rows."
```

---

### Task 7: Frontend — `downloadFullHtmlReport()` + button

**Files:**
- Modify: `frontend/features/compare.js` (add method near `downloadAllDifferences`/`pollDifferenceExport`, lines 757-808)
- Modify: `frontend/partials/tab-history.html` (button, near lines 48-61)
- Regenerate: `frontend/index.html` (via `node scripts/build-html.js`)

- [ ] **Step 1: Extend `pollDifferenceExport`'s filename fallback for html**

In `frontend/features/compare.js`, `pollDifferenceExport` (around line 796), the completed-branch fallback filename currently only handles csv/parquet:

```javascript
          const fallback = `all_differences_${runId}_${exportId}.${format === 'parquet' ? 'parquet' : 'csv'}`;
```

Replace with:

```javascript
          const ext = format === 'parquet' ? 'parquet' : format === 'html' ? 'html' : 'csv';
          const fallback = `all_differences_${runId}_${exportId}.${ext}`;
```

- [ ] **Step 2: Add `downloadFullHtmlReport`**

In `frontend/features/compare.js`, add right after `downloadAllDifferences` (after line 787, before `pollDifferenceExport`):

```javascript
    async downloadFullHtmlReport(runId) {
      if (!runId || this.isDifferenceExportBusy(runId, 'html')) return;
      let summary;
      try {
        summary = await api('GET', `/api/runs/${runId}/differences/summary`);
      } catch (e) {
        this.toast('error', 'Failed to load mismatch summary', e.message);
        return;
      }
      const estMb = ((summary.total_issues || 0) * 1.8 / 1024).toFixed(1);
      if (!confirm(`This run has ${summary.total_issues} total mismatches (~${estMb} MB estimated). Continue?`)) return;
      const key = this.differenceExportKey(runId, 'html');
      this.differenceExports = { ...this.differenceExports, [key]: { status: 'PENDING' } };
      try {
        const job = await api('POST', `/api/runs/${runId}/exports`, { format: 'html' });
        this.differenceExports = { ...this.differenceExports, [key]: job };
        await this.pollDifferenceExport(runId, job.export_id, 'html');
      } catch (e) {
        this.differenceExports = { ...this.differenceExports, [key]: { status: 'FAILED', error_message: e.message } };
        this.toast('error', 'Full report download failed', e.message);
      }
    },
```

- [ ] **Step 3: Add the button to History tab run detail**

In `frontend/partials/tab-history.html`, in the run-detail action row (lines 48-61), add a new button right after the "All differences ... parquet" button (after line 60, before the closing `</div>` at line 61):

```html
            <button @click="downloadFullHtmlReport(selectedRun.run_id)"
                    :disabled="isDifferenceExportBusy(selectedRun.run_id, 'html')"
                    class="text-indigo-500 hover:underline text-xs disabled:text-slate-400"
                    data-testid="history-download-full-report-btn"
                    x-text="'Download Full HTML Report ' + differenceExportLabel(selectedRun.run_id, 'html')"></button>
```

- [ ] **Step 4: Rebuild `index.html` from the partials**

Run: `node scripts/build-html.js`
Expected output: `Built <repo>\frontend\index.html from <repo>\frontend\index.template.html + N partials`

- [ ] **Step 5: Sanity-check the build**

Run: `grep -n "downloadFullHtmlReport" frontend/index.html`
Expected: one match, inside the History tab section

- [ ] **Step 6: Commit**

```bash
git add frontend/features/compare.js frontend/partials/tab-history.html frontend/index.html
git commit -m "feat(frontend): add Download Full HTML Report button to History tab

Reuses the existing export-job poll/download plumbing
(differenceExportKey/State/Label, pollDifferenceExport) already used
by the All differences CSV/Parquet buttons, with format=html and a
confirm-dialog size estimate up front."
```

---

### Task 8: E2E test for the full flow

**Files:**
- Modify: `tests/e2e/06-reports.spec.ts` (or add a new block in `04-history.spec.ts` — use `04-history.spec.ts` since the button lives in History tab run detail, matching existing test file scoping by tab)
- Reference: `tests/e2e/api-helpers.ts` (`createFileJob`, `triggerRun`, `waitForTerminal`, `deleteJob`, `authedContext`)

- [ ] **Step 1: Write the test**

Add to `tests/e2e/04-history.spec.ts`, inside `test.describe('04 history', ...)`, as a new `test(...)` block (after the existing "Run Detail shows..." test):

```typescript
  test('Download Full HTML Report produces a self-contained file with all mismatches', async ({ authedPage, adminToken }) => {
    const ctx = await authedContext(adminToken);
    let fullReportJobName = '';
    let fullReportRunId = '';
    try {
      const job = await createFileJob(ctx, 'e2e-full-html-report');
      fullReportJobName = job.name;
      const { run_id } = await triggerRun(ctx, [fullReportJobName]);
      fullReportRunId = run_id;
      await waitForTerminal(ctx, run_id);

      await authedPage.goto('/');
      await authedPage.locator('[data-testid="nav-tab-history"]').click();
      await authedPage.locator('[data-testid="history-subtab-runs"]').click();
      await authedPage.locator(`[data-testid="history-run-row-${run_id}"]`).click();
      await expect(authedPage.locator('[data-testid="run-detail-back-btn"]')).toBeVisible();

      authedPage.once('dialog', (d) => d.accept());
      const downloadPromise = authedPage.waitForEvent('download');
      await authedPage.locator('[data-testid="history-download-full-report-btn"]').click();
      const download = await downloadPromise;

      expect(download.suggestedFilename()).toContain('.html');
      const downloadPath = await download.path();
      const fs = require('fs');
      const html = fs.readFileSync(downloadPath, 'utf-8');
      assertFullReportContainsAllMismatches(html);
    } finally {
      if (fullReportJobName) await deleteJob(ctx, fullReportJobName);
      await ctx.dispose();
    }
  });
```

Add this helper function near the top of the file (after the imports, before `test.describe`):

```typescript
function assertFullReportContainsAllMismatches(html: string) {
  expect(html).toContain('data-mismatch');
  // the global "Load all differences" button only renders when some section is
  // still truncated -- a full report must never show it
  expect(html).not.toContain('load-all-btn-global');
}
```

- [ ] **Step 2: Run the test to verify it fails (before this plan's other tasks are wired) or passes (after)**

Run: `npx playwright test tests/e2e/04-history.spec.ts -g "Download Full HTML Report"`
Expected (once Tasks 1-7 are all complete): PASS

If run before earlier tasks are done, this fails with a missing `data-testid` / 404 on `/differences/summary` — that's expected until the backend/frontend tasks above are in place.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/04-history.spec.ts
git commit -m "test(e2e): cover Download Full HTML Report end to end

Creates a real failing compare, downloads the full report, and asserts
the downloaded file has no truncation artifacts (no load-all-btn-global)
-- i.e. it's genuinely self-contained, not just successfully triggered."
```

---

### Task 9: Full verification pass

- [ ] **Step 1: Run the full backend unit test suite**

Run: `python -m pytest tests/unit -q`
Expected: all PASS, 0 failed

- [ ] **Step 2: Run the full e2e suite**

Run: `npx playwright test`
Expected: all PASS

- [ ] **Step 3: Manual smoke check**

Start the app locally, trigger a File Compare job whose result has more mismatches than `mismatch_row_limit` (or use an existing large run from history), click "Download Full HTML Report" in History tab run detail, confirm the dialog, wait for the download, open the downloaded file directly from disk (double-click, not through the app), and verify:
- the search box (`/` to focus) finds a mismatch that would have been past the old truncation cutoff
- no "Load all differences" button is present anywhere in the file
- filtering by column/type/test still works

- [ ] **Step 4: Update the design spec status (optional but recommended)**

If the implementation deviated from `docs/superpowers/specs/2026-07-18-full-html-report-download-design.md` in any way (e.g. the Task 1 fallback-string fix, which wasn't originally called out in the spec), add a short "Implementation notes" section to the spec documenting the deviation, and commit it.
