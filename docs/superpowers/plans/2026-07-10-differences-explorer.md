# Differences Explorer & Report Interactivity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add server-side search/filter/pagination to the stored-mismatch API, a run-level insights aggregation endpoint, a new "Differences" frontend tab that browses/searches mismatches live, and small additive interactivity to the static HTML report (accepted/open stat, deep-link into the new tab).

**Architecture:** Extend the existing `RunRepository.list_mismatches` query layer with optional filters (search/column/type/accepted/sort) and a new `count_mismatches`, surfaced via the existing `GET /runs/{run_id}/results/{result_id}/mismatches` endpoint using **response headers** (`X-Total-Count`, `X-Stored-Complete`) rather than changing the JSON body shape — the endpoint already has 6+ existing frontend consumers that expect a bare list, so the body stays a bare list and only becomes additive. A new `GET /runs/{run_id}/mismatches/insights` endpoint aggregates top-columns/type-totals/accepted-vs-open across a run's results, reusing the existing `mismatch_summary`-backed `ReportResult` properties (no full mismatch-table scan needed). The frontend gets a new "Differences" nav tab (Alpine.js `currentView`) with its own fetch helper (`apiPaged`) that reads the new headers, Chart.js bar/doughnut insight cards (reusing the pattern already used for `mismatchChartData`), and a "run full export" banner that calls the **already-implemented** `downloadAllDifferences()` flow. The static report gets two small, additive, same-file changes (no new file dependency, no offline-capability regression): an accepted/open stat card and a deep-link anchor into the new tab (wired client-side via `window.location.origin`, since blob-loaded report documents inherit their creator's origin).

**Deviation from the approved design doc** ([2026-07-10-differences-explorer-design.md](../specs/2026-07-10-differences-explorer-design.md)): the design sketched a `{items, total, stored_complete}` JSON envelope for the mismatches endpoint and a shared `frontend/report-charts.js` module loaded by both the static report and the app. Implementation research found: (1) the endpoint already has 6 existing frontend call sites expecting a bare array — wrapping the body would require updating all of them for no behavioral gain, so pagination metadata moves to response headers instead; (2) the static report is always loaded via a `blob:` URL (`apiBlob()` + `URL.createObjectURL`), never served as a static file, so a `<script src="/report-charts.js">` reference would be fragile across browsers and would break the report's offline/download-and-open use case. The plan below duplicates the small (~2 functions) accepted/open counting logic directly in the report's existing inline script instead of extracting a shared file. Insight charts in the new app tab use Chart.js (already loaded app-wide via `vendor/chart.umd.min.js` and already used for `mismatchChartData`) rather than porting the report's hand-rolled SVG builders, which is more consistent with the rest of the app.

**Tech Stack:** FastAPI + SQLAlchemy (backend), Alpine.js + Chart.js + Tailwind (frontend), Jinja2 (static report), pytest + `TestClient` (tests).

---

### Task 1: Repository — searchable/paginated/sortable mismatches query

**Files:**
- Modify: `etl_framework/repository/repository.py:373-388` (existing `list_mismatches`)
- Test: `tests/unit/test_mismatch_search.py` (new)

- [ ] **Step 1: Write the failing repository tests**

Create `tests/unit/test_mismatch_search.py`:

```python
"""Tests for RunRepository.list_mismatches / count_mismatches search, filter, sort."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.reconciliation.models import MismatchRecord, ReconciliationResult
from etl_framework.runner.state import TestStatus


@pytest.fixture
def db_session():
    from etl_framework.repository.database import Base
    import etl_framework.repository.models  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session_ = sessionmaker(bind=engine)
    with Session_() as db:
        yield db


def _seed(db: Session) -> int:
    """Insert one run + one result + 5 mismatch rows with varied data. Returns result_id."""
    from etl_framework.repository.repository import RunRepository

    repo = RunRepository(db)
    run_id = str(uuid.uuid4())
    repo.create_run(run_id, "dev", "qa", {})
    result = repo.add_test_result(run_id, ReconciliationResult(
        query_name="orders",
        source_env="dev",
        target_env="qa",
        source_row_count=10,
        target_row_count=10,
        matched_count=8,
        missing_in_target_count=1,
        missing_in_source_count=1,
        value_mismatch_count=3,
        mismatches=[],
        status=TestStatus.FAILED,
        executed_at=datetime.now(timezone.utc),
        duration_seconds=1.0,
    ))
    repo.add_mismatch_details(result.id, [
        MismatchRecord(key_values={"id": 1}, column_name="amount", source_value="10", target_value="12", mismatch_type="value_diff"),
        MismatchRecord(key_values={"id": 2}, column_name="amount", source_value="20", target_value="21", mismatch_type="value_diff"),
        MismatchRecord(key_values={"id": 3}, column_name="status", source_value="OPEN", target_value="CLOSED", mismatch_type="value_diff"),
        MismatchRecord(key_values={"id": 4}, column_name=None, source_value=None, target_value=None, mismatch_type="missing_in_target"),
        MismatchRecord(key_values={"id": 5}, column_name=None, source_value=None, target_value=None, mismatch_type="missing_in_source"),
    ])
    return result.id


def test_list_mismatches_filters_by_column(db_session):
    from etl_framework.repository.repository import RunRepository

    result_id = _seed(db_session)
    repo = RunRepository(db_session)

    rows = repo.list_mismatches(result_id=result_id, column="amount")
    assert {r.column_name for r in rows} == {"amount"}
    assert len(rows) == 2


def test_list_mismatches_filters_by_type(db_session):
    from etl_framework.repository.repository import RunRepository

    result_id = _seed(db_session)
    repo = RunRepository(db_session)

    rows = repo.list_mismatches(result_id=result_id, mismatch_type="missing_in_target")
    assert len(rows) == 1
    assert rows[0].mismatch_type == "missing_in_target"


def test_list_mismatches_filters_by_accepted(db_session):
    from etl_framework.repository.repository import RunRepository
    from etl_framework.repository.models import MismatchDetail

    result_id = _seed(db_session)
    first = db_session.query(MismatchDetail).filter(MismatchDetail.test_result_id == result_id).first()
    first.accepted = True
    db_session.commit()

    repo = RunRepository(db_session)
    accepted_rows = repo.list_mismatches(result_id=result_id, accepted=True)
    open_rows = repo.list_mismatches(result_id=result_id, accepted=False)
    assert len(accepted_rows) == 1
    assert len(open_rows) == 4


def test_list_mismatches_search_matches_column_source_target_and_key(db_session):
    from etl_framework.repository.repository import RunRepository

    result_id = _seed(db_session)
    repo = RunRepository(db_session)

    assert len(repo.list_mismatches(result_id=result_id, search="closed")) == 1
    assert len(repo.list_mismatches(result_id=result_id, search="amount")) == 2
    assert len(repo.list_mismatches(result_id=result_id, search='"id": 4')) == 1
    assert len(repo.list_mismatches(result_id=result_id, search="nonexistent-value")) == 0


def test_list_mismatches_sort_by_column(db_session):
    from etl_framework.repository.repository import RunRepository

    result_id = _seed(db_session)
    repo = RunRepository(db_session)

    rows = repo.list_mismatches(result_id=result_id, sort="column")
    columns = [r.column_name for r in rows if r.column_name]
    assert columns == sorted(columns)


def test_list_mismatches_pagination(db_session):
    from etl_framework.repository.repository import RunRepository

    result_id = _seed(db_session)
    repo = RunRepository(db_session)

    page1 = repo.list_mismatches(result_id=result_id, limit=2, offset=0)
    page2 = repo.list_mismatches(result_id=result_id, limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 2
    assert {r.id for r in page1}.isdisjoint({r.id for r in page2})


def test_count_mismatches_respects_same_filters_as_list(db_session):
    from etl_framework.repository.repository import RunRepository

    result_id = _seed(db_session)
    repo = RunRepository(db_session)

    assert repo.count_mismatches(result_id=result_id) == 5
    assert repo.count_mismatches(result_id=result_id, column="amount") == 2
    assert repo.count_mismatches(result_id=result_id, mismatch_type="value_diff") == 3
    assert repo.count_mismatches(result_id=result_id, search="closed") == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_mismatch_search.py -v`
Expected: FAIL with `TypeError: list_mismatches() got an unexpected keyword argument 'column'` (or similar — `count_mismatches` doesn't exist yet either).

- [ ] **Step 3: Implement the filters, sort, and count method**

In `etl_framework/repository/repository.py`, change the import line at the top (line 3):

```python
from sqlalchemy import case, insert, or_, cast, String, func
```

Replace the existing `list_mismatches` method (current lines 373-388) with:

```python
    def _mismatch_base_query(self, result_id: int):
        return self._db.query(MismatchDetail).filter(MismatchDetail.test_result_id == result_id)

    def _apply_mismatch_filters(
        self,
        query,
        *,
        search: str | None = None,
        column: str | None = None,
        mismatch_type: str | None = None,
        accepted: bool | None = None,
    ):
        if column:
            query = query.filter(MismatchDetail.column_name == column)
        if mismatch_type:
            query = query.filter(MismatchDetail.mismatch_type == mismatch_type)
        if accepted is not None:
            query = query.filter(MismatchDetail.accepted == accepted)
        if search:
            like = f"%{search.lower()}%"
            query = query.filter(or_(
                func.lower(MismatchDetail.column_name).like(like),
                func.lower(MismatchDetail.source_value).like(like),
                func.lower(MismatchDetail.target_value).like(like),
                func.lower(cast(MismatchDetail.key_values, String)).like(like),
            ))
        return query

    def list_mismatches(
        self,
        result_id: int,
        limit: int = 100,
        offset: int = 0,
        search: str | None = None,
        column: str | None = None,
        mismatch_type: str | None = None,
        accepted: bool | None = None,
        sort: str = "id",
    ) -> list[MismatchDetail]:
        query = self._apply_mismatch_filters(
            self._mismatch_base_query(result_id),
            search=search, column=column, mismatch_type=mismatch_type, accepted=accepted,
        )
        if sort == "column":
            order = (MismatchDetail.column_name, MismatchDetail.id)
        elif sort == "mismatch_type":
            order = (MismatchDetail.mismatch_type, MismatchDetail.id)
        else:
            mismatch_order = case(
                (MismatchDetail.mismatch_type == "missing_in_target", 0),
                (MismatchDetail.mismatch_type == "missing_in_source", 1),
                else_=2,
            )
            order = (mismatch_order, MismatchDetail.id)
        return query.order_by(*order).offset(offset).limit(limit).all()

    def count_mismatches(
        self,
        result_id: int,
        search: str | None = None,
        column: str | None = None,
        mismatch_type: str | None = None,
        accepted: bool | None = None,
    ) -> int:
        query = self._apply_mismatch_filters(
            self._db.query(func.count(MismatchDetail.id)).filter(MismatchDetail.test_result_id == result_id),
            search=search, column=column, mismatch_type=mismatch_type, accepted=accepted,
        )
        return int(query.scalar() or 0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_mismatch_search.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add -f tests/unit/test_mismatch_search.py
git add etl_framework/repository/repository.py
git commit -m "feat(repo): add search/filter/sort/count to mismatch queries"
```

---

### Task 2: Accepted-vs-open aggregation helper

**Files:**
- Modify: `api/services/difference_export.py`
- Test: covered by Task 5's insights tests (this helper has no independent test file — it's a 6-line grouped-count query exercised end-to-end through the insights endpoint)

- [ ] **Step 1: Add the helper function**

In `api/services/difference_export.py`, add this function right after `stored_completeness_summary` (after line 195, before `def export_dir`):

```python
def accepted_counts(db: Session, run_id: str) -> dict[str, int]:
    rows = (
        db.query(MismatchDetail.accepted, func.count(MismatchDetail.id))
        .join(TestResult, TestResult.id == MismatchDetail.test_result_id)
        .filter(TestResult.run_id == run_id)
        .group_by(MismatchDetail.accepted)
        .all()
    )
    counts = {"accepted": 0, "open": 0}
    for accepted, count in rows:
        counts["accepted" if accepted else "open"] += int(count)
    return counts
```

No new imports needed — `func`, `MismatchDetail`, `TestResult`, and `Session` are already imported at the top of this file.

- [ ] **Step 2: Commit**

```bash
git add api/services/difference_export.py
git commit -m "feat(export): add accepted-vs-open mismatch count aggregation"
```

(This step has no standalone test run — it's verified as part of Task 5. Committing here keeps the diff small and reviewable; Task 5 will fail first if this is wrong.)

---

### Task 3: Schemas — filter enums and insights response types

**Files:**
- Modify: `api/schemas.py`

- [ ] **Step 1: Check for an existing `Enum` import**

Run: `grep -n "^from enum import" api/schemas.py`
If it returns nothing, add `from enum import Enum` near the top of `api/schemas.py` alongside the other stdlib imports (next to the existing `datetime`/`Literal` imports at the top of the file).

- [ ] **Step 2: Add the enums and insight schemas**

Add this block to `api/schemas.py` directly after the `MismatchOut` class (after line 303, i.e. right after its `model_config = {"from_attributes": True}` line):

```python
class MismatchTypeFilter(str, Enum):
    value_diff = "value_diff"
    missing_in_target = "missing_in_target"
    missing_in_source = "missing_in_source"


class MismatchSortField(str, Enum):
    id = "id"
    column = "column"
    mismatch_type = "mismatch_type"


class MismatchColumnInsight(BaseModel):
    column: str
    count: int


class MismatchTestInsight(BaseModel):
    result_id: int
    query_name: str
    total_issues: int
    stored_rows: int
    stored_complete: bool


class RunMismatchInsightsOut(BaseModel):
    run_id: str
    top_columns: list[MismatchColumnInsight] = Field(default_factory=list)
    type_totals: dict[str, int] = Field(default_factory=dict)
    accepted_count: int = 0
    open_count: int = 0
    tests: list[MismatchTestInsight] = Field(default_factory=list)
```

- [ ] **Step 3: Verify the module still imports cleanly**

Run: `python -c "import api.schemas"`
Expected: no output, exit code 0.

- [ ] **Step 4: Commit**

```bash
git add api/schemas.py
git commit -m "feat(schemas): add mismatch filter enums and insights response types"
```

---

### Task 4: Route — extend the mismatches endpoint with filters + headers

**Files:**
- Modify: `api/routes/runs.py:551-579` (existing `list_result_mismatches`)
- Modify: `tests/unit/test_runs_extensions.py` (update 2 existing assertions, add new tests)
- Test: `tests/unit/test_mismatch_search.py` (add endpoint-level `stored_complete` test using a real DB)

- [ ] **Step 1: Write the new/updated failing tests in `test_runs_extensions.py`**

In `tests/unit/test_runs_extensions.py`, replace `test_mismatches_respects_pagination_params` and `test_mismatches_default_pagination_is_100_0` (current lines 143-162) with:

```python
def test_mismatches_respects_pagination_params(client, mock_run_repo):
    run = MagicMock()
    mock_run_repo.get_run.return_value = run
    mock_run_repo.list_mismatches.return_value = []
    mock_run_repo.count_mismatches.return_value = 0

    client.get("/api/runs/r1/results/42/mismatches?limit=25&offset=50")
    mock_run_repo.list_mismatches.assert_called_once_with(
        result_id=42, limit=25, offset=50,
        search=None, column=None, mismatch_type=None, accepted=None, sort="id",
    )


def test_mismatches_default_pagination_is_100_0(client, mock_run_repo):
    run = MagicMock()
    mock_run_repo.get_run.return_value = run
    mock_run_repo.list_mismatches.return_value = []
    mock_run_repo.count_mismatches.return_value = 0

    client.get("/api/runs/r1/results/7/mismatches")
    mock_run_repo.list_mismatches.assert_called_once_with(
        result_id=7, limit=100, offset=0,
        search=None, column=None, mismatch_type=None, accepted=None, sort="id",
    )


def test_mismatches_forwards_filters(client, mock_run_repo):
    run = MagicMock()
    mock_run_repo.get_run.return_value = run
    mock_run_repo.list_mismatches.return_value = []
    mock_run_repo.count_mismatches.return_value = 0

    client.get(
        "/api/runs/r1/results/42/mismatches"
        "?search=foo&column=amount&mismatch_type=value_diff&accepted=true&sort=column"
    )
    mock_run_repo.list_mismatches.assert_called_once_with(
        result_id=42, limit=100, offset=0,
        search="foo", column="amount", mismatch_type="value_diff", accepted=True, sort="column",
    )


def test_mismatches_sets_total_count_header(client, mock_run_repo):
    run = MagicMock()
    mock_run_repo.get_run.return_value = run
    mock_run_repo.list_mismatches.return_value = [_make_mismatch(1)]
    mock_run_repo.count_mismatches.return_value = 42

    resp = client.get("/api/runs/r1/results/42/mismatches")
    assert resp.headers["x-total-count"] == "42"


def test_mismatches_rejects_bad_mismatch_type(client, mock_run_repo):
    mock_run_repo.get_run.return_value = MagicMock()
    resp = client.get("/api/runs/r1/results/1/mismatches?mismatch_type=bogus")
    assert resp.status_code == 422


def test_mismatches_rejects_bad_accepted(client, mock_run_repo):
    mock_run_repo.get_run.return_value = MagicMock()
    resp = client.get("/api/runs/r1/results/1/mismatches?accepted=maybe")
    assert resp.status_code == 422


def test_mismatches_rejects_bad_sort(client, mock_run_repo):
    mock_run_repo.get_run.return_value = MagicMock()
    resp = client.get("/api/runs/r1/results/1/mismatches?sort=bogus")
    assert resp.status_code == 422
```

Also add this real-DB test to `tests/unit/test_mismatch_search.py` (appended at the end of the file — it needs a `TestClient`, not the raw repository, so it imports independently rather than reusing `db_session`):

```python
def test_stored_complete_flag_via_endpoint(monkeypatch):
    """stored_complete should be false when total_issues exceeds stored detail rows."""
    import uuid as _uuid
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine as _create_engine
    from sqlalchemy.orm import sessionmaker as _sessionmaker
    from sqlalchemy.pool import StaticPool as _StaticPool

    from api.main import app
    from etl_framework.repository.database import Base
    from etl_framework.repository import database as _db_module
    import etl_framework.repository.models  # noqa: F401
    from etl_framework.repository.repository import RunRepository, TokenRepository

    engine = _create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=_StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", _sessionmaker(bind=engine))

    with Session(engine) as setup_db:
        raw, _ = TokenRepository(setup_db).create("test-runner")
        repo = RunRepository(setup_db)
        run_id = str(_uuid.uuid4())
        repo.create_run(run_id, "dev", "qa", {})
        result = repo.add_test_result(run_id, ReconciliationResult(
            query_name="orders", source_env="dev", target_env="qa",
            source_row_count=10, target_row_count=10, matched_count=0,
            missing_in_target_count=0, missing_in_source_count=0,
            value_mismatch_count=100, mismatches=[],
            status=TestStatus.FAILED, executed_at=datetime.now(timezone.utc),
            duration_seconds=1.0,
        ))
        repo.add_mismatch_details(result.id, [
            MismatchRecord(key_values={"id": i}, column_name="amount", source_value=str(i),
                           target_value=str(i + 1), mismatch_type="value_diff")
            for i in range(3)
        ])
        result_id = result.id

    client = TestClient(app, headers={"Authorization": f"Bearer {raw}"})
    resp = client.get(f"/api/runs/{run_id}/results/{result_id}/mismatches")
    assert resp.status_code == 200
    assert resp.headers["x-stored-complete"] == "false"
    assert resp.headers["x-total-count"] == "3"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_runs_extensions.py tests/unit/test_mismatch_search.py -v`
Expected: the new/updated tests FAIL (assertion mismatches on call args; header tests fail with `KeyError` since headers don't exist yet).

- [ ] **Step 3: Implement the route changes**

In `api/routes/runs.py`, this file already has a *second*, later `from fastapi.responses import Response` at line 1303 (kept there historically, marked `noqa: E402`) in addition to the `fastapi.responses` import at the top of the file (line 14) that pulls in `PlainTextResponse, FileResponse, HTMLResponse, JSONResponse, StreamingResponse`. Rather than adding a third import path (`from fastapi import ... Response`), add `Response` to the existing top-of-file `fastapi.responses` import so there's one obvious source — update line 14:

```python
from fastapi.responses import PlainTextResponse, FileResponse, HTMLResponse, JSONResponse, StreamingResponse, Response
```

Leave the late import at line 1303 alone — it's a harmless duplicate rebinding of the same name, not worth churning in this change.

Add the new schema names to the `api.schemas` import block (lines 20-42) — insert alphabetically:

```python
    MismatchColumnInsight,
    MismatchSortField,
    MismatchTestInsight,
    MismatchTypeFilter,
```

Add `accepted_counts` and `stored_detail_counts` to the `api.services.difference_export` import block (lines 55-65):

```python
from api.services.difference_export import (
    accepted_counts,
    create_or_reuse_export_job,
    export_filename,
    export_status_out,
    media_type_for,
    run_difference_export_job,
    stored_completeness_summary,
    stored_detail_counts,
    stored_rows_are_complete,
    validate_difference_format,
    write_stored_differences,
)
```

Add the schema imports `RunMismatchInsightsOut` to the same block as the other new schema names above.

Replace the existing `list_result_mismatches` function (current lines 551-579) with:

```python
@router.get("/{run_id}/results/{result_id}/mismatches", response_model=list[MismatchOut])
def list_result_mismatches(
    run_id: str,
    result_id: int,
    response: Response,
    limit: int = 100,
    offset: int = 0,
    search: str | None = None,
    column: str | None = None,
    mismatch_type: MismatchTypeFilter | None = None,
    accepted: bool | None = None,
    sort: MismatchSortField = MismatchSortField.id,
    db: Session = Depends(get_session),
):
    from etl_framework.repository.models import TestResult

    repo = RunRepository(db)
    if repo.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")

    mismatch_type_value = mismatch_type.value if mismatch_type else None
    rows = repo.list_mismatches(
        result_id=result_id,
        limit=limit,
        offset=offset,
        search=search,
        column=column,
        mismatch_type=mismatch_type_value,
        accepted=accepted,
        sort=sort.value,
    )
    total = repo.count_mismatches(
        result_id=result_id,
        search=search,
        column=column,
        mismatch_type=mismatch_type_value,
        accepted=accepted,
    )
    response.headers["X-Total-Count"] = str(total)

    test_result = db.get(TestResult, result_id)
    if test_result is not None and test_result.run_id == run_id:
        stored_total = repo.count_mismatches(result_id=result_id)
        stored_complete = stored_total >= int(test_result.total_issues or 0)
        response.headers["X-Stored-Complete"] = "true" if stored_complete else "false"

    return [
        MismatchOut(
            id=m.id,
            column_name=m.column_name,
            key_values=m.key_values,
            source_value=m.source_value,
            target_value=m.target_value,
            mismatch_type=m.mismatch_type,
            delta=m.delta,
            relative_delta=m.relative_delta,
            accepted=m.accepted,
            accepted_note=m.accepted_note,
            accepted_at=m.accepted_at,
            accepted_by=m.accepted_by,
        )
        for m in rows
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_runs_extensions.py tests/unit/test_mismatch_search.py -v`
Expected: PASS (all mismatches-related tests green)

- [ ] **Step 5: Commit**

```bash
git add api/routes/runs.py
git add -f tests/unit/test_runs_extensions.py tests/unit/test_mismatch_search.py
git commit -m "feat(api): add search/filter/sort query params and pagination headers to mismatches endpoint"
```

---

### Task 5: Route — run-level mismatch insights endpoint

**Files:**
- Modify: `api/routes/runs.py` (new route, add near `list_result_mismatches`)
- Test: `tests/unit/test_mismatch_insights.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_mismatch_insights.py`:

```python
"""Tests for GET /runs/{run_id}/mismatches/insights."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.reconciliation.models import MismatchRecord, ReconciliationResult
from etl_framework.runner.state import TestStatus


@pytest.fixture
def client(monkeypatch):
    from api.main import app
    from etl_framework.repository.database import Base
    from etl_framework.repository import database as _db_module
    import etl_framework.repository.models  # noqa: F401
    from etl_framework.repository.repository import TokenRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test-runner")

    return TestClient(app, headers={"Authorization": f"Bearer {raw}"})


def _seed_two_results(run_id: str):
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.repository import RunRepository
    from etl_framework.repository.models import MismatchDetail

    db = _db_module.SessionLocal()
    try:
        repo = RunRepository(db)
        repo.create_run(run_id, "dev", "qa", {})

        result_a = repo.add_test_result(run_id, ReconciliationResult(
            query_name="orders", source_env="dev", target_env="qa",
            source_row_count=10, target_row_count=10, matched_count=7,
            missing_in_target_count=1, missing_in_source_count=1,
            value_mismatch_count=3, mismatches=[],
            status=TestStatus.FAILED, executed_at=datetime.now(timezone.utc),
            duration_seconds=1.0,
            mismatch_summary={
                "by_column": {"amount": 2, "status": 1},
                "by_type": {"value_diff": 3, "missing_in_target": 1, "missing_in_source": 1},
            },
        ))
        repo.add_mismatch_details(result_a.id, [
            MismatchRecord(key_values={"id": 1}, column_name="amount", source_value="1", target_value="2", mismatch_type="value_diff"),
            MismatchRecord(key_values={"id": 2}, column_name="amount", source_value="1", target_value="2", mismatch_type="value_diff"),
            MismatchRecord(key_values={"id": 3}, column_name="status", source_value="A", target_value="B", mismatch_type="value_diff"),
            MismatchRecord(key_values={"id": 4}, column_name=None, source_value=None, target_value=None, mismatch_type="missing_in_target"),
            MismatchRecord(key_values={"id": 5}, column_name=None, source_value=None, target_value=None, mismatch_type="missing_in_source"),
        ])

        result_b = repo.add_test_result(run_id, ReconciliationResult(
            query_name="invoices", source_env="dev", target_env="qa",
            source_row_count=5, target_row_count=5, matched_count=4,
            missing_in_target_count=0, missing_in_source_count=0,
            value_mismatch_count=1, mismatches=[],
            status=TestStatus.FAILED, executed_at=datetime.now(timezone.utc),
            duration_seconds=0.5,
            mismatch_summary={
                "by_column": {"amount": 1},
                "by_type": {"value_diff": 1, "missing_in_target": 0, "missing_in_source": 0},
            },
        ))
        repo.add_mismatch_details(result_b.id, [
            MismatchRecord(key_values={"id": 1}, column_name="amount", source_value="5", target_value="6", mismatch_type="value_diff"),
        ])

        # Mark one mismatch on result_a as accepted.
        first = (
            db.query(MismatchDetail)
            .filter(MismatchDetail.test_result_id == result_a.id)
            .first()
        )
        first.accepted = True
        db.commit()

        return result_a.id, result_b.id
    finally:
        db.close()


def test_insights_aggregates_across_results(client):
    run_id = str(uuid.uuid4())
    result_a_id, result_b_id = _seed_two_results(run_id)

    resp = client.get(f"/api/runs/{run_id}/mismatches/insights")
    assert resp.status_code == 200
    data = resp.json()

    assert data["run_id"] == run_id
    columns = {c["column"]: c["count"] for c in data["top_columns"]}
    assert columns["amount"] == 3
    assert columns["status"] == 1
    assert data["type_totals"]["value_diff"] == 4
    assert data["type_totals"]["missing_in_target"] == 1
    assert data["type_totals"]["missing_in_source"] == 1
    assert data["accepted_count"] == 1
    assert data["open_count"] == 5

    tests_by_name = {t["query_name"]: t for t in data["tests"]}
    assert tests_by_name["orders"]["result_id"] == result_a_id
    assert tests_by_name["orders"]["total_issues"] == 5
    assert tests_by_name["orders"]["stored_rows"] == 5
    assert tests_by_name["orders"]["stored_complete"] is True
    assert tests_by_name["invoices"]["result_id"] == result_b_id


def test_insights_404_for_unknown_run(client):
    resp = client.get("/api/runs/no-such-run/mismatches/insights")
    assert resp.status_code == 404


def test_insights_empty_run_returns_zeroed_aggregates(client):
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.repository import RunRepository

    run_id = str(uuid.uuid4())
    db = _db_module.SessionLocal()
    try:
        RunRepository(db).create_run(run_id, "dev", "qa", {})
    finally:
        db.close()

    resp = client.get(f"/api/runs/{run_id}/mismatches/insights")
    assert resp.status_code == 200
    data = resp.json()
    assert data["top_columns"] == []
    assert data["accepted_count"] == 0
    assert data["open_count"] == 0
    assert data["tests"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_mismatch_insights.py -v`
Expected: FAIL with 404 (route doesn't exist yet).

- [ ] **Step 3: Implement the insights route**

In `api/routes/runs.py`, add this route directly after the `list_result_mismatches` function from Task 4 (i.e. right before `download_mismatches`):

```python
@router.get("/{run_id}/mismatches/insights", response_model=RunMismatchInsightsOut)
def get_run_mismatch_insights(run_id: str, db: Session = Depends(get_session)):
    repo = RunRepository(db)
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    snapshot = build_run_report_snapshot(run)
    stored_counts = stored_detail_counts(db, run_id)

    column_totals: dict[str, int] = {}
    type_totals: dict[str, int] = {"value_diff": 0, "missing_in_target": 0, "missing_in_source": 0}
    tests: list[MismatchTestInsight] = []

    for result in snapshot.results:
        for column, count in result.mismatch_by_column.items():
            column_totals[column] = column_totals.get(column, 0) + count
        for mtype, count in result.mismatch_by_type.items():
            type_totals[mtype] = type_totals.get(mtype, 0) + count
        stored_rows = stored_counts.get(result.id, 0) if result.id is not None else 0
        tests.append(MismatchTestInsight(
            result_id=result.id or 0,
            query_name=result.query_name,
            total_issues=result.total_issues,
            stored_rows=stored_rows,
            stored_complete=stored_rows >= result.total_issues,
        ))

    top_columns = sorted(column_totals.items(), key=lambda kv: -kv[1])[:10]
    accepted = accepted_counts(db, run_id)

    return RunMismatchInsightsOut(
        run_id=run_id,
        top_columns=[MismatchColumnInsight(column=c, count=n) for c, n in top_columns],
        type_totals=type_totals,
        accepted_count=accepted["accepted"],
        open_count=accepted["open"],
        tests=tests,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_mismatch_insights.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full mismatch-related test suite to check for regressions**

Run: `python -m pytest tests/unit/test_runs_extensions.py tests/unit/test_mismatch_search.py tests/unit/test_mismatch_insights.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add api/routes/runs.py
git add -f tests/unit/test_mismatch_insights.py
git commit -m "feat(api): add run-level mismatch insights endpoint"
```

---

### Task 6: Frontend — apiPaged helper, nav tab, and Differences state

**Files:**
- Modify: `frontend/app.js`

- [ ] **Step 1: Add the `apiPaged` fetch helper**

In `frontend/app.js`, add this function directly after the existing `apiBlob` function (after line 66, before `function triggerDownload`):

```js
async function apiPaged(path) {
  const token = normalizeToken(sessionStorage.getItem('etl_token'));
  const headers = token ? { 'Authorization': 'Bearer ' + token } : {};
  const resp = await fetch(API + path, { headers });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    const error = new Error(apiErrorMessage(err.detail ?? err, resp.statusText));
    error.status = resp.status;
    throw error;
  }
  const items = await resp.json();
  return {
    items,
    total: parseInt(resp.headers.get('x-total-count') || String(items.length), 10),
    storedComplete: resp.headers.get('x-stored-complete') !== 'false',
  };
}
```

- [ ] **Step 2: Add the nav tab entry**

In the `tabs` array (starts at line 158), add a new entry right after the `reports` entry (after line 170's closing `},`, before the `compare` entry):

```js
      { id: 'differences', label: 'Differences',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>' },
```

- [ ] **Step 3: Add Differences tab state**

Add this block right after the existing `reportLogLimit: 500,` line (line 359), before the `// Inline mismatch expand (History detail)` comment:

```js
    // -----------------------------------------------------------
    // Differences Explorer tab
    // -----------------------------------------------------------
    diffRunId: '',
    diffResultId: null,
    diffRunDetail: null,
    diffTestOptions: [],
    diffColumnOptions: [],
    diffSearch: '',
    diffColumn: '',
    diffType: '',
    diffAccepted: '',
    diffSort: 'id',
    diffPage: 0,
    diffPageSize: 100,
    diffRows: [],
    diffTotal: 0,
    diffStoredComplete: true,
    diffLoading: false,
    diffInsights: null,
    diffInsightsLoading: false,
```

- [ ] **Step 4: Verify the file still parses**

Run: `node --check frontend/app.js`
Expected: no output, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add frontend/app.js
git commit -m "feat(frontend): add Differences tab scaffolding and paginated fetch helper"
```

---

### Task 7: Frontend — Differences tab methods

**Files:**
- Modify: `frontend/app.js`

- [ ] **Step 1: Add the deep-link bootstrap call to `init()`**

In the `async init()` method (starts at line 677), add a call as the very last line inside the method body, right before its closing `},` (currently line 709):

```js
      this._applyDeepLink();
    },
```

(This replaces the existing closing `},` on line 709 — the new line is inserted immediately above it.)

- [ ] **Step 2: Add the Differences tab methods**

Add this whole block directly after the `async switchReportView(view) { ... },` method (after line 3005, before `async loadReport()`):

```js
    // ===========================================================
    // DIFFERENCES TAB
    // ===========================================================
    _applyDeepLink() {
      const params = new URLSearchParams(window.location.search);
      const tab = params.get('tab');
      const run = params.get('run');
      const result = params.get('result');
      if (tab === 'differences' && run) {
        this.currentView = 'differences';
        this.selectDifferenceRun(run).then(() => {
          if (result) this.selectDifferenceResult(result);
        });
        window.history.replaceState(null, '', window.location.pathname);
      }
    },

    async selectDifferenceRun(runId) {
      this.diffRunId = runId;
      this.diffResultId = null;
      this.diffRunDetail = null;
      this.diffTestOptions = [];
      this.diffColumnOptions = [];
      this.diffRows = [];
      this.diffTotal = 0;
      this.diffPage = 0;
      this.diffInsights = null;
      if (!runId) return;
      try {
        const run = await api('GET', `/api/runs/${runId}`);
        this.diffRunDetail = run;
        this.diffTestOptions = (run.results || []).map(r => ({
          id: r.id,
          query_name: r.query_name,
          total_issues: (r.value_mismatch_count || 0) + (r.missing_in_target_count || 0) + (r.missing_in_source_count || 0),
        }));
      } catch (e) {
        this.toast('error', 'Failed to load run', e.message);
      }
      await this.loadDifferenceInsights();
    },

    async loadDifferenceInsights() {
      if (!this.diffRunId) return;
      this.diffInsightsLoading = true;
      try {
        this.diffInsights = await api('GET', `/api/runs/${this.diffRunId}/mismatches/insights`);
      } catch (e) {
        this.diffInsights = null;
        this.toast('error', 'Failed to load insights', e.message);
      } finally {
        this.diffInsightsLoading = false;
        this.$nextTick(() => this._renderDifferenceCharts());
      }
    },

    async selectDifferenceResult(resultId) {
      this.diffResultId = resultId ? Number(resultId) : null;
      this.diffPage = 0;
      this.diffColumnOptions = [];
      if (!this.diffResultId) { this.diffRows = []; this.diffTotal = 0; return; }
      const result = (this.diffRunDetail?.results || []).find(r => r.id === this.diffResultId);
      this.diffColumnOptions = (result?.column_stats || []).map(s => s.column);
      await this.fetchDifferenceRows();
    },

    differenceQueryString() {
      const params = new URLSearchParams();
      params.set('limit', String(this.diffPageSize));
      params.set('offset', String(this.diffPage * this.diffPageSize));
      if (this.diffSearch) params.set('search', this.diffSearch);
      if (this.diffColumn) params.set('column', this.diffColumn);
      if (this.diffType) params.set('mismatch_type', this.diffType);
      if (this.diffAccepted) params.set('accepted', this.diffAccepted);
      if (this.diffSort) params.set('sort', this.diffSort);
      return params.toString();
    },

    async fetchDifferenceRows() {
      if (!this.diffRunId || !this.diffResultId) return;
      this.diffLoading = true;
      try {
        const { items, total, storedComplete } = await apiPaged(
          `/api/runs/${this.diffRunId}/results/${this.diffResultId}/mismatches?${this.differenceQueryString()}`);
        this.diffRows = items;
        this.diffTotal = total;
        this.diffStoredComplete = storedComplete;
      } catch (e) {
        this.toast('error', 'Failed to load differences', e.message);
      } finally {
        this.diffLoading = false;
      }
    },

    applyDifferenceFilters() {
      this.diffPage = 0;
      this.fetchDifferenceRows();
    },

    clearDifferenceFilters() {
      this.diffSearch = ''; this.diffColumn = ''; this.diffType = ''; this.diffAccepted = ''; this.diffSort = 'id';
      this.applyDifferenceFilters();
    },

    differenceTotalPages() {
      return this.diffTotal > 0 ? Math.ceil(this.diffTotal / this.diffPageSize) : 1;
    },

    nextDifferencePage() {
      if ((this.diffPage + 1) * this.diffPageSize >= this.diffTotal) return;
      this.diffPage++;
      this.fetchDifferenceRows();
    },

    prevDifferencePage() {
      if (this.diffPage === 0) return;
      this.diffPage--;
      this.fetchDifferenceRows();
    },

    _renderDifferenceCharts() {
      if (typeof Chart === 'undefined' || !this.diffInsights) return;
      const colCanvas = document.getElementById('diffColumnsChart');
      if (colCanvas) {
        if (this._diffColumnsChartInstance) { this._diffColumnsChartInstance.destroy(); this._diffColumnsChartInstance = null; }
        const cols = this.diffInsights.top_columns || [];
        this._diffColumnsChartInstance = new Chart(colCanvas, {
          type: 'bar',
          data: {
            labels: cols.map(c => c.column),
            datasets: [{ label: 'Mismatches', data: cols.map(c => c.count), backgroundColor: '#fb7185', borderColor: '#0d0f12', borderWidth: 1 }],
          },
          options: {
            responsive: true,
            plugins: { legend: { display: false } },
            scales: {
              x: { ticks: { color: '#94a3b8' }, grid: { color: '#1e2533' } },
              y: { ticks: { color: '#94a3b8' }, grid: { color: '#1e2533' } },
            },
          },
        });
      }
      const typeCanvas = document.getElementById('diffTypeChart');
      if (typeCanvas) {
        if (this._diffTypeChartInstance) { this._diffTypeChartInstance.destroy(); this._diffTypeChartInstance = null; }
        const totals = this.diffInsights.type_totals || {};
        this._diffTypeChartInstance = new Chart(typeCanvas, {
          type: 'doughnut',
          data: {
            labels: ['Value diff', 'Missing →', 'Missing ←'],
            datasets: [{
              data: [totals.value_diff || 0, totals.missing_in_target || 0, totals.missing_in_source || 0],
              backgroundColor: ['#fbbf24', '#38bdf8', '#a78bfa'],
            }],
          },
          options: { responsive: true, plugins: { legend: { position: 'bottom', labels: { color: '#94a3b8' } } } },
        });
      }
    },
```

- [ ] **Step 3: Verify the file still parses**

Run: `node --check frontend/app.js`
Expected: no output, exit code 0.

- [ ] **Step 4: Commit**

```bash
git add frontend/app.js
git commit -m "feat(frontend): implement Differences tab search/pagination/insights logic"
```

---

### Task 8: Frontend — Differences tab markup

**Files:**
- Modify: `frontend/index.html`

- [ ] **Step 1: Insert the new tab section**

In `frontend/index.html`, insert this whole block right after the Reports tab's closing `</div>` (currently line 3158), before the `<!-- ==== TAB 7 - COMPARE ==== -->` comment (currently line 3161):

```html

<!-- ====================================================================
     TAB - DIFFERENCES EXPLORER
     ==================================================================== -->
<div x-show="currentView === 'differences'" x-cloak>
  <div class="section-header">
    <div>
      <div class="section-title">Differences Explorer</div>
      <div class="section-sub">Search and browse stored mismatch rows across a run, with insights</div>
    </div>
  </div>

  <div class="card mb-4">
    <div class="font-semibold text-slate-700 mb-3">Select a Run &amp; Test</div>
    <div class="flex gap-3 flex-wrap">
      <select x-model="diffRunId" @change="selectDifferenceRun(diffRunId)" class="field-input field-select flex-1">
        <option value="">— Choose run —</option>
        <template x-for="run in runs" :key="run.run_id">
          <option :value="run.run_id" x-text="run.run_id.substring(0,8) + '… [' + run.status + ']'"></option>
        </template>
      </select>
      <select x-model="diffResultId" @change="selectDifferenceResult(diffResultId)" :disabled="!diffTestOptions.length" class="field-input field-select flex-1">
        <option value="">— Choose test —</option>
        <template x-for="t in diffTestOptions" :key="t.id">
          <option :value="t.id" x-text="t.query_name + ' (' + t.total_issues + ' issues)'"></option>
        </template>
      </select>
    </div>
  </div>

  <template x-if="diffInsights">
    <div class="space-y-4 mb-4">
      <div class="metric-grid">
        <div class="metric-card metric-green"><div class="metric-label">Accepted</div><div class="metric-value" x-text="diffInsights.accepted_count"></div></div>
        <div class="metric-card metric-rose"><div class="metric-label">Open</div><div class="metric-value" x-text="diffInsights.open_count"></div></div>
      </div>
      <div class="flex gap-3 flex-wrap">
        <div class="card flex-1" style="min-width:280px">
          <div class="font-semibold text-slate-700 mb-2">Top Columns by Mismatch Count</div>
          <canvas id="diffColumnsChart" height="160"></canvas>
        </div>
        <div class="card flex-1" style="min-width:280px">
          <div class="font-semibold text-slate-700 mb-2">Mismatch Type Breakdown</div>
          <canvas id="diffTypeChart" height="160"></canvas>
        </div>
      </div>
    </div>
  </template>

  <template x-if="diffResultId">
    <div>
      <template x-if="!diffStoredComplete">
        <div class="card mb-4" style="border-left:3px solid #fbbf24">
          <div class="flex items-center justify-between gap-3 flex-wrap">
            <div>Showing <strong x-text="diffTotal"></strong> stored rows for this test — more differences exist than are stored. Run a full export for the rest.</div>
            <button class="btn-secondary btn-sm" @click="downloadAllDifferences(diffRunId, 'csv')" :disabled="isDifferenceExportBusy(diffRunId, 'csv')">
              <span x-text="differenceExportLabel(diffRunId, 'csv')"></span>
            </button>
          </div>
        </div>
      </template>

      <div class="card mb-4">
        <div class="flex gap-2 flex-wrap items-center">
          <input x-model="diffSearch" @input.debounce.300ms="applyDifferenceFilters()" class="field-input flex-1" placeholder="Search key values, column, source/target…" style="min-width:220px">
          <select x-model="diffColumn" @change="applyDifferenceFilters()" class="field-input field-select">
            <option value="">All columns</option>
            <template x-for="c in diffColumnOptions" :key="c"><option :value="c" x-text="c"></option></template>
          </select>
          <select x-model="diffType" @change="applyDifferenceFilters()" class="field-input field-select">
            <option value="">All types</option>
            <option value="value_diff">Value diff</option>
            <option value="missing_in_target">Missing →</option>
            <option value="missing_in_source">Missing ←</option>
          </select>
          <select x-model="diffAccepted" @change="applyDifferenceFilters()" class="field-input field-select">
            <option value="">Accepted + open</option>
            <option value="true">Accepted only</option>
            <option value="false">Open only</option>
          </select>
          <select x-model="diffSort" @change="applyDifferenceFilters()" class="field-input field-select">
            <option value="id">Default order</option>
            <option value="column">Sort by column</option>
            <option value="mismatch_type">Sort by type</option>
          </select>
          <button class="btn-secondary btn-sm" @click="clearDifferenceFilters()">✕ Clear</button>
          <span class="text-xs text-muted ml-auto" x-text="diffTotal + ' matching rows'"></span>
        </div>
      </div>

      <div class="card overflow-hidden p-0">
        <table class="data-table">
          <thead><tr><th>Type</th><th>Column</th><th>Key Values</th><th>Source</th><th>Target</th><th>Status</th></tr></thead>
          <tbody>
            <template x-if="diffLoading">
              <tr><td colspan="6" class="text-center text-muted py-4">Loading…</td></tr>
            </template>
            <template x-if="!diffLoading && diffRows.length === 0">
              <tr><td colspan="6" class="text-center text-muted py-4">No mismatches match the current filters.</td></tr>
            </template>
            <template x-for="m in diffRows" :key="m.id">
              <tr>
                <td><span class="badge" :class="m.mismatch_type === 'value_diff' ? 'badge-amber' : m.mismatch_type === 'missing_in_target' ? 'badge-sky' : 'badge-purple'" x-text="m.mismatch_type"></span></td>
                <td class="font-mono text-xs" x-text="m.column_name || '—'"></td>
                <td class="font-mono text-xs" x-text="JSON.stringify(m.key_values)"></td>
                <td class="font-mono text-xs" x-text="m.source_value ?? 'NULL'"></td>
                <td class="font-mono text-xs" x-text="m.target_value ?? 'NULL'"></td>
                <td><span class="badge" :class="m.accepted ? 'badge-green' : 'badge-gray'" x-text="m.accepted ? 'Accepted' : 'Open'"></span></td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>

      <div class="flex items-center justify-between gap-3 mt-3">
        <button class="btn-secondary btn-sm" @click="prevDifferencePage()" :disabled="diffPage === 0">← Prev</button>
        <span class="text-xs text-muted" x-text="'Page ' + (diffPage + 1) + ' of ' + differenceTotalPages()"></span>
        <button class="btn-secondary btn-sm" @click="nextDifferencePage()" :disabled="(diffPage + 1) * diffPageSize >= diffTotal">Next →</button>
      </div>
    </div>
  </template>

  <template x-if="!diffResultId && diffRunId">
    <div class="card empty-state">
      <div class="empty-state-title">Select a test to browse its differences</div>
    </div>
  </template>
  <template x-if="!diffRunId">
    <div class="card empty-state">
      <div class="empty-state-icon">🔍</div>
      <div class="empty-state-title">Select a run to explore differences</div>
    </div>
  </template>
</div>

```

- [ ] **Step 2: Commit**

```bash
git add frontend/index.html
git commit -m "feat(frontend): add Differences Explorer tab markup"
```

---

### Task 9: Static report — accepted/open stat card

**Files:**
- Modify: `etl_framework/reporting/templates/report.html.j2`

- [ ] **Step 1: Add a 5th summary stat card**

In `report.html.j2`, the summary stats are a flex row of 4 cards (Passed/Failed/Mismatches/Duration) at lines 206-223. Add a 5th card immediately after the Duration card (after the `</div>` that closes the Duration card, i.e. right after line 222's `</div>`, still inside the outer flex container that closes at line 223):

```html
        <div style="flex:1;min-width:120px;background:rgba(52,211,153,0.08);border:1px solid rgba(52,211,153,0.25);border-radius:8px;padding:12px 16px">
          <div id="stat-accepted" style="font-size:1.8em;font-weight:700;color:#86efac">0 / 0</div>
          <div style="font-size:0.8em;color:var(--muted);text-transform:uppercase;letter-spacing:0.05em">Accepted / Open (shown)</div>
        </div>
```

- [ ] **Step 2: Tag mismatch rows with their accepted status**

The mismatch `<tr data-mismatch ...>` element (lines 384-390) already carries several `data-*` attributes. Add one more, `data-accepted`, to that existing attribute list:

```html
                <tr data-mismatch
                    data-test="{{ result.query_name }}"
                    data-column="{{ mm.column_name }}"
                    data-type="{{ mm.mismatch_type }}"
                    data-key="{{ mm.key_values | tojson }}"
                    data-src="{{ mm.source_value if mm.source_value is not none else '' }}"
                    data-tgt="{{ mm.target_value if mm.target_value is not none else '' }}"
                    data-accepted="{{ 'true' if mm.accepted else 'false' }}">
```

- [ ] **Step 3: Add the stat-computing function and wire it into DOMContentLoaded**

Add this function directly after `buildHeatmap()`'s closing brace and before `function filterByCol(col)` (i.e. right after line 690's closing `}`):

```js
  function computeAcceptedStats(){
    var accepted=0, open=0;
    document.querySelectorAll('tr[data-mismatch]').forEach(function(tr){
      if(tr.dataset.accepted==='true') accepted++; else open++;
    });
    var el=document.getElementById('stat-accepted');
    if(el) el.textContent = accepted + ' / ' + open;
  }
```

In the `DOMContentLoaded` listener (lines 763-772), add a call to it alongside the other stat builders:

```js
  document.addEventListener('DOMContentLoaded', function(){
    populateColFilter();
    applyDiff();
    buildHeatmap();
    buildDonut();
    computeAcceptedStats();
    fillDuration();
    buildNavList();
    var rows=document.querySelectorAll('tr[data-mismatch]').length;
    updateFilterCount(rows, rows);
  });
```

- [ ] **Step 4: Manually verify the template still renders**

Run: `python -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('etl_framework/reporting/templates'), autoescape=True)
env.filters['to_local'] = lambda v: ''
tmpl = env.get_template('report.html.j2')
class Suite:
    run_id='x'; started_at='t'; source_env='dev'; target_env='qa'
    total_passed=1; total_failed=0; total_issues=0
    test_cases=[]; reconciliation_results=[]
print(len(tmpl.render(suite=Suite())))
"`
Expected: prints a number (rendered HTML length), no traceback.

- [ ] **Step 5: Commit**

```bash
git add etl_framework/reporting/templates/report.html.j2
git commit -m "feat(report): add accepted/open stat card to generated HTML report"
```

---

### Task 10: Static report — deep-link into the Differences Explorer

**Files:**
- Modify: `etl_framework/reporting/templates/report.html.j2`

- [ ] **Step 1: Add the deep-link anchor to the truncated-mismatches note**

There are two places in the template where a test's mismatches are known to be truncated: the main truncation note (lines 368-371) and the "0 detail rows stored" branch (lines 423-427). Update both.

Replace (lines 368-371):

```html
            {% if total_mismatches > shown_mismatches %}
            <p style="margin:6px 0 12px;color:var(--muted);font-size:0.85em">
              {{ shown_mismatches }} of {{ total_mismatches }} rows shown; download the full differences export for all differences.
            </p>
            {% endif %}
```

with:

```html
            {% if total_mismatches > shown_mismatches %}
            <p style="margin:6px 0 12px;color:var(--muted);font-size:0.85em">
              {{ shown_mismatches }} of {{ total_mismatches }} rows shown; download the full differences export for all differences,
              or <a href="#" data-differences-link data-run="{{ suite.run_id }}" data-result="{{ result.id }}" style="color:var(--accent)">open in Differences Explorer &#8599;</a>.
            </p>
            {% endif %}
```

Replace (lines 423-427):

```html
        {% elif result.total_issues > 0 %}
          <details>
            <summary>{{ result.query_name }} Mismatches (0 detail rows stored of {{ result.total_issues }})</summary>
            <p style="margin:6px 0;color:var(--muted);font-size:0.9em">
              0 of {{ result.total_issues }} rows shown; download the full differences export for all differences.
            </p>
          </details>
```

with:

```html
        {% elif result.total_issues > 0 %}
          <details>
            <summary>{{ result.query_name }} Mismatches (0 detail rows stored of {{ result.total_issues }})</summary>
            <p style="margin:6px 0;color:var(--muted);font-size:0.9em">
              0 of {{ result.total_issues }} rows shown; download the full differences export for all differences,
              or <a href="#" data-differences-link data-run="{{ suite.run_id }}" data-result="{{ result.id }}" style="color:var(--accent)">open in Differences Explorer &#8599;</a>.
            </p>
          </details>
```

- [ ] **Step 2: Wire the link hrefs client-side**

Add this function directly after `computeAcceptedStats()` (added in Task 9), before `function filterByCol(col)`:

```js
  function wireDifferencesLinks(){
    document.querySelectorAll('a[data-differences-link]').forEach(function(a){
      var run=a.dataset.run, result=a.dataset.result;
      a.href = window.location.origin + '/?tab=differences&run=' + encodeURIComponent(run) + '&result=' + encodeURIComponent(result);
      a.target = '_top';
    });
  }
```

Add a call to it in `DOMContentLoaded`, alongside `computeAcceptedStats()`:

```js
  document.addEventListener('DOMContentLoaded', function(){
    populateColFilter();
    applyDiff();
    buildHeatmap();
    buildDonut();
    computeAcceptedStats();
    wireDifferencesLinks();
    fillDuration();
    buildNavList();
    var rows=document.querySelectorAll('tr[data-mismatch]').length;
    updateFilterCount(rows, rows);
  });
```

(This replaces the `DOMContentLoaded` block written in Task 9 Step 3 — the two tasks touch the same block, so after this task it should contain both `computeAcceptedStats()` and `wireDifferencesLinks()`.)

- [ ] **Step 3: Manually verify the template still renders**

Run the same Jinja smoke-test command as Task 9 Step 4.
Expected: prints a number, no traceback.

- [ ] **Step 4: Commit**

```bash
git add etl_framework/reporting/templates/report.html.j2
git commit -m "feat(report): deep-link truncated mismatch sections into the Differences Explorer"
```

---

### Task 11: Full backend regression check

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend unit test suite**

Run: `python -m pytest tests/unit -q`
Expected: all tests pass (no regressions from Tasks 1-5, 9-10). If any pre-existing failures are unrelated to this change (check by running `git stash` and re-running), note them but don't attempt to fix — out of scope.

- [ ] **Step 2: Fix any regressions found**

If a test fails that touches `list_mismatches`, `count_mismatches`, `mismatches` endpoint, or `report.html.j2` rendering, re-read the failure, and fix it in the relevant file from Tasks 1-5/9-10 before proceeding. Do not skip or delete failing tests.

---

### Task 12: Manual verification (frontend)

**Files:** none (verification only)

- [ ] **Step 1: Start the dev server**

Use this project's `run` skill (or the project's normal dev-server startup command) to launch the API + frontend.

- [ ] **Step 2: Exercise the Differences tab**

In a browser:
1. Trigger or pick an existing completed run with mismatches.
2. Open the "Differences" nav tab.
3. Select the run, then a test — confirm the insights cards (Accepted/Open counts, top-columns bar chart, type-breakdown doughnut) render and the mismatch table populates.
4. Type into the search box — confirm the table re-queries and narrows (check Network tab: request hits `/api/runs/{id}/results/{id}/mismatches?search=...` and a fresh `X-Total-Count` header comes back).
5. Change the column/type/accepted/sort selects — confirm each re-queries.
6. Click "Clear" — confirm filters reset and the full set reloads.
7. If total rows exceed one page, click Next/Prev — confirm the page changes and rows differ.
8. If a run/test has stored rows below its total issue count, confirm the truncation banner appears and its "Run export" button drives the existing `downloadAllDifferences` flow to completion (CSV downloads).

- [ ] **Step 3: Exercise the static report additions**

1. Open the Report tab for a run with mismatches, load the report.
2. Confirm the new "Accepted / Open (shown)" stat card appears in the summary row with a sensible count.
3. For a test whose mismatches are truncated (or force one via a low `mismatch_row_limit` setting), confirm the "open in Differences Explorer ↗" link appears next to the existing download note, and clicking it navigates the top-level app to the Differences tab with the correct run and test preselected.

- [ ] **Step 4: Report results**

Summarize pass/fail for each item above. If anything doesn't work as described, treat it as a bug to fix before considering this plan complete — do not report success without having actually driven the flow in a browser.
