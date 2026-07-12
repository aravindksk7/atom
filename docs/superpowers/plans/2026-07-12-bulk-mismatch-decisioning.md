# Bulk Mismatch Decisioning on Filtered/All Rows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user accept or reject every mismatch matching the Differences Explorer's current search/column/type/status filters (or all rows, when no filter is set) in one action, on both the Differences Explorer tab and the Compare-tab mismatch drawer, and give mismatches a real `rejected` state (today `m.rejected` is referenced in the frontend but no such backend field exists).

**Architecture:** Add `rejected`/`rejected_note`/`rejected_at`/`rejected_by` columns to `MismatchDetail`, mirroring the existing `accepted_*` columns, with a mutual-exclusion invariant enforced in the repository layer. Add a single `bulk_decide_mismatches` repository method that reuses the existing `_apply_mismatch_filters` helper — the same filter logic already powering `list_mismatches`/`count_mismatches` — so "filtered" and "all rows" are the same code path (empty filters = all rows). Expose it via one new route, `POST /{run_id}/results/{result_id}/mismatches/bulk-decide`, plus a single-row mirror of the existing `/accept` endpoint: `/reject`. Both frontend surfaces (Differences Explorer tab, mismatch drawer) call the same bulk-decide endpoint through one shared confirm-modal component (`mismatchDecisionForm`) instead of each inventing their own.

**Tech Stack:** FastAPI + SQLAlchemy (backend), Alpine.js + vanilla fetch (frontend), pytest + `TestClient` (tests). Design doc: [docs/superpowers/specs/2026-07-12-bulk-mismatch-decisioning-design.md](../specs/2026-07-12-bulk-mismatch-decisioning-design.md).

---

### Task 1: Data model — `rejected` columns + sqlite migration shim

**Files:**
- Modify: `etl_framework/repository/models.py:182-200` (`MismatchDetail`)
- Modify: `etl_framework/repository/database.py:63-75` (`_ensure_compare_columns`)

- [ ] **Step 1: Add the columns to the ORM model**

In `etl_framework/repository/models.py`, in the `MismatchDetail` class, replace:

```python
    accepted      = Column(Boolean, nullable=False, default=False)
    accepted_note = Column(Text, nullable=True)
    accepted_at   = Column(DateTime(timezone=True), nullable=True)
    accepted_by   = Column(String(255), nullable=True)
```

with:

```python
    accepted      = Column(Boolean, nullable=False, default=False)
    accepted_note = Column(Text, nullable=True)
    accepted_at   = Column(DateTime(timezone=True), nullable=True)
    accepted_by   = Column(String(255), nullable=True)
    rejected      = Column(Boolean, nullable=False, default=False)
    rejected_note = Column(Text, nullable=True)
    rejected_at   = Column(DateTime(timezone=True), nullable=True)
    rejected_by   = Column(String(255), nullable=True)
```

- [ ] **Step 2: Add the sqlite backward-compat migration**

In `etl_framework/repository/database.py`, in `_ensure_compare_columns`, directly after the existing block:

```python
        if "accepted_by" not in mismatch_cols:
            conn.execute(text(
                "ALTER TABLE mismatch_details ADD COLUMN accepted_by VARCHAR(255)"
            ))
```

add:

```python
        if "rejected" not in mismatch_cols:
            conn.execute(text(
                "ALTER TABLE mismatch_details "
                "ADD COLUMN rejected BOOLEAN NOT NULL DEFAULT 0"
            ))
        if "rejected_note" not in mismatch_cols:
            conn.execute(text("ALTER TABLE mismatch_details ADD COLUMN rejected_note TEXT"))
        if "rejected_at" not in mismatch_cols:
            conn.execute(text("ALTER TABLE mismatch_details ADD COLUMN rejected_at DATETIME"))
        if "rejected_by" not in mismatch_cols:
            conn.execute(text(
                "ALTER TABLE mismatch_details ADD COLUMN rejected_by VARCHAR(255)"
            ))
```

- [ ] **Step 3: Verify the app still boots and migrates a fresh in-memory DB cleanly**

Run:

```bash
python -c "
from sqlalchemy import create_engine, inspect
from etl_framework.repository.database import Base, init_db
import etl_framework.repository.database as dbmod
engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False})
dbmod.engine = engine
init_db()
cols = {c['name'] for c in inspect(engine).get_columns('mismatch_details')}
assert {'rejected', 'rejected_note', 'rejected_at', 'rejected_by'} <= cols, cols
print('OK')
"
```

Expected: prints `OK`, no traceback.

- [ ] **Step 4: Commit**

```bash
git add etl_framework/repository/models.py etl_framework/repository/database.py
git commit -m "feat(db): add rejected mismatch decision columns"
```

---

### Task 2: Repository — mutual exclusion, `reject_mismatch`, `rejected`/`status` filters, `bulk_decide_mismatches`

**Files:**
- Modify: `etl_framework/repository/repository.py:376-531`
- Test: `tests/unit/test_mismatch_search.py` (filters), `tests/unit/test_bulk_decisioning.py` (reject + bulk-decide)

- [ ] **Step 1: Write the failing repository tests**

Append to `tests/unit/test_mismatch_search.py` (uses the existing `db_session`/`_seed` fixtures already in that file — `_seed` inserts 5 mismatches on one result: 3 `value_diff` on columns `amount`/`amount`/`status`, 1 `missing_in_target`, 1 `missing_in_source`, ids 1-5 in that order):

```python
def test_list_mismatches_filters_by_rejected(db_session):
    from etl_framework.repository.repository import RunRepository
    from etl_framework.repository.models import MismatchDetail

    result_id = _seed(db_session)
    first = db_session.query(MismatchDetail).filter(MismatchDetail.test_result_id == result_id).first()
    first.rejected = True
    db_session.commit()

    repo = RunRepository(db_session)
    rejected_rows = repo.list_mismatches(result_id=result_id, rejected=True)
    not_rejected_rows = repo.list_mismatches(result_id=result_id, rejected=False)
    assert len(rejected_rows) == 1
    assert len(not_rejected_rows) == 4


def test_list_mismatches_filters_by_status(db_session):
    from etl_framework.repository.repository import RunRepository
    from etl_framework.repository.models import MismatchDetail

    result_id = _seed(db_session)
    rows = db_session.query(MismatchDetail).filter(MismatchDetail.test_result_id == result_id).order_by(MismatchDetail.id).all()
    rows[0].accepted = True
    rows[1].rejected = True
    db_session.commit()

    repo = RunRepository(db_session)
    assert len(repo.list_mismatches(result_id=result_id, status="accepted")) == 1
    assert len(repo.list_mismatches(result_id=result_id, status="rejected")) == 1
    assert len(repo.list_mismatches(result_id=result_id, status="pending")) == 3


def test_count_mismatches_respects_rejected_and_status_filters(db_session):
    from etl_framework.repository.repository import RunRepository
    from etl_framework.repository.models import MismatchDetail

    result_id = _seed(db_session)
    first = db_session.query(MismatchDetail).filter(MismatchDetail.test_result_id == result_id).first()
    first.rejected = True
    db_session.commit()

    repo = RunRepository(db_session)
    assert repo.count_mismatches(result_id=result_id, rejected=True) == 1
    assert repo.count_mismatches(result_id=result_id, status="rejected") == 1
    assert repo.count_mismatches(result_id=result_id, status="pending") == 4
```

Append to `tests/unit/test_bulk_decisioning.py` (uses the existing `bulk_client`/`_make_run` fixtures already in that file):

```python
def test_reject_mismatch_clears_prior_accept_and_vice_versa(bulk_client):
    engine = _db_module.SessionLocal().bind
    _make_run(engine)
    db = Session(engine)
    try:
        tr = TestResult(
            run_id="bulk-run-001", query_name="q1", status="FAILED",
            duration_seconds=1.0, source_row_count=10, target_row_count=10,
            value_mismatch_count=1, missing_in_target_count=0, missing_in_source_count=0,
        )
        db.add(tr); db.commit(); db.refresh(tr)
        md = MismatchDetail(test_result_id=tr.id, column_name="c1",
                            source_value="a", target_value="b", mismatch_type="value_diff",
                            accepted=True, accepted_note="was accepted")
        db.add(md); db.commit(); db.refresh(md)
        tr_id, md_id = tr.id, md.id
    finally:
        db.close()

    from etl_framework.repository.repository import RunRepository
    db = Session(engine)
    try:
        repo = RunRepository(db)
        updated, _ = repo.reject_mismatch(md_id, "actually wrong", "qa-lead")
        assert updated.rejected is True
        assert updated.rejected_note == "actually wrong"
        assert updated.accepted is False
        assert updated.accepted_note is None

        updated2, _ = repo.accept_mismatch(md_id, "re-accepted", "qa-lead")
        assert updated2.accepted is True
        assert updated2.rejected is False
        assert updated2.rejected_note is None
    finally:
        db.close()


def test_bulk_decide_mismatches_accept_respects_filter(bulk_client):
    engine = _db_module.SessionLocal().bind
    _make_run(engine)
    db = Session(engine)
    try:
        tr = TestResult(
            run_id="bulk-run-001", query_name="q1", status="FAILED",
            duration_seconds=1.0, source_row_count=10, target_row_count=10,
            value_mismatch_count=3, missing_in_target_count=0, missing_in_source_count=0,
        )
        db.add(tr); db.commit(); db.refresh(tr)
        m1 = MismatchDetail(test_result_id=tr.id, column_name="amount", source_value="1", target_value="2", mismatch_type="value_diff")
        m2 = MismatchDetail(test_result_id=tr.id, column_name="amount", source_value="3", target_value="4", mismatch_type="value_diff")
        m3 = MismatchDetail(test_result_id=tr.id, column_name="status", source_value="A", target_value="B", mismatch_type="value_diff")
        db.add_all([m1, m2, m3]); db.commit()
        tr_id, m3_id = tr.id, m3.id
    finally:
        db.close()

    from etl_framework.repository.repository import RunRepository
    db = Session(engine)
    try:
        repo = RunRepository(db)
        summary = repo.bulk_decide_mismatches(
            tr_id, decision="accept", note="rounding tolerance", decided_by="qa-lead",
            column="amount",
        )
        assert summary["matched_count"] == 2
        assert summary["decided_count"] == 2
        assert summary["result_status_updated"] is False

        m3 = db.get(MismatchDetail, m3_id)
        assert m3.accepted is False
    finally:
        db.close()


def test_bulk_decide_mismatches_accept_all_rows_flips_result_to_passed(bulk_client):
    engine = _db_module.SessionLocal().bind
    _make_run(engine)
    db = Session(engine)
    try:
        tr = TestResult(
            run_id="bulk-run-001", query_name="q1", status="FAILED",
            duration_seconds=1.0, source_row_count=10, target_row_count=10,
            value_mismatch_count=2, missing_in_target_count=0, missing_in_source_count=0,
        )
        db.add(tr); db.commit(); db.refresh(tr)
        db.add_all([
            MismatchDetail(test_result_id=tr.id, column_name="c1", source_value="a", target_value="b", mismatch_type="value_diff"),
            MismatchDetail(test_result_id=tr.id, column_name="c2", source_value="x", target_value="y", mismatch_type="value_diff"),
        ])
        db.commit()
        tr_id = tr.id
    finally:
        db.close()

    from etl_framework.repository.repository import RunRepository
    db = Session(engine)
    try:
        repo = RunRepository(db)
        summary = repo.bulk_decide_mismatches(tr_id, decision="accept", note="all good", decided_by=None)
        assert summary["decided_count"] == 2
        assert summary["result_status_updated"] is True
        assert db.get(TestResult, tr_id).status == "PASSED"
    finally:
        db.close()


def test_bulk_decide_mismatches_reject_never_flips_result_to_passed(bulk_client):
    engine = _db_module.SessionLocal().bind
    _make_run(engine)
    db = Session(engine)
    try:
        tr = TestResult(
            run_id="bulk-run-001", query_name="q1", status="FAILED",
            duration_seconds=1.0, source_row_count=10, target_row_count=10,
            value_mismatch_count=1, missing_in_target_count=0, missing_in_source_count=0,
        )
        db.add(tr); db.commit(); db.refresh(tr)
        db.add(MismatchDetail(test_result_id=tr.id, column_name="c1", source_value="a", target_value="b", mismatch_type="value_diff"))
        db.commit()
        tr_id = tr.id
    finally:
        db.close()

    from etl_framework.repository.repository import RunRepository
    db = Session(engine)
    try:
        repo = RunRepository(db)
        summary = repo.bulk_decide_mismatches(tr_id, decision="reject", note="confirmed real diff", decided_by=None)
        assert summary["decided_count"] == 1
        assert summary["result_status_updated"] is False
        assert db.get(TestResult, tr_id).status == "FAILED"
    finally:
        db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_mismatch_search.py tests/unit/test_bulk_decisioning.py -v -k "rejected or status or reject_mismatch or bulk_decide"`
Expected: FAIL — `list_mismatches()`/`count_mismatches()` don't accept `rejected`/`status` kwargs, `reject_mismatch`/`bulk_decide_mismatches` don't exist.

- [ ] **Step 3: Implement mutual exclusion in `accept_mismatch`**

In `etl_framework/repository/repository.py`, in `accept_mismatch` (current lines 443-478), change:

```python
        md.accepted = True
        md.accepted_note = note
        md.accepted_at = datetime.now(timezone.utc)
        md.accepted_by = accepted_by
```

to:

```python
        md.accepted = True
        md.accepted_note = note
        md.accepted_at = datetime.now(timezone.utc)
        md.accepted_by = accepted_by
        md.rejected = False
        md.rejected_note = None
        md.rejected_at = None
        md.rejected_by = None
```

- [ ] **Step 4: Add `reject_mismatch`**

Directly after `accept_mismatch`'s closing `return md, status_changed` (current line 478), before `def bulk_accept_mismatches`, add:

```python
    def reject_mismatch(
        self,
        mismatch_id: int,
        note: str,
        rejected_by: str | None,
    ) -> tuple[MismatchDetail, bool]:
        md = self._db.get(MismatchDetail, mismatch_id)
        if md is None:
            raise ValueError(f"MismatchDetail {mismatch_id} not found")
        md.rejected = True
        md.rejected_note = note
        md.rejected_at = datetime.now(timezone.utc)
        md.rejected_by = rejected_by
        md.accepted = False
        md.accepted_note = None
        md.accepted_at = None
        md.accepted_by = None
        self._db.commit()
        self._db.refresh(md)
        return md, False
```

- [ ] **Step 5: Add `rejected`/`status` params to `_apply_mismatch_filters`, `list_mismatches`, `count_mismatches`**

Replace the existing `_apply_mismatch_filters` (current lines 376-399) with:

```python
    def _apply_mismatch_filters(
        self,
        query,
        *,
        search: str | None = None,
        column: str | None = None,
        mismatch_type: str | None = None,
        accepted: bool | None = None,
        rejected: bool | None = None,
        status: str | None = None,
    ):
        if status is not None:
            if status == "pending":
                accepted, rejected = False, False
            elif status == "accepted":
                accepted, rejected = True, None
            elif status == "rejected":
                accepted, rejected = None, True
        if column:
            query = query.filter(MismatchDetail.column_name == column)
        if mismatch_type:
            query = query.filter(MismatchDetail.mismatch_type == mismatch_type)
        if accepted is not None:
            query = query.filter(MismatchDetail.accepted == accepted)
        if rejected is not None:
            query = query.filter(MismatchDetail.rejected == rejected)
        if search:
            like = f"%{search.lower()}%"
            query = query.filter(or_(
                func.lower(MismatchDetail.column_name).like(like),
                func.lower(MismatchDetail.source_value).like(like),
                func.lower(MismatchDetail.target_value).like(like),
                func.lower(cast(MismatchDetail.key_values, String)).like(like),
            ))
        return query
```

Replace `list_mismatches` (current lines 401-427) with:

```python
    def list_mismatches(
        self,
        result_id: int,
        limit: int = 100,
        offset: int = 0,
        search: str | None = None,
        column: str | None = None,
        mismatch_type: str | None = None,
        accepted: bool | None = None,
        rejected: bool | None = None,
        status: str | None = None,
        sort: str = "id",
    ) -> list[MismatchDetail]:
        query = self._apply_mismatch_filters(
            self._mismatch_base_query(result_id),
            search=search, column=column, mismatch_type=mismatch_type,
            accepted=accepted, rejected=rejected, status=status,
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
```

Replace `count_mismatches` (current lines 429-441) with:

```python
    def count_mismatches(
        self,
        result_id: int,
        search: str | None = None,
        column: str | None = None,
        mismatch_type: str | None = None,
        accepted: bool | None = None,
        rejected: bool | None = None,
        status: str | None = None,
    ) -> int:
        query = self._apply_mismatch_filters(
            self._db.query(func.count(MismatchDetail.id)).filter(MismatchDetail.test_result_id == result_id),
            search=search, column=column, mismatch_type=mismatch_type,
            accepted=accepted, rejected=rejected, status=status,
        )
        return int(query.scalar() or 0)
```

- [ ] **Step 6: Add `bulk_decide_mismatches`**

Directly after `bulk_accept_mismatches`'s closing `}` (current line 531, end of file section), add:

```python
    def bulk_decide_mismatches(
        self,
        result_id: int,
        decision: str,
        note: str,
        decided_by: str | None,
        *,
        search: str | None = None,
        column: str | None = None,
        mismatch_type: str | None = None,
        status: str | None = None,
    ) -> dict:
        matched = self._apply_mismatch_filters(
            self._mismatch_base_query(result_id),
            search=search, column=column, mismatch_type=mismatch_type, status=status,
        ).all()
        matched_count = len(matched)

        if decision == "accept":
            targets = [md for md in matched if not md.accepted]
        else:
            targets = [md for md in matched if not md.rejected]

        now = datetime.now(timezone.utc)
        for md in targets:
            if decision == "accept":
                md.accepted = True
                md.accepted_note = note
                md.accepted_at = now
                md.accepted_by = decided_by
                md.rejected = False
                md.rejected_note = None
                md.rejected_at = None
                md.rejected_by = None
            else:
                md.rejected = True
                md.rejected_note = note
                md.rejected_at = now
                md.rejected_by = decided_by
                md.accepted = False
                md.accepted_note = None
                md.accepted_at = None
                md.accepted_by = None
        self._db.commit()

        result_status_updated = False
        if decision == "accept" and targets:
            remaining = (
                self._db.query(MismatchDetail)
                .filter(
                    MismatchDetail.test_result_id == result_id,
                    MismatchDetail.accepted == False,  # noqa: E712
                )
                .count()
            )
            if remaining == 0:
                tr = self._db.get(TestResult, result_id)
                if tr and tr.status != "PASSED":
                    tr.status = "PASSED"
                    run = self.get_run(tr.run_id)
                    if run:
                        run.passed = max(0, (run.passed or 0) + 1)
                        run.failed = max(0, (run.failed or 0) - 1)
                    self._db.commit()
                    result_status_updated = True

        return {
            "decision": decision,
            "matched_count": matched_count,
            "decided_count": len(targets),
            "result_status_updated": result_status_updated,
        }
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_mismatch_search.py tests/unit/test_bulk_decisioning.py -v`
Expected: PASS (all tests in both files, including pre-existing ones — no regressions).

- [ ] **Step 8: Commit**

```bash
git add -f tests/unit/test_mismatch_search.py tests/unit/test_bulk_decisioning.py
git add etl_framework/repository/repository.py
git commit -m "feat(repo): add reject_mismatch, rejected/status filters, bulk_decide_mismatches"
```

---

### Task 3: Schemas

**Files:**
- Modify: `api/schemas.py:290-317` (`MismatchOut`, filter enums), `api/schemas.py:762-773` (`MismatchAcceptRequest`/`Out`), `api/schemas.py:864-879` (bulk schemas)

- [ ] **Step 1: Extend `MismatchOut` with rejection fields, add `MismatchStatusFilter`**

In `api/schemas.py`, replace the existing `MismatchOut` class (current lines 290-304) with:

```python
class MismatchOut(BaseModel):
    id: int
    column_name: str | None = None
    key_values: dict | None = None
    source_value: str | None = None
    target_value: str | None = None
    mismatch_type: str | None = None
    delta: float | None = None
    relative_delta: float | None = None
    accepted: bool = False
    accepted_note: str | None = None
    accepted_at: datetime | None = None
    accepted_by: str | None = None
    rejected: bool = False
    rejected_note: str | None = None
    rejected_at: datetime | None = None
    rejected_by: str | None = None

    model_config = {"from_attributes": True}


class MismatchStatusFilter(str, Enum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"
```

- [ ] **Step 2: Add `MismatchRejectRequest`, replace `MismatchAcceptOut` with `MismatchDecisionOut`**

Replace the existing `MismatchAcceptRequest`/`MismatchAcceptOut` block (current lines 762-773) with:

```python
class MismatchAcceptRequest(BaseModel):
    note: str = Field(min_length=1)
    accepted_by: str | None = None


class MismatchRejectRequest(BaseModel):
    note: str = Field(min_length=1)
    rejected_by: str | None = None


class MismatchDecisionOut(BaseModel):
    id: int
    accepted: bool
    accepted_note: str | None = None
    accepted_at: datetime | None = None
    accepted_by: str | None = None
    rejected: bool = False
    rejected_note: str | None = None
    rejected_at: datetime | None = None
    rejected_by: str | None = None
    result_status_updated: bool = False


MismatchAcceptOut = MismatchDecisionOut
```

- [ ] **Step 3: Add bulk-decide request/response schemas**

Directly after `BulkDecisionOut` (current lines 875-878), add:

```python
class BulkMismatchDecisionRequest(BaseModel):
    decision: Literal["accept", "reject"]
    note: str = Field(min_length=1, max_length=1000)
    decided_by: str | None = None
    search: str | None = None
    column: str | None = None
    mismatch_type: MismatchTypeFilter | None = None
    status: MismatchStatusFilter | None = None


class BulkMismatchDecisionOut(BaseModel):
    decision: str
    matched_count: int = 0
    decided_count: int = 0
    result_status_updated: bool = False
```

- [ ] **Step 4: Verify the module still imports cleanly**

Run: `python -c "import api.schemas"`
Expected: no output, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add api/schemas.py
git commit -m "feat(schemas): add rejection fields, status filter, bulk mismatch decision schemas"
```

---

### Task 4: Routes — `/reject`, `/mismatches/bulk-decide`, extend the list endpoint

**Files:**
- Modify: `api/routes/runs.py:20-50` (imports), `api/routes/runs.py:561-623` (`list_result_mismatches`), `api/routes/runs.py:791-836` (`accept_mismatch`, add `reject_mismatch` after it)
- Test: `tests/unit/test_bulk_decisioning.py`, `tests/unit/test_runs_extensions.py`

- [ ] **Step 1: Write the failing route tests**

Append to `tests/unit/test_bulk_decisioning.py`:

```python
def test_reject_mismatch_endpoint(bulk_client):
    engine = _db_module.SessionLocal().bind
    _make_run(engine)
    db = Session(engine)
    try:
        tr = TestResult(
            run_id="bulk-run-001", query_name="q1", status="FAILED",
            duration_seconds=1.0, source_row_count=10, target_row_count=10,
            value_mismatch_count=1, missing_in_target_count=0, missing_in_source_count=0,
        )
        db.add(tr); db.commit(); db.refresh(tr)
        md = MismatchDetail(test_result_id=tr.id, column_name="c1",
                            source_value="a", target_value="b", mismatch_type="value_diff")
        db.add(md); db.commit(); db.refresh(md)
        tr_id, md_id = tr.id, md.id
    finally:
        db.close()

    resp = bulk_client.patch(
        f"/api/runs/bulk-run-001/results/{tr_id}/mismatches/{md_id}/reject",
        json={"note": "confirmed real diff"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["rejected"] is True
    assert data["rejected_note"] == "confirmed real diff"
    assert data["accepted"] is False


def test_reject_mismatch_endpoint_requires_note(bulk_client):
    resp = bulk_client.patch(
        "/api/runs/bulk-run-001/results/1/mismatches/1/reject",
        json={"note": ""},
    )
    assert resp.status_code == 422


def test_bulk_decide_endpoint_filtered_and_all_rows(bulk_client):
    engine = _db_module.SessionLocal().bind
    _make_run(engine)
    db = Session(engine)
    try:
        tr = TestResult(
            run_id="bulk-run-001", query_name="q1", status="FAILED",
            duration_seconds=1.0, source_row_count=10, target_row_count=10,
            value_mismatch_count=3, missing_in_target_count=0, missing_in_source_count=0,
        )
        db.add(tr); db.commit(); db.refresh(tr)
        db.add_all([
            MismatchDetail(test_result_id=tr.id, column_name="amount", source_value="1", target_value="2", mismatch_type="value_diff"),
            MismatchDetail(test_result_id=tr.id, column_name="amount", source_value="3", target_value="4", mismatch_type="value_diff"),
            MismatchDetail(test_result_id=tr.id, column_name="status", source_value="A", target_value="B", mismatch_type="value_diff"),
        ])
        db.commit()
        tr_id = tr.id
    finally:
        db.close()

    filtered = bulk_client.post(
        f"/api/runs/bulk-run-001/results/{tr_id}/mismatches/bulk-decide",
        json={"decision": "accept", "note": "rounding", "column": "amount"},
    )
    assert filtered.status_code == 200
    assert filtered.json()["decided_count"] == 2
    assert filtered.json()["result_status_updated"] is False

    all_rows = bulk_client.post(
        f"/api/runs/bulk-run-001/results/{tr_id}/mismatches/bulk-decide",
        json={"decision": "accept", "note": "the rest"},
    )
    assert all_rows.status_code == 200
    assert all_rows.json()["decided_count"] == 1
    assert all_rows.json()["result_status_updated"] is True


def test_bulk_decide_endpoint_404_for_missing_run(bulk_client):
    resp = bulk_client.post(
        "/api/runs/no-such-run/results/1/mismatches/bulk-decide",
        json={"decision": "accept", "note": "x"},
    )
    assert resp.status_code == 404
```

In `tests/unit/test_runs_extensions.py`, find `test_mismatches_forwards_filters` and add a new test directly after it:

```python
def test_mismatches_forwards_rejected_and_status_filters(client, mock_run_repo):
    run = MagicMock()
    mock_run_repo.get_run.return_value = run
    mock_run_repo.list_mismatches.return_value = []
    mock_run_repo.count_mismatches.return_value = 0

    client.get("/api/runs/r1/results/42/mismatches?rejected=true&status=rejected")
    mock_run_repo.list_mismatches.assert_called_once_with(
        result_id=42, limit=100, offset=0,
        search=None, column=None, mismatch_type=None, accepted=None,
        rejected=True, status="rejected", sort="id",
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_bulk_decisioning.py tests/unit/test_runs_extensions.py -v -k "reject or bulk_decide or rejected_and_status"`
Expected: FAIL — `/reject` and `/mismatches/bulk-decide` routes 404 (don't exist), `list_mismatches` mock call doesn't include `rejected`/`status`.

- [ ] **Step 3: Update imports in `api/routes/runs.py`**

In the `from api.schemas import (...)` block (current lines 20-50), add these entries in alphabetical position:

```python
    BulkMismatchDecisionOut,
    BulkMismatchDecisionRequest,
```

(alongside the existing `BulkDecisionOut, BulkMismatchAcceptRequest, BulkOverrideRequest,`)

and:

```python
    MismatchDecisionOut,
    MismatchRejectRequest,
    MismatchStatusFilter,
```

(alongside the existing `MismatchAcceptOut, MismatchAcceptRequest, MismatchColumnInsight, MismatchOut, MismatchSortField, MismatchTestInsight, MismatchTypeFilter,` — `MismatchAcceptOut` stays imported too, since `api/schemas.py` still exports it as an alias).

- [ ] **Step 4: Extend `list_result_mismatches` with `rejected`/`status` params**

Replace the existing `list_result_mismatches` function (current lines 561-623) with:

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
    rejected: bool | None = None,
    status: MismatchStatusFilter | None = None,
    sort: MismatchSortField = MismatchSortField.id,
    db: Session = Depends(get_session),
):
    from etl_framework.repository.models import TestResult

    repo = RunRepository(db)
    if repo.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")

    mismatch_type_value = mismatch_type.value if mismatch_type else None
    status_value = status.value if status else None
    rows = repo.list_mismatches(
        result_id=result_id,
        limit=limit,
        offset=offset,
        search=search,
        column=column,
        mismatch_type=mismatch_type_value,
        accepted=accepted,
        rejected=rejected,
        status=status_value,
        sort=sort.value,
    )
    total = repo.count_mismatches(
        result_id=result_id,
        search=search,
        column=column,
        mismatch_type=mismatch_type_value,
        accepted=accepted,
        rejected=rejected,
        status=status_value,
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
            rejected=m.rejected,
            rejected_note=m.rejected_note,
            rejected_at=m.rejected_at,
            rejected_by=m.rejected_by,
        )
        for m in rows
    ]
```

- [ ] **Step 5: Add the `/reject` route and the `/mismatches/bulk-decide` route**

Directly after `accept_mismatch`'s closing `)` (current line 836, right before `@router.post("/{run_id}/results/bulk-accept"...)`), add:

```python
@router.patch(
    "/{run_id}/results/{result_id}/mismatches/{mismatch_id}/reject",
    response_model=MismatchDecisionOut,
)
def reject_mismatch(
    run_id: str,
    result_id: int,
    mismatch_id: int,
    body: MismatchRejectRequest,
    request: Request,
    db: Session = Depends(get_session),
):
    from etl_framework.repository.models import MismatchDetail, TestResult

    repo = RunRepository(db)
    if repo.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    tr = db.get(TestResult, result_id)
    if tr is None or tr.run_id != run_id:
        raise HTTPException(status_code=404, detail="Result not found")
    md = db.get(MismatchDetail, mismatch_id)
    if md is None or md.test_result_id != result_id:
        raise HTTPException(status_code=404, detail="Mismatch not found")
    updated, status_changed = repo.reject_mismatch(mismatch_id, body.note, body.rejected_by)
    AuditService(db).log(
        request,
        "mismatch.rejected",
        "mismatch",
        mismatch_id,
        {
            "run_id": run_id,
            "result_id": result_id,
            "note": body.note,
            "rejected_by": body.rejected_by,
        },
        actor=body.rejected_by,
    )
    return MismatchDecisionOut(
        id=updated.id,
        accepted=updated.accepted,
        accepted_note=updated.accepted_note,
        accepted_at=updated.accepted_at,
        accepted_by=updated.accepted_by,
        rejected=updated.rejected,
        rejected_note=updated.rejected_note,
        rejected_at=updated.rejected_at,
        rejected_by=updated.rejected_by,
        result_status_updated=status_changed,
    )


@router.post(
    "/{run_id}/results/{result_id}/mismatches/bulk-decide",
    response_model=BulkMismatchDecisionOut,
)
def bulk_decide_mismatches(
    run_id: str,
    result_id: int,
    body: BulkMismatchDecisionRequest,
    request: Request,
    db: Session = Depends(get_session),
):
    from etl_framework.repository.models import TestResult

    repo = RunRepository(db)
    if repo.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    tr = db.get(TestResult, result_id)
    if tr is None or tr.run_id != run_id:
        raise HTTPException(status_code=404, detail="Result not found")

    summary = repo.bulk_decide_mismatches(
        result_id,
        decision=body.decision,
        note=body.note,
        decided_by=body.decided_by,
        search=body.search,
        column=body.column,
        mismatch_type=body.mismatch_type.value if body.mismatch_type else None,
        status=body.status.value if body.status else None,
    )
    AuditService(db).log(
        request,
        "mismatch.bulk_decided",
        "test_result",
        result_id,
        {
            "run_id": run_id,
            "decision": body.decision,
            "note": body.note,
            "decided_by": body.decided_by,
            "filters": {
                "search": body.search,
                "column": body.column,
                "mismatch_type": body.mismatch_type.value if body.mismatch_type else None,
                "status": body.status.value if body.status else None,
            },
            "matched_count": summary["matched_count"],
            "decided_count": summary["decided_count"],
        },
        actor=body.decided_by,
    )
    return BulkMismatchDecisionOut(**summary)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_bulk_decisioning.py tests/unit/test_runs_extensions.py tests/unit/test_mismatch_search.py -v`
Expected: PASS (all tests, including pre-existing ones).

- [ ] **Step 7: Commit**

```bash
git add -f tests/unit/test_bulk_decisioning.py tests/unit/test_runs_extensions.py
git add api/routes/runs.py
git commit -m "feat(api): add reject and bulk-decide mismatch endpoints, extend list filters"
```

---

### Task 5: Full backend regression check

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend unit test suite**

Run: `python -m pytest tests/unit -q`
Expected: all tests pass, no regressions from Tasks 1-4.

- [ ] **Step 2: Fix any regressions found**

If a test fails that touches `MismatchDetail`, `list_mismatches`, `count_mismatches`, `accept_mismatch`, or the `mismatches` routes, re-read the failure and fix it in the relevant Task 1-4 file before proceeding.

---

### Task 6: Frontend — shared state, `diffStatus`, mismatch decision form

**Files:**
- Modify: `frontend/app.js:326-343` (Differences tab state), `frontend/app.js:396` (near `acceptForms`), `frontend/app.js:2405` and `2432` (`differenceQueryString`, `clearDifferenceFilters`)

- [ ] **Step 1: Replace `diffAccepted` with `diffStatus` in state**

In `frontend/app.js`, in the Differences Explorer state block (current lines 326-343), replace:

```js
    diffAccepted: '',
```

with:

```js
    diffStatus: '',
```

- [ ] **Step 2: Add the shared `mismatchDecisionForm` state**

Directly after the existing `acceptForms: {},` line (current line 396), add:

```js
    mismatchDecisionForm: { open: false, scope: null, decision: null, note: '', saving: false },
```

- [ ] **Step 3: Update `differenceQueryString` and `clearDifferenceFilters` to use `diffStatus`**

In `differenceQueryString` (current lines 2398-2408), replace:

```js
      if (this.diffAccepted) params.set('accepted', this.diffAccepted);
```

with:

```js
      if (this.diffStatus) params.set('status', this.diffStatus);
```

In `clearDifferenceFilters` (current line 2432), replace:

```js
      this.diffSearch = ''; this.diffColumn = ''; this.diffType = ''; this.diffAccepted = ''; this.diffSort = 'id';
```

with:

```js
      this.diffSearch = ''; this.diffColumn = ''; this.diffType = ''; this.diffStatus = ''; this.diffSort = 'id';
```

- [ ] **Step 4: Verify the file still parses**

Run: `node --check frontend/app.js`
Expected: no output, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add frontend/app.js
git commit -m "feat(frontend): switch Differences tab to tri-state status filter, add shared decision-form state"
```

---

### Task 7: Frontend — Differences Explorer bulk-decide bar, status dropdown, shared modal methods

**Files:**
- Modify: `frontend/index.html:3282-3286` (accepted dropdown → status dropdown), `frontend/index.html:3269-3296` (filter card, add bulk bar)
- Modify: `frontend/app.js` (add `openMismatchDecisionForm`, `closeMismatchDecisionForm`, `submitMismatchDecision`, directly after `_renderDifferenceCharts`)

- [ ] **Step 1: Replace the accepted-only dropdown with a status dropdown**

In `frontend/index.html`, replace (current lines 3282-3286):

```html
          <select x-model="diffAccepted" @change="applyDifferenceFilters()" class="field-input field-select">
            <option value="">Accepted + open</option>
            <option value="true">Accepted only</option>
            <option value="false">Open only</option>
          </select>
```

with:

```html
          <select x-model="diffStatus" @change="applyDifferenceFilters()" class="field-input field-select">
            <option value="">All statuses</option>
            <option value="pending">Pending only</option>
            <option value="accepted">Accepted only</option>
            <option value="rejected">Rejected only</option>
          </select>
```

- [ ] **Step 2: Update the row-status badge to show rejected state**

In the same table body (current line 3314), replace:

```html
                <td><span class="badge" :class="m.accepted ? 'badge-green' : 'badge-gray'" x-text="m.accepted ? 'Accepted' : 'Open'"></span></td>
```

with:

```html
                <td><span class="badge" :class="m.accepted ? 'badge-green' : m.rejected ? 'badge-rose' : 'badge-gray'" x-text="m.accepted ? 'Accepted' : m.rejected ? 'Rejected' : 'Pending'"></span></td>
```

- [ ] **Step 3: Add the bulk-decide bar above the filter card**

In `frontend/index.html`, directly before the existing filter card `<div class="card mb-4">` (current line 3269, the one containing the search input), insert:

```html
      <div class="card mb-4">
        <div class="flex items-center gap-3 flex-wrap">
          <span class="text-xs font-semibold text-slate-600" x-text="diffTotal + ' matching rows'"></span>
          <div class="flex gap-2 ml-auto">
            <button @click="openMismatchDecisionForm('diff', 'accept')"
                    class="btn-accept-confirm text-xs px-3 py-1.5" :disabled="diffTotal === 0">
              Accept all <span x-text="diffTotal"></span> filtered
            </button>
            <button @click="openMismatchDecisionForm('diff', 'reject')"
                    class="btn-outline btn-sm text-xs" :disabled="diffTotal === 0">
              Reject all <span x-text="diffTotal"></span> filtered
            </button>
          </div>
        </div>
      </div>

```

- [ ] **Step 4: Add the shared modal markup**

In `frontend/index.html`, directly after the existing `bulkDecisionForm` modal's closing `</template>` (search for `<template x-if="bulkDecisionForm.open">` and find its matching closing `</template>`), add:

```html
    <template x-if="mismatchDecisionForm.open">
      <div class="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-40" @click.self="closeMismatchDecisionForm()">
        <div class="bg-white rounded-xl shadow-2xl p-6 w-full max-w-md mx-4">
          <div class="flex items-center justify-between mb-4">
            <div class="text-base font-semibold text-slate-900"
                 x-text="(mismatchDecisionForm.decision === 'accept' ? 'Accept' : 'Reject') + ' matching mismatches'"></div>
            <button @click="closeMismatchDecisionForm()" class="text-slate-400 hover:text-slate-600 text-lg leading-none">&times;</button>
          </div>
          <div class="space-y-3">
            <div>
              <label class="field-label" x-text="'Reason (applied to every matching mismatch)'"></label>
              <textarea x-model="mismatchDecisionForm.note" maxlength="1000" rows="3"
                        class="field-input" placeholder="e.g. rounding diff within tolerance"></textarea>
            </div>
            <div class="flex gap-2 justify-end pt-2">
              <button @click="closeMismatchDecisionForm()" class="btn-secondary">Cancel</button>
              <button @click="submitMismatchDecision()" :disabled="mismatchDecisionForm.saving"
                      class="btn-primary">
                <span x-show="!mismatchDecisionForm.saving">Confirm</span>
                <span x-show="mismatchDecisionForm.saving">Saving...</span>
              </button>
            </div>
          </div>
        </div>
      </div>
    </template>
```

- [ ] **Step 5: Add the shared modal methods**

In `frontend/app.js`, directly after `_renderDifferenceCharts()`'s closing `},` (end of the Differences Explorer methods block, right before the next section comment or method), add:

```js
    openMismatchDecisionForm(scope, decision) {
      this.mismatchDecisionForm = { open: true, scope, decision, note: '', saving: false };
    },

    closeMismatchDecisionForm() {
      this.mismatchDecisionForm = { open: false, scope: null, decision: null, note: '', saving: false };
    },

    async submitMismatchDecision() {
      const { scope, decision, note } = this.mismatchDecisionForm;
      const trimmed = (note || '').trim();
      if (!trimmed) {
        this.toast('warn', 'Reason required', 'Enter a reason before deciding these mismatches');
        return;
      }
      this.mismatchDecisionForm.saving = true;
      try {
        if (scope === 'diff') {
          const body = { decision, note: trimmed };
          if (this.diffSearch) body.search = this.diffSearch;
          if (this.diffColumn) body.column = this.diffColumn;
          if (this.diffType) body.mismatch_type = this.diffType;
          if (this.diffStatus) body.status = this.diffStatus;
          const result = await api('POST',
            `/api/runs/${this.diffRunId}/results/${this.diffResultId}/mismatches/bulk-decide`, body);
          this.closeMismatchDecisionForm();
          this.toast('success', `${result.decided_count} mismatch(es) ${decision}ed`,
            result.result_status_updated ? 'Test flipped to PASSED' : '');
          this.diffPage = 0;
          await this.fetchDifferenceRows();
          await this.loadDifferenceInsights();
        } else if (scope === 'drawer') {
          const body = { decision, note: trimmed, status: 'pending' };
          const result = await api('POST',
            `/api/runs/${this.drawer.runId}/results/${this.drawer.result.id}/mismatches/bulk-decide`, body);
          this.closeMismatchDecisionForm();
          this.toast('success', `${result.decided_count} mismatch(es) ${decision}ed`,
            result.result_status_updated ? 'Test flipped to PASSED' : '');
          this.drawer.offset = 0;
          this.drawer.loading = true;
          await this._fetchMismatches();
          if (result.decided_count > 0) await this.loadRuns();
        }
      } catch (e) {
        this.mismatchDecisionForm.saving = false;
        this.toast('error', 'Bulk decision failed', e.message);
      }
    },
```

- [ ] **Step 6: Verify the file still parses**

Run: `node --check frontend/app.js`
Expected: no output, exit code 0.

- [ ] **Step 7: Commit**

```bash
git add frontend/app.js frontend/index.html
git commit -m "feat(frontend): add Differences Explorer bulk-decide bar and shared decision modal"
```

---

### Task 8: Frontend — mismatch drawer bulk decide (replace the paginated-loop accept)

**Files:**
- Modify: `frontend/app.js:3856-3891` (`filteredMismatches`, `acceptAllVisibleMismatches`)
- Modify: `frontend/index.html:4802-4821` (drawer action bar)

- [ ] **Step 1: Replace `acceptAllVisibleMismatches` with server-side bulk decide**

In `frontend/app.js`, replace the existing `acceptAllVisibleMismatches` method (current lines 3867-3891) with:

```js
    async decideAllPendingDrawerMismatches(decision) {
      this.openMismatchDecisionForm('drawer', decision);
    },
```

(`filteredMismatches`, current lines 3856-3865, is unchanged — it already filters on `m.accepted`/`m.rejected`, which now come back as real fields from the API instead of always-undefined for `rejected`.)

- [ ] **Step 2: Update the drawer action bar markup**

In `frontend/index.html`, replace the existing drawer action bar (current lines 4802-4821):

```html
        <template x-if="!drawer.loading && drawer.rows.length > 0">
          <div class="flex items-center gap-2 mb-3 flex-wrap">
            <div class="flex gap-1 text-xs">
              <template x-for="f in ['ALL','PENDING','ACCEPTED','REJECTED']" :key="f">
                <button @click="mismatchStatusFilter = f"
                        class="px-2 py-1 rounded border text-xs transition-colors"
                        :class="mismatchStatusFilter === f ? 'bg-indigo-500 text-white border-indigo-500' : 'text-slate-600 border-slate-200 hover:bg-slate-50'"
                        x-text="f">
                </button>
              </template>
            </div>
            <div class="ml-auto">
              <button @click="acceptAllVisibleMismatches()"
                      class="btn-outline btn-sm text-xs"
                      :disabled="filteredMismatches.filter(m => !m.accepted && !m.rejected).length === 0">
                Accept All Visible
              </button>
            </div>
          </div>
        </template>
```

with:

```html
        <template x-if="!drawer.loading && drawer.rows.length > 0">
          <div class="flex items-center gap-2 mb-3 flex-wrap">
            <div class="flex gap-1 text-xs">
              <template x-for="f in ['ALL','PENDING','ACCEPTED','REJECTED']" :key="f">
                <button @click="mismatchStatusFilter = f"
                        class="px-2 py-1 rounded border text-xs transition-colors"
                        :class="mismatchStatusFilter === f ? 'bg-indigo-500 text-white border-indigo-500' : 'text-slate-600 border-slate-200 hover:bg-slate-50'"
                        x-text="f">
                </button>
              </template>
            </div>
            <div class="ml-auto flex gap-2">
              <button @click="decideAllPendingDrawerMismatches('accept')"
                      class="btn-accept-confirm text-xs px-3 py-1.5">
                Accept All Pending
              </button>
              <button @click="decideAllPendingDrawerMismatches('reject')"
                      class="btn-outline btn-sm text-xs">
                Reject All Pending
              </button>
            </div>
          </div>
        </template>
```

(This drops the disabled-state count check tied to `drawer.rows` — the button now always offers to decide every pending row server-side, not just what's currently loaded into the drawer; a 0-`decided_count` result is a harmless no-op, reported via the existing toast in `submitMismatchDecision`.)

- [ ] **Step 3: Verify the file still parses**

Run: `node --check frontend/app.js`
Expected: no output, exit code 0.

- [ ] **Step 4: Commit**

```bash
git add frontend/app.js frontend/index.html
git commit -m "feat(frontend): drive mismatch drawer bulk accept/reject through server-side bulk-decide"
```

---

### Task 9: Manual verification (frontend)

**Files:** none (verification only)

- [ ] **Step 1: Start the dev server**

Use this project's `run` skill (or the project's normal dev-server startup command) to launch the API + frontend.

- [ ] **Step 2: Exercise the Differences Explorer bulk bar**

1. Open History → a FAILED run with mismatches → Differences tab (or navigate directly) and select the run + a test with several mismatches.
2. Set a column or search filter that matches a subset of rows; confirm the "Accept all N filtered" / "Reject all N filtered" button labels update to the filtered count (not the full result's count).
3. Click "Reject all N filtered", enter a reason, confirm — check the table re-loads, the affected rows show a "Rejected" badge, and rows outside the filter are unaffected.
4. Clear filters (so the count equals every row in the result) and click "Accept all N filtered" — confirm it now acts on every remaining pending row, and if that empties the pending set, the underlying test result flips to PASSED (check via History).
5. Set the new status dropdown to "Rejected only" — confirm it shows exactly the row(s) rejected in step 3.

- [ ] **Step 3: Exercise the mismatch drawer**

1. From the Compare tab, open a mismatch drawer for a test with mismatches (`openMismatchDrawer`).
2. Click "Reject All Pending", enter a reason, confirm — check rows flip to rejected and the `REJECTED` status-filter chip now shows them (previously always empty, since `rejected` didn't exist).
3. Click "Accept All Pending" — confirm remaining pending rows accept, and if the drawer had more mismatches than the 100-row page previously loaded, confirm ones beyond that page are still deciding correctly (verify via the Differences tab or an API call, not just what was visibly loaded in the drawer).

- [ ] **Step 4: Report results**

Summarize pass/fail for each item above. If anything doesn't work as described, fix it before considering this plan complete — do not report success without having actually driven the flow in a browser.
