# Run History Report Name Column & Consistent Download Filenames Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a computed "Report Name" column to the Web UI run history page, and make every HTML/CSV/JSON/Parquet report download filename follow that same naming convention.

**Architecture:** A new pure function `report_name_base()` (in `api/services/run_label.py`, alongside the existing `run_display_label()`) derives a filename-safe stem from a run's config name (or environment pair fallback) plus its start timestamp. This is exposed to the frontend as a new `report_name` field on `RunStatusOut`/`RunDetailOut` (computed the same way the existing `label` field is), and reused by `export_filename()` in `api/services/difference_export.py` to build every download's `Content-Disposition` filename, converging four previously-inconsistent download routes on one convention.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy, pytest, Alpine.js (vanilla JS templates, no build step).

---

## Spec Coverage Map

- Spec §1 (report name computation) → Task 1
- Spec §2 (API schema) → Task 3
- Spec §3 (download filename convention, all 3+1 routes, all formats) → Tasks 4, 5
- Spec §4 (frontend history table column) → Task 6
- Spec §5 (frontend download handler fallback) → Task 7
- Spec §6 (testing) → Tasks 1, 2, 3, 4, 5, 8

## File Structure

- Modify `api/services/run_label.py` — add `report_name_base()` / `report_name_base_for()`.
- Modify `api/services/run_report.py` — add `RunReportSnapshot.report_name` property.
- Modify `api/schemas.py` — add `report_name` field to `RunStatusOut`.
- Modify `api/routes/runs.py` — populate `report_name` in `_run_status_out()` and `get_run_detail()`; update `/report`, `/mismatches/download`, `/differences/download`, `/exports/{export_id}/download` to use the shared filename helper.
- Modify `api/services/difference_export.py` — change `export_filename()` to take a `run` object; update its two internal call sites.
- Modify `frontend/partials/tab-history.html` and `frontend/index.html` — add "Report Name" column.
- Modify `frontend/features/compare.js` — update the rarely-hit filename fallback string.
- Modify `tests/unit/test_run_label.py`, `tests/unit/test_difference_export.py` — new/updated unit tests.
- Modify `tests/e2e/04-history.spec.ts` — assert the new column renders.

---

### Task 1: `report_name_base()` helper

**Files:**
- Modify: `api/services/run_label.py`
- Test: `tests/unit/test_run_label.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_run_label.py` (add the `datetime`/`timezone` import at the top alongside the existing `import types`, and append this new class at the end of the file):

```python
from datetime import datetime, timezone
```

```python
class TestReportNameBase:
    STARTED = datetime(2026, 8, 28, 14, 30, 5, tzinfo=timezone.utc)

    def test_uses_config_name_when_present(self):
        from api.services.run_label import report_name_base

        name = report_name_base(
            started_at=self.STARTED,
            source_env="dev",
            target_env="prod",
            config_snapshot={"config_name": "Nightly Recon"},
        )
        assert name == "nightly_recon_2026-08-28_14-30-05"

    def test_falls_back_to_env_pair_when_no_config_name(self):
        from api.services.run_label import report_name_base

        name = report_name_base(started_at=self.STARTED, source_env="dev", target_env="prod")
        assert name == "dev_to_prod_2026-08-28_14-30-05"

    def test_one_sided_environment_still_shows(self):
        from api.services.run_label import report_name_base

        name = report_name_base(started_at=self.STARTED, source_env="dev")
        assert name == "dev_2026-08-28_14-30-05"

    def test_falls_back_to_run_when_nothing_identifies_it(self):
        from api.services.run_label import report_name_base

        assert report_name_base(started_at=self.STARTED) == "run_2026-08-28_14-30-05"

    def test_missing_started_at_still_produces_a_name(self):
        from api.services.run_label import report_name_base

        name = report_name_base(started_at=None, source_env="dev", target_env="prod")
        assert name == "dev_to_prod_unscheduled"

    def test_sanitizes_special_characters_in_config_name(self):
        from api.services.run_label import report_name_base

        name = report_name_base(
            started_at=self.STARTED,
            config_snapshot={"config_name": "Q3 Recon / Sales!!"},
        )
        assert name == "q3_recon_sales_2026-08-28_14-30-05"

    def test_reads_off_a_run_object(self):
        import types
        from api.services.run_label import report_name_base_for

        run = types.SimpleNamespace(
            started_at=self.STARTED,
            source_env="dev",
            target_env="prod",
            config_snapshot={"config_name": "Nightly Recon"},
        )
        assert report_name_base_for(run) == "nightly_recon_2026-08-28_14-30-05"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_run_label.py::TestReportNameBase -v`
Expected: FAIL with `ImportError: cannot import name 'report_name_base'`

- [ ] **Step 3: Implement `report_name_base()` and `report_name_base_for()`**

In `api/services/run_label.py`, change the import line at the top (line 15) from:

```python
from typing import Any
```

to:

```python
import re
from datetime import datetime
from typing import Any
```

Then append this to the end of the file (after `run_display_label_for`):

```python
_SLUG_INVALID = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    """Filesystem/URL-safe token: lowercase, non-alphanumeric runs collapsed to a
    single underscore, no leading/trailing underscore."""
    lowered = str(text or "").strip().lower()
    collapsed = _SLUG_INVALID.sub("_", lowered).strip("_")
    return collapsed or "run"


def report_name_base(
    started_at: Any,
    source_env: Any = None,
    target_env: Any = None,
    config_snapshot: Any = None,
) -> str:
    """Build the report/download name stem, e.g. "nightly_recon_2026-08-28_14-30-05".

    Prefers the saved config's name (what the user actually named the job) and
    falls back to the environment pair when a run has no config (ad-hoc file
    compares). Always suffixed with the run's start time so repeated downloads
    of the same run produce the same name. export_filename() in
    difference_export.py appends a short run id on top of this for on-disk/
    download uniqueness.
    """
    config_name = None
    if isinstance(config_snapshot, dict):
        raw = config_snapshot.get("config_name")
        if raw:
            config_name = str(raw)

    if config_name:
        source = config_name
    else:
        source_part = str(source_env).strip() if source_env else ""
        target_part = str(target_env).strip() if target_env else ""
        if source_part and target_part:
            source = f"{source_part}_to_{target_part}"
        else:
            source = source_part or target_part or "run"

    if isinstance(started_at, datetime):
        timestamp = started_at.strftime("%Y-%m-%d_%H-%M-%S")
    else:
        timestamp = "unscheduled"

    return f"{_slug(source)}_{timestamp}"


def report_name_base_for(run: Any) -> str:
    """Same, read off any object exposing the run's attributes."""
    return report_name_base(
        started_at=getattr(run, "started_at", None),
        source_env=getattr(run, "source_env", None),
        target_env=getattr(run, "target_env", None),
        config_snapshot=getattr(run, "config_snapshot", None),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_run_label.py -v`
Expected: PASS (all tests in the file, old and new)

- [ ] **Step 5: Commit**

```bash
git add api/services/run_label.py tests/unit/test_run_label.py
git commit -m "$(cat <<'EOF'
feat: add report_name_base helper for run download/report naming

Derives a filename-safe stem from a run's config name (or env-pair
fallback) plus its start timestamp, for reuse by the history table
column and download filenames.
EOF
)"
```

---

### Task 2: Expose `report_name` on `RunReportSnapshot`

**Files:**
- Modify: `api/services/run_report.py:182-194` (next to the existing `run_label` property)
- Test: `tests/unit/test_run_label.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_run_label.py`:

```python
def test_snapshot_exposes_the_report_name_for_downloads():
    from api.services.run_report import build_run_report_snapshot

    run = types.SimpleNamespace(
        run_id=RUN_ID,
        status="FAILED",
        started_at=datetime(2026, 8, 28, 14, 30, 5, tzinfo=timezone.utc),
        completed_at=None,
        source_env="dev",
        target_env="prod",
        config_snapshot={"config_name": "Nightly Recon"},
        run_type="reconciliation",
        pair_id=None,
        results=[],
        total_tests=0, passed=0, failed=0, slow=0, error=0,
    )
    snapshot = build_run_report_snapshot(run)

    assert snapshot.report_name == "nightly_recon_2026-08-28_14-30-05"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_run_label.py::test_snapshot_exposes_the_report_name_for_downloads -v`
Expected: FAIL with `AttributeError: 'RunReportSnapshot' object has no attribute 'report_name'`

- [ ] **Step 3: Add the property**

In `api/services/run_report.py`, immediately after the `run_label` property (after line 194, before the `short_run_id` property at line 196), add:

```python
    @property
    def report_name(self) -> str:
        """Download/report name stem, e.g. "nightly_recon_2026-08-28_14-30-05".
        Derived rather than stored -- see api/services/run_label.py."""
        from api.services.run_label import report_name_base

        return report_name_base(
            started_at=self.started_at,
            source_env=self.source_env,
            target_env=self.target_env,
            config_snapshot=self.config_snapshot,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_run_label.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/services/run_report.py tests/unit/test_run_label.py
git commit -m "feat: expose report_name on RunReportSnapshot"
```

---

### Task 3: Add `report_name` to the API schema and populate it

**Files:**
- Modify: `api/schemas.py:386-406` (`RunStatusOut`)
- Modify: `api/routes/runs.py:186-202` (`_run_status_out`), `api/routes/runs.py:1400-1418` (`get_run_detail`)
- Test: `tests/unit/test_difference_export.py` (reuses its TestClient/sqlite scaffolding)

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_difference_export.py` (this file already has the `TestClient` + in-memory-sqlite pattern used below; the same pattern appears in `test_create_export_job_accepts_html_format`):

```python
def test_list_runs_includes_report_name(monkeypatch):
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

    def override_get_db():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_db] = override_get_db
    try:
        with Session(engine) as db:
            raw, _ = TokenRepository(db).create("test-runner")
            run_id = str(uuid.uuid4())
            RunRepository(db).create_run(run_id, "dev", "prod", {"config_name": "Nightly Recon"})

        client = TestClient(app, headers={"Authorization": f"Bearer {raw}"})
        resp = client.get("/api/runs")
        assert resp.status_code == 200
        rows = [r for r in resp.json() if r["run_id"] == run_id]
        assert len(rows) == 1
        assert rows[0]["report_name"].startswith("nightly_recon_")

        detail_resp = client.get(f"/api/runs/{run_id}")
        assert detail_resp.status_code == 200
        assert detail_resp.json()["report_name"].startswith("nightly_recon_")
    finally:
        app.dependency_overrides.pop(get_db, None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_difference_export.py::test_list_runs_includes_report_name -v`
Expected: FAIL with `KeyError: 'report_name'`

- [ ] **Step 3: Add the schema field**

In `api/schemas.py`, change `RunStatusOut` (lines 386-406) from:

```python
class RunStatusOut(BaseModel):
    run_id: str
    # Human-readable handle derived from the run ("file compare · dev → prod ·
    # 00a638ef"). Computed server-side so the UI and the downloadable HTML report
    # cannot drift -- see api/services/run_label.py.
    label: str = ""
    status: str
```

to:

```python
class RunStatusOut(BaseModel):
    run_id: str
    # Human-readable handle derived from the run ("file compare · dev → prod ·
    # 00a638ef"). Computed server-side so the UI and the downloadable HTML report
    # cannot drift -- see api/services/run_label.py.
    label: str = ""
    # Download/report name stem ("nightly_recon_2026-08-28_14-30-05"). Computed
    # the same way as label; export_filename() in difference_export.py appends
    # a short run id on top of this for the actual download filename.
    report_name: str = ""
    status: str
```

- [ ] **Step 4: Populate the field in `_run_status_out()`**

In `api/routes/runs.py`, change `_run_status_out()` (lines 186-202) from:

```python
def _run_status_out(run) -> RunStatusOut:
    snapshot = build_run_report_snapshot(run)
    return RunStatusOut(
        run_id=snapshot.run_id,
        label=snapshot.run_label,
        status=snapshot.status,
```

to:

```python
def _run_status_out(run) -> RunStatusOut:
    snapshot = build_run_report_snapshot(run)
    return RunStatusOut(
        run_id=snapshot.run_id,
        label=snapshot.run_label,
        report_name=snapshot.report_name,
        status=snapshot.status,
```

- [ ] **Step 5: Populate the field in `get_run_detail()`**

In `api/routes/runs.py`, change `get_run_detail()` (lines 1400-1418) from:

```python
    return RunDetailOut(
        run_id=snapshot.run_id,
        status=snapshot.status,
```

to:

```python
    return RunDetailOut(
        run_id=snapshot.run_id,
        report_name=snapshot.report_name,
        status=snapshot.status,
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_difference_export.py::test_list_runs_includes_report_name -v`
Expected: PASS

- [ ] **Step 7: Run the full unit suite for regressions**

Run: `python -m pytest tests/unit/test_run_label.py tests/unit/test_difference_export.py -v`
Expected: PASS (all)

- [ ] **Step 8: Commit**

```bash
git add api/schemas.py api/routes/runs.py tests/unit/test_difference_export.py
git commit -m "$(cat <<'EOF'
feat: add report_name to RunStatusOut/RunDetailOut API responses

Populated the same way as the existing label field, so both the
history table and the run detail view can show a job-identifying
name instead of the bare run id.
EOF
)"
```

**Note for the next engineer:** `RunStatusOut` is also constructed without `label`/`report_name` in three other places (`api/routes/compare.py:40`, `api/routes/runs.py:1336` inside `compare_runs`, `api/routes/selections.py:181`) — those surfaces already show a blank `label` today and are out of scope for this plan; `report_name` will be blank there too, consistent with the existing gap.

---

### Task 4: Change `export_filename()` to take a `run` object

**Files:**
- Modify: `api/services/difference_export.py:245-261` (`export_filename`, `write_stored_differences`), `api/services/difference_export.py:354` (`run_difference_export_job`)
- Test: `tests/unit/test_difference_export.py:62-74,253-258` (update existing tests)

- [ ] **Step 1: Update the existing tests first (they currently pass a bare string)**

In `tests/unit/test_difference_export.py`, add this helper near the top of the file (after the existing imports, before the first test function):

```python
import types
from datetime import datetime, timezone


def _fake_run(run_id="run-1", config_name=None):
    return types.SimpleNamespace(
        run_id=run_id,
        started_at=datetime(2026, 8, 28, 14, 30, 5, tzinfo=timezone.utc),
        source_env="dev",
        target_env="prod",
        config_snapshot={"config_name": config_name} if config_name else None,
    )
```

Then change `test_export_filename_json_uses_jsonl_suffix` and `test_export_filename_csv_and_parquet_unaffected` (lines 62-74) from:

```python
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

to:

```python
def test_export_filename_json_uses_jsonl_suffix():
    from api.services.difference_export import export_filename

    name = export_filename(_fake_run(), "json", "exp-1")
    assert name.endswith(".jsonl")
    assert "dev_to_prod_2026-08-28_14-30-05" in name and "run-1" in name and "exp-1" in name


def test_export_filename_csv_and_parquet_unaffected():
    from api.services.difference_export import export_filename

    assert export_filename(_fake_run(), "csv").endswith(".csv")
    assert export_filename(_fake_run(), "parquet").endswith(".parquet")
```

And change `test_export_filename_html_uses_html_suffix` (lines 253-258) from:

```python
def test_export_filename_html_uses_html_suffix():
    from api.services.difference_export import export_filename

    name = export_filename("run-1", "html", "exp-1")
    assert name.endswith(".html")
    assert "run-1" in name and "exp-1" in name
```

to:

```python
def test_export_filename_html_uses_html_suffix():
    from api.services.difference_export import export_filename

    name = export_filename(_fake_run(), "html", "exp-1")
    assert name.endswith(".html")
    assert "run-1" in name and "exp-1" in name


def test_export_filename_uses_config_name_when_present():
    from api.services.difference_export import export_filename

    name = export_filename(_fake_run(config_name="Nightly Recon"), "csv")
    assert name.startswith("nightly_recon_2026-08-28_14-30-05_run-1")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_difference_export.py -k export_filename -v`
Expected: FAIL — `AttributeError: 'str' object has no attribute 'run_id'` (the old call sites pass a bare run id string; the new tests call the not-yet-updated function with a `SimpleNamespace`, which also fails since the current implementation does `f"all_differences_{run_id}"` treating its first arg as a string, so `_fake_run()` objects render as their repr, not the failure shown above — either way, the assertions on content fail)

- [ ] **Step 3: Update `export_filename()`**

In `api/services/difference_export.py`, add this import near the other local imports at the top of the file (after line 52, next to the existing `etl_framework...` imports):

```python
from api.services.run_label import report_name_base_for, short_run_id
```

Then change `export_filename()` (lines 245-257) from:

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

to:

```python
def export_filename(run: Any, fmt: str, export_id: str | None = None) -> str:
    if fmt == "parquet":
        suffix = "parquet"
    elif fmt == "json":
        suffix = "jsonl"
    elif fmt == "html":
        suffix = "html"
    else:
        suffix = "csv"
    stem = f"{report_name_base_for(run)}_{short_run_id(run.run_id)}"
    if export_id:
        stem += f"_{export_id}"
    return f"{stem}.{suffix}"
```

- [ ] **Step 4: Update the two internal call sites**

In `api/services/difference_export.py`, change `write_stored_differences()` (line 261) from:

```python
    path = export_dir(run.run_id) / export_filename(run.run_id, fmt, f"stored_{uuid.uuid4().hex[:8]}")
```

to:

```python
    path = export_dir(run.run_id) / export_filename(run, fmt, f"stored_{uuid.uuid4().hex[:8]}")
```

And change `run_difference_export_job()` (line 354) from:

```python
        path = export_dir(job.run_id) / export_filename(job.run_id, job.format, job.export_id)
```

to:

```python
        path = export_dir(job.run_id) / export_filename(run, job.format, job.export_id)
```

(`run` is already loaded three lines above this at line 350 — `run = db.query(TestRun).filter(TestRun.run_id == job.run_id).first()`.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_difference_export.py -k export_filename -v`
Expected: PASS

- [ ] **Step 6: Run the full difference-export test file for regressions**

Run: `python -m pytest tests/unit/test_difference_export.py -v`
Expected: PASS (all) — this includes `test_create_export_job_accepts_html_format` and the multi-file recompute tests, which exercise `run_difference_export_job` end-to-end.

- [ ] **Step 7: Commit**

```bash
git add api/services/difference_export.py tests/unit/test_difference_export.py
git commit -m "$(cat <<'EOF'
refactor: export_filename takes a run object, not a bare run_id

Lets export filenames incorporate the run's config name / env pair
and start time via report_name_base_for(), instead of only the run
id. Both on-disk callers already had the run object loaded.
EOF
)"
```

---

### Task 5: Converge the four download routes on the shared filename

**Files:**
- Modify: `api/routes/runs.py:594-598` (`/report`), `api/routes/runs.py:737-757` (`/mismatches/download`), `api/routes/runs.py:788-796` (`/differences/download`), `api/routes/runs.py:848-872` (`/exports/{export_id}/download`)
- Test: `tests/unit/test_difference_export.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_difference_export.py`:

```python
def _seed_completed_run(engine, config_name="Nightly Recon"):
    from etl_framework.repository.repository import RunRepository, TokenRepository

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test-runner")
        run_id = str(uuid.uuid4())
        run = RunRepository(db).create_run(run_id, "dev", "prod", {"config_name": config_name})
        run.status = "PASSED"
        run.started_at = datetime(2026, 8, 28, 14, 30, 5, tzinfo=timezone.utc)
        db.commit()
    return raw, run_id


def test_report_route_uses_report_name_convention(monkeypatch):
    from fastapi.testclient import TestClient

    from api.main import app
    from etl_framework.repository.database import Base, get_db
    from etl_framework.repository import database as _db_module
    import etl_framework.repository.models  # noqa: F401
    from etl_framework.repository.repository import RunRepository, TokenRepository

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    def override_get_db():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_db] = override_get_db
    try:
        raw, run_id = _seed_completed_run(engine)
        client = TestClient(app, headers={"Authorization": f"Bearer {raw}"})

        resp = client.get(f"/api/runs/{run_id}/report")
        assert resp.status_code == 200
        disposition = resp.headers["content-disposition"]
        assert "nightly_recon_2026-08-28_14-30-05" in disposition
        assert disposition.endswith('.html"')

        resp2 = client.get(f"/api/runs/{run_id}/mismatches/download", params={"format": "html"})
        assert resp2.status_code == 200
        assert "nightly_recon_2026-08-28_14-30-05" in resp2.headers["content-disposition"]
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_exports_download_route_uses_report_name_convention(monkeypatch):
    from fastapi.testclient import TestClient

    from api.main import app
    from etl_framework.repository.database import Base, get_db
    from etl_framework.repository import database as _db_module
    import etl_framework.repository.models  # noqa: F401
    from etl_framework.repository.repository import RunRepository, TokenRepository

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    def override_get_db():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_db] = override_get_db
    try:
        raw, run_id = _seed_completed_run(engine)
        client = TestClient(app, headers={"Authorization": f"Bearer {raw}"})

        create_resp = client.post(f"/api/runs/{run_id}/exports", json={"format": "csv"})
        assert create_resp.status_code == 202
        export_id = create_resp.json()["export_id"]

        status_resp = client.get(f"/api/runs/{run_id}/exports/{export_id}")
        assert status_resp.json()["status"] == "COMPLETED"

        download_resp = client.get(f"/api/runs/{run_id}/exports/{export_id}/download")
        assert download_resp.status_code == 200
        assert "nightly_recon_2026-08-28_14-30-05" in download_resp.headers["content-disposition"]
    finally:
        app.dependency_overrides.pop(get_db, None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_difference_export.py -k "report_route_uses or exports_download_route_uses" -v`
Expected: FAIL — `/report` and `/mismatches/download` assertions fail because the header still says `report_<run_id>.html` (or has no header at all for `/report`); the exports-download assertion currently passes already for the run-name part... no — it currently fails too, since today's filename is `all_differences_<run_id>_<export_id>.csv` with no report name in it.

- [ ] **Step 3: Update `/report`**

In `api/routes/runs.py`, change `get_run_report()` (lines 594-598) from:

```python
@router.get("/{run_id}/report", response_class=FileResponse)
def get_run_report(run_id: str, db: Session = Depends(get_session)):
    service = ArtifactService(repository=RunRepository(db))
    report_path = service.generate_html_report(run_id)
    return FileResponse(report_path, media_type="text/html")
```

to:

```python
@router.get("/{run_id}/report", response_class=FileResponse)
def get_run_report(run_id: str, db: Session = Depends(get_session)):
    repo = RunRepository(db)
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    service = ArtifactService(repository=repo)
    report_path = service.generate_html_report(run_id)
    return FileResponse(
        report_path,
        media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="{export_filename(run, "html")}"'},
    )
```

- [ ] **Step 4: Update `/mismatches/download`**

In `api/routes/runs.py`, change the `html` branch of `download_mismatches()` (lines 750-757) from:

```python
    if format == "html":
        service = ArtifactService(repository=repo)
        report_path = service.generate_html_report(run_id)
        return FileResponse(
            report_path,
            media_type="text/html",
            headers={"Content-Disposition": f'attachment; filename="report_{run_id}.html"'},
        )
```

to:

```python
    if format == "html":
        service = ArtifactService(repository=repo)
        report_path = service.generate_html_report(run_id)
        return FileResponse(
            report_path,
            media_type="text/html",
            headers={"Content-Disposition": f'attachment; filename="{export_filename(run, "html")}"'},
        )
```

(`run` is already loaded at line 745 in this function.)

- [ ] **Step 5: Update `/differences/download`**

In `api/routes/runs.py`, change the `FileResponse` in `download_all_differences()` (lines 792-796) from:

```python
    return FileResponse(
        path,
        media_type=media_type_for(fmt),
        headers={"Content-Disposition": f'attachment; filename="{export_filename(run_id, fmt)}"'},
    )
```

to:

```python
    return FileResponse(
        path,
        media_type=media_type_for(fmt),
        headers={"Content-Disposition": f'attachment; filename="{export_filename(run, fmt)}"'},
    )
```

(`run` is already loaded at line 774 in this function.)

- [ ] **Step 6: Update `/exports/{export_id}/download`**

In `api/routes/runs.py`, change `download_difference_export()` (lines 848-872) from:

```python
@router.get("/{run_id}/exports/{export_id}/download")
def download_difference_export(
    run_id: str,
    export_id: str,
    db: Session = Depends(get_session),
):
    from etl_framework.repository.models import DifferenceExportJob

    job = (
        db.query(DifferenceExportJob)
        .filter(DifferenceExportJob.run_id == run_id, DifferenceExportJob.export_id == export_id)
        .first()
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Export job not found")
    if job.status != "COMPLETED" or not job.artifact_path:
        raise HTTPException(status_code=409, detail=f"Export job is {job.status}")
    path = Path(job.artifact_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Export artifact not found")
    return FileResponse(
        path,
        media_type=media_type_for(job.format),
        headers={"Content-Disposition": f'attachment; filename="{export_filename(run_id, job.format, export_id)}"'},
    )
```

to:

```python
@router.get("/{run_id}/exports/{export_id}/download")
def download_difference_export(
    run_id: str,
    export_id: str,
    db: Session = Depends(get_session),
):
    from etl_framework.repository.models import DifferenceExportJob

    job = (
        db.query(DifferenceExportJob)
        .filter(DifferenceExportJob.run_id == run_id, DifferenceExportJob.export_id == export_id)
        .first()
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Export job not found")
    if job.status != "COMPLETED" or not job.artifact_path:
        raise HTTPException(status_code=409, detail=f"Export job is {job.status}")
    path = Path(job.artifact_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Export artifact not found")
    run = RunRepository(db).get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return FileResponse(
        path,
        media_type=media_type_for(job.format),
        headers={"Content-Disposition": f'attachment; filename="{export_filename(run, job.format, export_id)}"'},
    )
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_difference_export.py -k "report_route_uses or exports_download_route_uses" -v`
Expected: PASS

- [ ] **Step 8: Run the full unit suite for regressions**

Run: `python -m pytest tests/unit/test_difference_export.py tests/unit/test_run_label.py tests/unit/test_reporting_generator.py tests/unit/test_artifact_service.py -v`
Expected: PASS (all)

- [ ] **Step 9: Commit**

```bash
git add api/routes/runs.py tests/unit/test_difference_export.py
git commit -m "$(cat <<'EOF'
feat: unify HTML/CSV/JSON/Parquet download filenames on one convention

/report, /mismatches/download, /differences/download, and
/exports/{id}/download previously used two different, run-id-only
filename conventions. All four now use export_filename(run, fmt),
so every downloaded report/export is named after the run's config
(or env pair) and start time, matching the new history table column.
EOF
)"
```

---

### Task 6: Add the "Report Name" column to the history table

**Files:**
- Modify: `frontend/partials/tab-history.html:861-892`
- Modify: `frontend/index.html:3913-3944` (identical mirrored markup)

- [ ] **Step 1: Update `frontend/partials/tab-history.html`**

Change (lines 861-878):

```html
        <table class="data-table" aria-label="history table 7">
          <thead>
            <tr>
              <th scope="col">Run ID</th>
              <th scope="col">Status</th>
              <th scope="col">Environments</th>
              <th scope="col">Started</th>
              <th scope="col">P / F / S</th>
              <th scope="col"></th>
            </tr>
          </thead>
          <tbody>
            <template x-for="run in runs" :key="run.run_id">
              <tr class="cursor-pointer" @click="viewRunDetail(run.run_id)" :data-testid="'history-run-row-' + run.run_id">
                <td class="text-xs text-slate-500">
                  <span x-text="runLabel(run)" :title="run.run_id"></span>
                  <span x-show="run.is_baseline" class="text-amber-500 ml-1" title="Baseline run">★</span>
                </td>
```

to:

```html
        <table class="data-table" aria-label="history table 7">
          <thead>
            <tr>
              <th scope="col">Run ID</th>
              <th scope="col">Report Name</th>
              <th scope="col">Status</th>
              <th scope="col">Environments</th>
              <th scope="col">Started</th>
              <th scope="col">P / F / S</th>
              <th scope="col"></th>
            </tr>
          </thead>
          <tbody>
            <template x-for="run in runs" :key="run.run_id">
              <tr class="cursor-pointer" @click="viewRunDetail(run.run_id)" :data-testid="'history-run-row-' + run.run_id">
                <td class="text-xs text-slate-500">
                  <span x-text="runLabel(run)" :title="run.run_id"></span>
                  <span x-show="run.is_baseline" class="text-amber-500 ml-1" title="Baseline run">★</span>
                </td>
                <td class="text-xs font-mono text-slate-600" x-text="run.report_name" :data-testid="'history-run-report-name-' + run.run_id"></td>
```

(No other line in the row template changes — `Status`, `Environments`, `Started`, `P / F / S`, and the actions cell stay exactly as they were, just shifted right by one column.)

- [ ] **Step 2: Apply the identical change to `frontend/index.html`**

`frontend/index.html` carries the same table markup verbatim at lines 3913-3930. Apply the same two edits there (add `<th scope="col">Report Name</th>` after `<th scope="col">Run ID</th>` in the header, and add the `report_name` `<td>` after the Run ID `<td>` in the row template).

- [ ] **Step 3: Manually verify in the browser**

Run the app locally (see the project's `run` skill or existing dev-server docs), open the Web UI, go to History → Runs, and confirm:
- A "Report Name" column appears between "Run ID" and "Status".
- Its values look like `nightly_recon_2026-08-28_14-30-05` (or `dev_to_prod_...` for a run with no saved config).

- [ ] **Step 4: Commit**

```bash
git add frontend/partials/tab-history.html frontend/index.html
git commit -m "feat: add Report Name column to run history table"
```

---

### Task 7: Update the download fallback filename string

**Files:**
- Modify: `frontend/features/compare.js:1259-1279` (`pollDifferenceExport`)

- [ ] **Step 1: Update the fallback**

In `frontend/features/compare.js`, change (line 1267):

```javascript
          const fallback = `all_differences_${runId}_${exportId}.${ext}`;
```

to:

```javascript
          // Rarely hit -- the server always sets Content-Disposition now (see
          // export_filename in api/services/difference_export.py). This fallback
          // can't compute the full report-name convention client-side (it doesn't
          // have the run's config_snapshot/started_at loaded here), so it just
          // avoids the old, now-inconsistent "all_differences_" prefix.
          const fallback = `report_${runId}_${exportId}.${ext}`;
```

- [ ] **Step 2: Manually verify**

This fallback path only triggers if the server response is missing `Content-Disposition`, which no longer happens after Task 5 — there is no automated test for this string. Confirm by reading the diff that the primary path (reading `disposition`) is unchanged.

- [ ] **Step 3: Commit**

```bash
git add frontend/features/compare.js
git commit -m "docs: clarify and rename the rarely-hit export filename fallback"
```

---

### Task 8: Update the Playwright history spec

**Files:**
- Modify: `tests/e2e/04-history.spec.ts`

- [ ] **Step 1: Add a column assertion**

In `tests/e2e/04-history.spec.ts`, change the test `'run appears in Run History with the expected FAILED status'` (lines 39-46) from:

```typescript
  test('run appears in Run History with the expected FAILED status', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-history"]').click();
    await authedPage.locator('[data-testid="history-subtab-runs"]').click();
    const row = authedPage.locator(`[data-testid="history-run-row-${runId}"]`);
    await expect(row).toBeVisible();
    await expect(row).toContainText('FAILED');
  });
```

to:

```typescript
  test('run appears in Run History with the expected FAILED status', async ({ authedPage }) => {
    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-history"]').click();
    await authedPage.locator('[data-testid="history-subtab-runs"]').click();
    const row = authedPage.locator(`[data-testid="history-run-row-${runId}"]`);
    await expect(row).toBeVisible();
    await expect(row).toContainText('FAILED');
    await expect(row.locator(`[data-testid="history-run-report-name-${runId}"]`))
      .toHaveText(/^\S+_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$/);
  });
```

- [ ] **Step 2: Run the spec**

Run: `rtk proxy npx playwright test tests/e2e/04-history.spec.ts` (per project convention, raw `npx playwright test` output is more reliable than the rtk-wrapped summary for this — see the `--reporter=list` flag if needed)
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/04-history.spec.ts
git commit -m "test: assert Report Name column renders in run history"
```

---

## Final Verification

- [ ] Run the full unit suite: `python -m pytest tests/unit -v`
- [ ] Run the full Playwright history/reports specs: `rtk proxy npx playwright test tests/e2e/04-history.spec.ts tests/e2e/06-reports.spec.ts`
- [ ] Manually download a report via "Download Full HTML Report" in the browser and confirm the saved filename matches the new convention.
