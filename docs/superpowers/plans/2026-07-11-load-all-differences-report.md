# Load All Differences Inline in the HTML Report — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `json` export format to the existing run-scoped differences export job, and use it to power a "Load all differences" button — both per-test and run-wide — that injects the complete mismatch set directly into the generated HTML report's own tables.

**Architecture:** Backend: extend `DifferenceWriter` (`api/services/difference_export.py`) with a newline-delimited-JSON (`jsonl`) output format, alongside the existing CSV/Parquet ones, reusing the same job creation/polling/download endpoints unchanged. Frontend (report only): add wrapper markup with stable element ids around each test's mismatch table, a global button and a per-test button, and vanilla JS (matching the report's existing hand-rolled style — `var`, `function`, no framework) that creates/polls the export job, fetches the NDJSON artifact, and appends rows into the DOM, then re-runs the report's existing filter/stat/nav rescans.

**Tech Stack:** FastAPI + Pydantic (backend), Jinja2 + vanilla JS (report template), pytest + `TestClient` (tests).

**Deviation from the approved design doc** ([2026-07-11-load-all-differences-report-design.md](../specs/2026-07-11-load-all-differences-report-design.md)): the design's step 4 said to re-run `buildHeatmap()`/`buildDonut()` after injecting rows. Reading the actual template (`report.html.j2`) shows both charts are driven by `aggregateMismatchData`, which is built from each result's `mismatch_by_column`/`mismatch_by_type`/`total_issues` — server-baked full-run statistics that are already complete regardless of how many detail rows are rendered in the DOM. Re-running those two functions would be a no-op at best. This plan re-runs `populateColFilter()`, `applyDiff()`, `computeAcceptedStats()`, and `applyFilters()` instead (the four functions that actually rescan `tr[data-mismatch]` in the DOM and would otherwise miss injected rows) — same outcome the design called for (charts/filters/stats stay correct after loading), fewer wasted calls.

---

### Task 1: Backend — add a `json` (NDJSON) export format to `DifferenceWriter`

**Files:**
- Modify: `api/services/difference_export.py:82-172` (`DifferenceWriter` class, `validate_difference_format`, `export_filename` — the last two are further down at lines 168-172 and 224-229, `media_type_for` at 218-221)
- Test: `tests/unit/test_difference_export.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_difference_export.py`:

```python
"""Tests for the DifferenceWriter json (NDJSON) export format and related helpers."""
from __future__ import annotations

import json
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def test_writer_json_format_writes_one_object_per_line(tmp_path):
    from api.services.difference_export import DifferenceWriter, DIFFERENCE_FIELDS

    path = tmp_path / "diffs.jsonl"
    with DifferenceWriter(path, "json") as writer:
        writer.write({
            "test_name": "orders", "key_values": {"id": 1}, "column_name": "amount",
            "source_value": "10", "target_value": "12", "mismatch_type": "value_diff",
            "delta": 2.0, "relative_delta": 0.2,
        })
        writer.write({
            "test_name": "orders", "key_values": {"id": 2}, "column_name": "amount",
            "source_value": "20", "target_value": "21", "mismatch_type": "value_diff",
            "delta": 1.0, "relative_delta": 0.05,
        })

    assert writer.row_count == 2
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        row = json.loads(line)
        assert set(row.keys()) == set(DIFFERENCE_FIELDS)
    first = json.loads(lines[0])
    assert first["test_name"] == "orders"
    assert first["key_values"] == '{"id": 1}'
    assert first["source_value"] == "10"


def test_validate_difference_format_accepts_json():
    from api.services.difference_export import validate_difference_format

    assert validate_difference_format("json") == "json"
    assert validate_difference_format("JSON") == "json"


def test_validate_difference_format_still_rejects_unknown():
    import pytest
    from fastapi import HTTPException
    from api.services.difference_export import validate_difference_format

    with pytest.raises(HTTPException):
        validate_difference_format("xlsx")


def test_media_type_for_json():
    from api.services.difference_export import media_type_for

    assert media_type_for("json") == "application/x-ndjson"


def test_export_filename_json_uses_jsonl_suffix():
    from api.services.difference_export import export_filename

    name = export_filename("run-1", "json", "exp-1")
    assert name.endswith(".jsonl")
    assert "run-1" in name and "exp-1" in name


def test_export_filename_csv_and_parquet_unaffected():
    from api.services.difference_export import export_filename

    assert export_filename("run-1", "csv").endswith(".csv")
    assert export_filename("run-1", "parquet").endswith(".parquet")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_difference_export.py -v`
Expected: FAIL — `DifferenceWriter(path, "json")` raises `ValueError: Unsupported export format: json`, and `validate_difference_format("json")` raises `HTTPException`.

- [ ] **Step 3: Implement the `json` format**

In `api/services/difference_export.py`, in `DifferenceWriter.__init__` (currently lines 93-107), change:

```python
        path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "csv":
            self._file = path.open("w", newline="", encoding="utf-8")
            self._csv_writer = csv.DictWriter(self._file, fieldnames=DIFFERENCE_FIELDS)
            self._csv_writer.writeheader()
        elif fmt == "parquet":
            try:
                import pyarrow as pa  # noqa: F401
                import pyarrow.parquet as pq  # noqa: F401
            except ImportError as exc:
                raise RuntimeError(
                    "pyarrow is required for Parquet exports. Install it with: pip install pyarrow"
                ) from exc
        else:
            raise ValueError(f"Unsupported export format: {fmt}")
```

to:

```python
        path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "csv":
            self._file = path.open("w", newline="", encoding="utf-8")
            self._csv_writer = csv.DictWriter(self._file, fieldnames=DIFFERENCE_FIELDS)
            self._csv_writer.writeheader()
        elif fmt == "json":
            self._file = path.open("w", encoding="utf-8")
        elif fmt == "parquet":
            try:
                import pyarrow as pa  # noqa: F401
                import pyarrow.parquet as pq  # noqa: F401
            except ImportError as exc:
                raise RuntimeError(
                    "pyarrow is required for Parquet exports. Install it with: pip install pyarrow"
                ) from exc
        else:
            raise ValueError(f"Unsupported export format: {fmt}")
```

In `DifferenceWriter.write` (currently lines 109-124), change:

```python
        if self.format == "csv":
            assert self._csv_writer is not None
            self._csv_writer.writerow(normalized)
        else:
            self._batch.append(normalized)
            if len(self._batch) >= self._batch_size:
                self._flush_parquet()
        self.row_count += 1
```

to:

```python
        if self.format == "csv":
            assert self._csv_writer is not None
            self._csv_writer.writerow(normalized)
        elif self.format == "json":
            assert self._file is not None
            self._file.write(json.dumps(normalized, ensure_ascii=False) + "\n")
        else:
            self._batch.append(normalized)
            if len(self._batch) >= self._batch_size:
                self._flush_parquet()
        self.row_count += 1
```

`close()` needs no change — it already does `if self._file is not None: self._file.close()`, which covers `json` since that branch sets `self._file`.

Change `validate_difference_format` (lines 168-172) from:

```python
def validate_difference_format(fmt: str) -> str:
    normalized = fmt.lower().strip()
    if normalized not in {"csv", "parquet"}:
        raise HTTPException(status_code=422, detail="format must be csv or parquet")
    return normalized
```

to:

```python
def validate_difference_format(fmt: str) -> str:
    normalized = fmt.lower().strip()
    if normalized not in {"csv", "parquet", "json"}:
        raise HTTPException(status_code=422, detail="format must be csv, parquet, or json")
    return normalized
```

Change `media_type_for` (lines 218-221) from:

```python
def media_type_for(fmt: str) -> str:
    if fmt == "parquet":
        return "application/vnd.apache.parquet"
    return "text/csv"
```

to:

```python
def media_type_for(fmt: str) -> str:
    if fmt == "parquet":
        return "application/vnd.apache.parquet"
    if fmt == "json":
        return "application/x-ndjson"
    return "text/csv"
```

Change `export_filename` (lines 224-229) from:

```python
def export_filename(run_id: str, fmt: str, export_id: str | None = None) -> str:
    suffix = "parquet" if fmt == "parquet" else "csv"
    stem = f"all_differences_{run_id}"
    if export_id:
        stem += f"_{export_id}"
    return f"{stem}.{suffix}"
```

to:

```python
def export_filename(run_id: str, fmt: str, export_id: str | None = None) -> str:
    if fmt == "parquet":
        suffix = "parquet"
    elif fmt == "json":
        suffix = "jsonl"
    else:
        suffix = "csv"
    stem = f"all_differences_{run_id}"
    if export_id:
        stem += f"_{export_id}"
    return f"{stem}.{suffix}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_difference_export.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add api/services/difference_export.py
git add -f tests/unit/test_difference_export.py
git commit -m "feat(export): add json (NDJSON) format to the differences export writer"
```

---

### Task 2: Backend — allow `json` in the export request/status schemas

**Files:**
- Modify: `api/schemas.py:745-755`

- [ ] **Step 1: Widen the two `Literal` types**

In `api/schemas.py`, change:

```python
class DifferenceExportRequest(BaseModel):
    format: Literal["csv", "parquet"] = "csv"


class DifferenceExportStatusOut(BaseModel):
    export_id: str
    run_id: str
    format: Literal["csv", "parquet"]
    status: Literal["PENDING", "RUNNING", "COMPLETED", "FAILED"]
    row_count: int = 0
    error_message: str | None = None
```

to:

```python
class DifferenceExportRequest(BaseModel):
    format: Literal["csv", "parquet", "json"] = "csv"


class DifferenceExportStatusOut(BaseModel):
    export_id: str
    run_id: str
    format: Literal["csv", "parquet", "json"]
    status: Literal["PENDING", "RUNNING", "COMPLETED", "FAILED"]
    row_count: int = 0
    error_message: str | None = None
```

- [ ] **Step 2: Verify the module still imports cleanly**

Run: `python -c "import api.schemas"`
Expected: no output, exit code 0.

- [ ] **Step 3: Commit**

```bash
git add api/schemas.py
git commit -m "feat(schemas): accept json as a valid differences export format"
```

---

### Task 3: Backend — endpoint-level test that `POST /exports` accepts `format=json`

**Files:**
- Modify: `tests/unit/test_difference_export.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_difference_export.py`:

```python
def test_create_export_job_accepts_json_format(monkeypatch):
    from fastapi.testclient import TestClient

    from api.main import app
    from etl_framework.repository.database import Base
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

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test-runner")
        run_id = str(uuid.uuid4())
        RunRepository(db).create_run(run_id, "dev", "qa", {})

    client = TestClient(app, headers={"Authorization": f"Bearer {raw}"})
    resp = client.post(f"/api/runs/{run_id}/exports", json={"format": "json"})
    assert resp.status_code == 202
    data = resp.json()
    assert data["format"] == "json"
    assert data["status"] in ("PENDING", "RUNNING", "COMPLETED", "FAILED")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_difference_export.py::test_create_export_job_accepts_json_format -v`
Expected: FAIL — before Tasks 1-2, `format=json` is rejected with a 422 from either the Pydantic `Literal` or `validate_difference_format`. Since Tasks 1-2 are already applied by this point in the plan, this instead confirms the whole path works end-to-end; if it fails here, re-check Tasks 1-2 were applied correctly.

- [ ] **Step 3: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_difference_export.py -v`
Expected: PASS (7 tests)

- [ ] **Step 4: Commit**

```bash
git add -f tests/unit/test_difference_export.py
git commit -m "test(export): verify POST /exports accepts format=json end-to-end"
```

---

### Task 4: Report — wrapper markup, stable ids, and both "Load all" buttons

**Files:**
- Modify: `etl_framework/reporting/templates/report.html.j2:357-437`

- [ ] **Step 1: Replace the mismatches header block**

Replace (current lines 357-361):

```html
    <div id="mismatches-header" style="display:flex;align-items:center;gap:8px;margin-bottom:12px">
      <h2 style="margin:0">Mismatch Details</h2>
      <button class="expand-all-btn" onclick="setAllDetails(true)">Expand All</button>
      <button class="expand-all-btn" onclick="setAllDetails(false)">Collapse All</button>
    </div>
```

with:

```html
    {% set ns = namespace(any_truncated=false) %}
    {% for result in suite.reconciliation_results %}
      {% if result.total_issues > (result.mismatches | length) %}{% set ns.any_truncated = true %}{% endif %}
    {% endfor %}
    <div id="mismatches-header" style="display:flex;align-items:center;gap:8px;margin-bottom:12px;flex-wrap:wrap">
      <h2 style="margin:0">Mismatch Details</h2>
      <button class="expand-all-btn" onclick="setAllDetails(true)">Expand All</button>
      <button class="expand-all-btn" onclick="setAllDetails(false)">Collapse All</button>
      {% if ns.any_truncated %}
      <button id="load-all-btn-global" class="expand-all-btn" data-run="{{ suite.run_id }}"
              onclick="loadAllDifferencesGlobal(this)">Load all differences (entire run)</button>
      <span id="load-all-status-global" style="font-size:0.82em;color:var(--muted)"></span>
      {% endif %}
    </div>
```

- [ ] **Step 2: Replace the per-test mismatch loop**

Replace (current lines 362-437, the whole `<div id="mismatches">...</div>` block) with:

```html
    <div id="mismatches">
      {% for result in suite.reconciliation_results %}
        {% set shown_mismatches = result.mismatches | length %}
        {% set total_mismatches = result.total_issues %}
        {% if result.mismatches %}
          <div class="mismatch-section" data-result-id="{{ result.id }}" data-query-name="{{ result.query_name }}"
               data-source-env="{{ suite.source_env }}" data-target-env="{{ suite.target_env }}">
          <details>
            <summary>
              {{ result.query_name }} Mismatches
              (<span id="mismatch-count-{{ result.id }}">Showing first {{ shown_mismatches }}{% if total_mismatches > shown_mismatches %} of {{ total_mismatches }}{% endif %}</span>)
            </summary>
            {% if total_mismatches > shown_mismatches %}
            <p id="truncation-note-{{ result.id }}" style="margin:6px 0 12px;color:var(--muted);font-size:0.85em">
              {{ shown_mismatches }} of {{ total_mismatches }} rows shown; download the full differences export for all differences,
              or <a href="#" data-differences-link data-run="{{ suite.run_id }}" data-result="{{ result.id }}" style="color:var(--accent)">open in Differences Explorer &#8599;</a>,
              or <button id="load-all-btn-{{ result.id }}" class="expand-all-btn" data-result="{{ result.id }}" data-run="{{ suite.run_id }}"
                         onclick="loadAllDifferencesForSection(this)">Load all for this test</button>
              <span id="load-all-status-{{ result.id }}" style="font-size:0.9em;color:var(--muted)"></span>
            </p>
            {% endif %}
            <table>
              <thead>
                <tr>
                  <th>Mismatch Type</th>
                  <th>Column</th>
                  <th>Row Key Values</th>
                  <th colspan="2">Values ({{ suite.source_env }} → {{ suite.target_env }})</th>
                </tr>
              </thead>
              <tbody id="mismatch-tbody-{{ result.id }}">
                {% for mm in result.mismatches %}
                <tr data-mismatch
                    data-test="{{ result.query_name }}"
                    data-column="{{ mm.column_name }}"
                    data-type="{{ mm.mismatch_type }}"
                    data-key="{{ mm.key_values | tojson }}"
                    data-src="{{ mm.source_value if mm.source_value is not none else '' }}"
                    data-tgt="{{ mm.target_value if mm.target_value is not none else '' }}"
                    data-accepted="{{ 'true' if mm.accepted else 'false' }}">
                  <td><span class="badge {% if mm.mismatch_type in ('value_diff', 'value_mismatch') %}badge-amber{% else %}badge-gray{% endif %}">{{ mm.mismatch_type }}</span></td>
                  <td>{{ mm.column_name }}</td>
                  <td style="font-family: monospace; font-size: 0.85em;">{{ mm.key_values | tojson }}</td>
                  <td class="diff-values-cell" colspan="2">
                    <div class="diff-panels">
                      <div class="diff-panel diff-panel-src">
                        <span class="diff-panel-label">{{ suite.source_env }}</span>
                        <span class="diff-panel-val" data-role="src-diff"
                              data-raw="{{ mm.source_value if mm.source_value is not none else '' }}">{{ mm.source_value if mm.source_value is not none else 'NULL' }}</span>
                        <button class="copy-btn" onclick="copyVal(this, this.closest('tr').dataset.src)" title="Copy source value">⎘</button>
                      </div>
                      <div class="diff-panel diff-panel-tgt">
                        <span class="diff-panel-label">{{ suite.target_env }}</span>
                        <span class="diff-panel-val" data-role="tgt-diff"
                              data-raw="{{ mm.target_value if mm.target_value is not none else '' }}">{{ mm.target_value if mm.target_value is not none else 'NULL' }}</span>
                        <button class="copy-btn" onclick="copyVal(this, this.closest('tr').dataset.tgt)" title="Copy target value">⎘</button>
                      </div>
                    </div>
                  </td>
                </tr>
                {% if mm.accepted %}
                <tr style="background: rgba(52,211,153,0.12);">
                  <td colspan="5" class="accepted-note">
                    ✓ Accepted{% if mm.accepted_by %} by {{ mm.accepted_by }}{% endif %}{% if mm.accepted_at %} on {{ mm.accepted_at | to_local }}{% endif %}{% if mm.accepted_note %} — {{ mm.accepted_note }}{% endif %}
                  </td>
                </tr>
                {% endif %}
                {% endfor %}
              </tbody>
            </table>
          </details>
          </div>
        {% elif result.total_issues > 0 %}
          <div class="mismatch-section" data-result-id="{{ result.id }}" data-query-name="{{ result.query_name }}"
               data-source-env="{{ suite.source_env }}" data-target-env="{{ suite.target_env }}">
          <details>
            <summary>{{ result.query_name }} Mismatches (<span id="mismatch-count-{{ result.id }}">0 detail rows stored of {{ result.total_issues }}</span>)</summary>
            <p id="truncation-note-{{ result.id }}" style="margin:6px 0;color:var(--muted);font-size:0.9em">
              0 of {{ result.total_issues }} rows shown; download the full differences export for all differences,
              or <a href="#" data-differences-link data-run="{{ suite.run_id }}" data-result="{{ result.id }}" style="color:var(--accent)">open in Differences Explorer &#8599;</a>,
              or <button id="load-all-btn-{{ result.id }}" class="expand-all-btn" data-result="{{ result.id }}" data-run="{{ suite.run_id }}"
                         onclick="loadAllDifferencesForSection(this)">Load all for this test</button>
              <span id="load-all-status-{{ result.id }}" style="font-size:0.9em;color:var(--muted)"></span>
            </p>
            <table style="display:none">
              <thead>
                <tr>
                  <th>Mismatch Type</th>
                  <th>Column</th>
                  <th>Row Key Values</th>
                  <th colspan="2">Values ({{ suite.source_env }} → {{ suite.target_env }})</th>
                </tr>
              </thead>
              <tbody id="mismatch-tbody-{{ result.id }}"></tbody>
            </table>
          </details>
          </div>
        {% endif %}
      {% endfor %}
    </div>
```

(This is purely additive: the same rows/attributes the template already produced are preserved unchanged — the only new things are the wrapping `<div class="mismatch-section" ...>`, the `id`s on the count `<span>`, the truncation `<p>`, the `<tbody>`, and the new "Load all for this test" button + status `<span>` inside each truncation paragraph, plus the empty-table skeleton for the zero-stored-rows branch which previously had no `<table>` at all.)

- [ ] **Step 3: Verify the template still renders**

Run:

```bash
python -c "
from jinja2 import Environment, FileSystemLoader

env = Environment(loader=FileSystemLoader('etl_framework/reporting/templates'), autoescape=True)
env.filters['to_local'] = lambda v: ''
tmpl = env.get_template('report.html.j2')

class Mismatch:
    column_name = 'amount'
    mismatch_type = 'value_diff'
    key_values = {'id': 1}
    source_value = '10'
    target_value = '12'
    accepted = False
    accepted_by = None
    accepted_at = None
    accepted_note = None

class TruncatedResult:
    id = 1
    query_name = 'orders'
    total_issues = 10
    mismatches = [Mismatch()]
    mismatch_by_column = {'amount': 10}
    mismatch_by_type = {'value_diff': 10, 'missing_in_target': 0, 'missing_in_source': 0}
    value_mismatch_count = 10
    missing_in_target_count = 0
    missing_in_source_count = 0

class ZeroStoredResult:
    id = 2
    query_name = 'invoices'
    total_issues = 5
    mismatches = []
    mismatch_by_column = {'amount': 5}
    mismatch_by_type = {'value_diff': 5, 'missing_in_target': 0, 'missing_in_source': 0}
    value_mismatch_count = 5
    missing_in_target_count = 0
    missing_in_source_count = 0

class Suite:
    run_id = 'run-1'
    started_at = 't'
    source_env = 'dev'
    target_env = 'qa'
    total_passed = 0
    total_failed = 2
    total_issues = 15
    test_cases = []
    reconciliation_results = [TruncatedResult(), ZeroStoredResult()]

html = tmpl.render(suite=Suite())
assert 'load-all-btn-global' in html
assert 'load-all-btn-1' in html
assert 'load-all-btn-2' in html
assert 'mismatch-tbody-1' in html
assert 'mismatch-tbody-2' in html
print(len(html))
"
```

Expected: prints a number (rendered HTML length), no traceback.

- [ ] **Step 4: Verify the global button is absent when nothing is truncated**

Run the same command as Step 3 but with both results' `total_issues` set equal to `len(mismatches)` (i.e. `TruncatedResult.total_issues = 1` and drop `ZeroStoredResult` from the list, or set its `total_issues = 0`), asserting `'load-all-btn-global' not in html` instead. This is a one-off manual check — no need to keep it as a script file.

- [ ] **Step 5: Commit**

```bash
git add etl_framework/reporting/templates/report.html.j2
git commit -m "feat(report): add load-all-differences markup and stable element ids"
```

---

### Task 5: Report — inline JS to load and inject all differences

**Files:**
- Modify: `etl_framework/reporting/templates/report.html.j2:619-627` (`populateColFilter`, dedupe fix)
- Modify: `etl_framework/reporting/templates/report.html.j2` (new functions, inserted after `wireDifferencesLinks()`)

- [ ] **Step 1: Fix `populateColFilter` to be safely callable more than once**

This function currently runs once at `DOMContentLoaded`. The new "load all" flow calls it again after injecting rows to pick up any new column names — as written it would re-add every existing option a second time (duplicate `<option>`s). Replace (current lines 619-627):

```html
  function populateColFilter() {
    var cols = new Set();
    document.querySelectorAll('tr[data-mismatch]').forEach(function(tr){cols.add(tr.dataset.column);});
    var sel=document.getElementById('filter-col'); if(!sel) return;
    cols.forEach(function(col){
      if(!col) return;
      var opt=document.createElement('option'); opt.value=col; opt.textContent=col; sel.appendChild(opt);
    });
  }
```

with:

```html
  function populateColFilter() {
    var cols = new Set();
    document.querySelectorAll('tr[data-mismatch]').forEach(function(tr){cols.add(tr.dataset.column);});
    var sel=document.getElementById('filter-col'); if(!sel) return;
    var existing = new Set([].slice.call(sel.options).map(function(o){return o.value;}));
    cols.forEach(function(col){
      if(!col || existing.has(col)) return;
      var opt=document.createElement('option'); opt.value=col; opt.textContent=col; sel.appendChild(opt);
      existing.add(col);
    });
  }
```

- [ ] **Step 2: Add the load-all-differences functions**

Insert this block directly after the `wireDifferencesLinks()` function (current lines 708-714, right before `function filterByCol(col){`):

```html
  // ── Load all differences (inline, into this report) ─────────────────────
  function reportAuthToken(){
    var raw = sessionStorage.getItem('etl_token') || '';
    return raw.replace(/^Bearer\s+/i, '').trim();
  }

  function reportAuthHeaders(extra){
    var token = reportAuthToken();
    var headers = extra ? JSON.parse(JSON.stringify(extra)) : {};
    if(token) headers['Authorization'] = 'Bearer ' + token;
    return headers;
  }

  function mismatchTypeBadgeClass(type){
    return (type === 'value_diff' || type === 'value_mismatch') ? 'badge-amber' : 'badge-gray';
  }

  function buildMismatchRow(row, sourceEnv, targetEnv){
    var tr = document.createElement('tr');
    tr.setAttribute('data-mismatch', '');
    tr.dataset.test = row.test_name || '';
    tr.dataset.column = row.column_name || '';
    tr.dataset.type = row.mismatch_type || '';
    tr.dataset.key = typeof row.key_values === 'string' ? row.key_values : JSON.stringify(row.key_values || {});
    tr.dataset.src = row.source_value || '';
    tr.dataset.tgt = row.target_value || '';
    tr.dataset.accepted = 'false';
    tr.innerHTML =
      '<td><span class="badge ' + mismatchTypeBadgeClass(row.mismatch_type) + '">' + escHtml(row.mismatch_type) + '</span></td>' +
      '<td>' + escHtml(row.column_name) + '</td>' +
      '<td style="font-family: monospace; font-size: 0.85em;">' + escHtml(tr.dataset.key) + '</td>' +
      '<td class="diff-values-cell" colspan="2">' +
        '<div class="diff-panels">' +
          '<div class="diff-panel diff-panel-src">' +
            '<span class="diff-panel-label">' + escHtml(sourceEnv) + '</span>' +
            '<span class="diff-panel-val" data-role="src-diff" data-raw="' + escHtml(tr.dataset.src) + '"></span>' +
            '<button class="copy-btn" onclick="copyVal(this, this.closest(\'tr\').dataset.src)" title="Copy source value">⎘</button>' +
          '</div>' +
          '<div class="diff-panel diff-panel-tgt">' +
            '<span class="diff-panel-label">' + escHtml(targetEnv) + '</span>' +
            '<span class="diff-panel-val" data-role="tgt-diff" data-raw="' + escHtml(tr.dataset.tgt) + '"></span>' +
            '<button class="copy-btn" onclick="copyVal(this, this.closest(\'tr\').dataset.tgt)" title="Copy target value">⎘</button>' +
          '</div>' +
        '</div>' +
      '</td>';
    return tr;
  }

  function injectRowsForSection(section, rows){
    var tbody = section.querySelector('tbody[id^="mismatch-tbody-"]');
    if(!tbody) return;
    var table = tbody.closest('table');
    if(table && table.style.display === 'none') table.style.display = '';
    var frag = document.createDocumentFragment();
    rows.forEach(function(row){
      frag.appendChild(buildMismatchRow(row, section.dataset.sourceEnv, section.dataset.targetEnv));
    });
    tbody.innerHTML = '';
    tbody.appendChild(frag);
    var countEl = document.getElementById('mismatch-count-' + section.dataset.resultId);
    if(countEl) countEl.textContent = 'Showing all ' + rows.length + ' of ' + rows.length;
    var noteEl = document.getElementById('truncation-note-' + section.dataset.resultId);
    if(noteEl) noteEl.textContent = 'Showing all ' + rows.length + ' of ' + rows.length + ' differences.';
  }

  function refreshAfterInjection(){
    populateColFilter();
    applyDiff();
    computeAcceptedStats();
    applyFilters();
  }

  function setLoadAllBusy(btn, statusEl, label){
    btn.disabled = true;
    btn.textContent = label;
    if(statusEl) statusEl.textContent = '';
  }

  function setLoadAllFailed(btn, statusEl, message){
    btn.disabled = false;
    btn.textContent = 'Retry';
    if(statusEl) statusEl.textContent = message;
  }

  function setLoadAllDone(btn, statusEl){
    btn.disabled = true;
    btn.textContent = 'Loaded';
    if(statusEl) statusEl.textContent = '';
  }

  function startOrReuseExportJob(runId){
    return fetch(window.location.origin + '/api/runs/' + encodeURIComponent(runId) + '/exports', {
      method: 'POST',
      headers: reportAuthHeaders({'Content-Type': 'application/json'}),
      body: JSON.stringify({format: 'json'}),
    }).then(function(resp){
      if(!resp.ok) throw new Error('Failed to start export job (HTTP ' + resp.status + ')');
      return resp.json();
    });
  }

  function pollExportJobUntilComplete(runId, exportId){
    var attempt = 0;
    function poll(){
      return fetch(window.location.origin + '/api/runs/' + encodeURIComponent(runId) + '/exports/' + encodeURIComponent(exportId), {
        headers: reportAuthHeaders(),
      }).then(function(resp){
        if(!resp.ok) throw new Error('Failed to check export status (HTTP ' + resp.status + ')');
        return resp.json();
      }).then(function(status){
        if(status.status === 'COMPLETED') return status;
        if(status.status === 'FAILED') throw new Error(status.error_message || 'Export job failed');
        attempt++;
        if(attempt >= 240) throw new Error('Export job timed out');
        return new Promise(function(resolve){ setTimeout(resolve, 2000); }).then(poll);
      });
    }
    return poll();
  }

  function fetchExportRows(runId, exportId){
    return fetch(window.location.origin + '/api/runs/' + encodeURIComponent(runId) + '/exports/' + encodeURIComponent(exportId) + '/download', {
      headers: reportAuthHeaders(),
    }).then(function(resp){
      if(!resp.ok) throw new Error('Failed to download export (HTTP ' + resp.status + ')');
      return resp.text();
    }).then(function(text){
      return text.split('\n').filter(function(line){ return line.trim().length; }).map(function(line){
        try { return JSON.parse(line); } catch(e){ return null; }
      }).filter(function(row){ return row !== null; });
    });
  }

  function loadAllDifferencesForSection(btn){
    var section = btn.closest('.mismatch-section');
    var runId = btn.dataset.run;
    var statusEl = document.getElementById('load-all-status-' + btn.dataset.result);
    setLoadAllBusy(btn, statusEl, 'Preparing…');
    var jobExportId;
    startOrReuseExportJob(runId).then(function(job){
      jobExportId = job.export_id;
      return pollExportJobUntilComplete(runId, jobExportId);
    }).then(function(){
      return fetchExportRows(runId, jobExportId);
    }).then(function(rows){
      var mine = rows.filter(function(r){ return r.test_name === section.dataset.queryName; });
      injectRowsForSection(section, mine);
      refreshAfterInjection();
      setLoadAllDone(btn, statusEl);
    }).catch(function(err){
      setLoadAllFailed(btn, statusEl, 'Failed to load all differences: ' + err.message);
    });
  }

  function loadAllDifferencesGlobal(btn){
    var runId = btn.dataset.run;
    var statusEl = document.getElementById('load-all-status-global');
    setLoadAllBusy(btn, statusEl, 'Preparing…');
    var jobExportId;
    startOrReuseExportJob(runId).then(function(job){
      jobExportId = job.export_id;
      return pollExportJobUntilComplete(runId, jobExportId);
    }).then(function(){
      return fetchExportRows(runId, jobExportId);
    }).then(function(rows){
      var byTest = {};
      rows.forEach(function(r){
        (byTest[r.test_name] = byTest[r.test_name] || []).push(r);
      });
      document.querySelectorAll('.mismatch-section[data-result-id]').forEach(function(section){
        var mine = byTest[section.dataset.queryName] || [];
        if(!mine.length) return;
        injectRowsForSection(section, mine);
        var sectionBtn = document.getElementById('load-all-btn-' + section.dataset.resultId);
        var sectionStatus = document.getElementById('load-all-status-' + section.dataset.resultId);
        if(sectionBtn) setLoadAllDone(sectionBtn, sectionStatus);
      });
      refreshAfterInjection();
      setLoadAllDone(btn, statusEl);
    }).catch(function(err){
      setLoadAllFailed(btn, statusEl, 'Failed to load all differences: ' + err.message);
    });
  }

```

Note: `buildMismatchRow` deliberately renders each diff-panel's `<span data-role="src-diff">`/`<span data-role="tgt-diff">` empty — `refreshAfterInjection()` calls `applyDiff()`, which fills every such span (existing and newly injected) from `tr.dataset.src`/`tr.dataset.tgt` via `renderSrc()`/`renderTgt()`. This matches how `applyDiff()` already works for server-rendered rows and avoids duplicating that char-diff logic here.

Also note: the export payload's `source_value`/`target_value`/`key_values` are already-stringified by `DifferenceWriter.write()` (via `_json_text`/`_cell_text` in `api/services/difference_export.py`) for every format, including a compare's original `None` collapsing to `""` — the same fidelity the existing CSV/Parquet exports have. Injected rows therefore render an empty value rather than the "NULL" label server-rendered rows show for true nulls; this is a pre-existing export limitation, not something introduced here.

- [ ] **Step 3: Verify the template still renders**

Run the same Jinja smoke-test command from Task 4 Step 3.
Expected: prints a number, no traceback (confirms the added `<script>` content didn't break Jinja parsing — Jinja treats the script block as literal text outside `{% %}`/`{{ }}` tags, so this mainly catches accidental `{{`/`{%` collisions, e.g. in the JSON.stringify calls, which is why none are used here).

- [ ] **Step 4: Commit**

```bash
git add etl_framework/reporting/templates/report.html.j2
git commit -m "feat(report): implement inline load-all-differences fetch/poll/inject flow"
```

---

### Task 6: Manual verification (browser)

**Files:** none (verification only)

- [ ] **Step 1: Start the dev server**

Use this project's `run` skill (or the project's normal dev-server startup command) to launch the API + frontend.

- [ ] **Step 2: Produce a truncated run**

Launch a compare with a `mismatch_row_limit` lower than the number of mismatches it will find (e.g. set it to 5 against a source/target pair with 20+ differing rows), so at least one test result is truncated.

- [ ] **Step 3: Exercise the report**

1. Open that run's Report tab, load the report.
2. Confirm the "Load all differences (entire run)" button appears near "Expand All"/"Collapse All", and each truncated test's mismatch section shows a "Load all for this test" button next to the existing "open in Differences Explorer" link.
3. Click a single test's "Load all for this test" button. Confirm: button shows "Preparing…", then (once the job completes) the table for that test grows to show every mismatch, the truncation text changes to "Showing all N of N differences.", and the button becomes disabled and reads "Loaded".
4. Use the existing search/column/type filters — confirm they still work correctly against both the original and newly-injected rows.
5. Reload the report (or open a fresh copy) and click the global "Load all differences (entire run)" button instead. Confirm every truncated test's table is populated in one pass and each one's own per-test button also flips to "Loaded".
6. Confirm the "Accepted / Open (shown)" stat card total increases correctly after loading (open rows added by the load count as open).
7. Force a failure (e.g. stop the API mid-poll, or revoke the session token) and confirm the button shows "Retry" with an inline error message rather than a silent failure or unhandled exception in the console.

- [ ] **Step 4: Report results**

Summarize pass/fail for each item above. If anything doesn't work as described, treat it as a bug to fix before considering this plan complete — do not report success without having actually driven the flow in a browser.
