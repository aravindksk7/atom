# SQL Job Pass Conditions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `PassCondition` to job definitions and extend `StepCondition` so users can declare explicit pass/fail criteria (row counts, mismatch limits, required status, custom SQL) on individual jobs and per-step launch gates.

**Architecture:** A new `PassCondition` Pydantic model is stored inside `JobDefinition.pass_condition` (serialized to the `params` JSON column alongside existing `rules`). The executor evaluates it post-reconciliation via `_apply_pass_condition()`, injecting `pass_condition_violation` mismatch records on failure. `StepCondition` gains five parallel granular fields evaluated by the existing `_check_condition()`. The frontend adds a Conditions tab to the job modal and five new inputs to the step settings panel.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy, Alpine.js (frontend), pytest + FastAPI TestClient.

---

## File Map

| File | Change |
|---|---|
| `api/schemas.py` | Add `PassCondition`; add `pass_condition` to `JobDefinition`; add 5 fields to `StepCondition` |
| `api/routes/jobs.py` | Extend `_job_to_data()` and `_job_to_schema()` to serialize/deserialize `pass_condition` |
| `api/services/run_executor.py` | Add `_apply_pass_condition()`; fix `_job_to_definition()` to extract `pass_condition` and `rules` from params; extend `_check_condition()` |
| `frontend/app.js` | Add Conditions tab fields; extend step settings defaults; update `saveJob()`, `openNewJobModal()`, `openEditJobModal()`, `getStepCfg()`, `_buildJobSequence()` |
| `frontend/index.html` | Add Conditions tab panel; add 5 new step-settings inputs |
| `tests/unit/test_pass_condition_schema.py` | New — schema unit tests |
| `tests/unit/test_pass_condition_executor.py` | New — executor unit + integration tests |
| `tests/unit/test_api.py` | Extend — job CRUD with pass_condition round-trip |

---

## Task 1: PassCondition Schema + StepCondition Extension

**Files:**
- Modify: `api/schemas.py`
- Create: `tests/unit/test_pass_condition_schema.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_pass_condition_schema.py`:

```python
"""Tests for PassCondition schema and StepCondition extension."""
from __future__ import annotations
import pytest
from pydantic import ValidationError


def test_pass_condition_all_defaults_are_none_or_empty():
    from api.schemas import PassCondition
    pc = PassCondition()
    assert pc.min_row_count is None
    assert pc.max_row_count is None
    assert pc.max_value_mismatches is None
    assert pc.max_missing_in_target is None
    assert pc.max_missing_in_source is None
    assert pc.require_status == []
    assert pc.pass_sql is None
    assert pc.pass_sql_mode == "rows_mean_pass"


def test_pass_condition_rejects_extra_fields():
    from api.schemas import PassCondition
    with pytest.raises(ValidationError):
        PassCondition(unknown_field=1)


def test_pass_condition_pass_sql_mode_must_be_valid():
    from api.schemas import PassCondition
    with pytest.raises(ValidationError):
        PassCondition(pass_sql="SELECT 1", pass_sql_mode="bad_mode")


def test_pass_condition_accepts_valid_fields():
    from api.schemas import PassCondition
    pc = PassCondition(
        min_row_count=1,
        max_row_count=1000,
        max_value_mismatches=0,
        max_missing_in_target=5,
        max_missing_in_source=5,
        require_status=["PASSED", "SLOW"],
        pass_sql="SELECT 1",
        pass_sql_mode="rows_mean_fail",
    )
    assert pc.min_row_count == 1
    assert pc.require_status == ["PASSED", "SLOW"]
    assert pc.pass_sql_mode == "rows_mean_fail"


def test_job_definition_accepts_pass_condition():
    from api.schemas import JobDefinition, PassCondition
    job = JobDefinition(
        name="test",
        query="SELECT * FROM t",
        key_columns=["id"],
        pass_condition=PassCondition(min_row_count=1, require_status=["PASSED"]),
    )
    assert job.pass_condition is not None
    assert job.pass_condition.min_row_count == 1
    assert job.pass_condition.require_status == ["PASSED"]


def test_job_definition_pass_condition_defaults_to_none():
    from api.schemas import JobDefinition
    job = JobDefinition(name="test", query="SELECT * FROM t", key_columns=["id"])
    assert job.pass_condition is None


def test_step_condition_new_fields_default_none():
    from api.schemas import StepCondition
    sc = StepCondition()
    assert sc.min_row_count is None
    assert sc.max_row_count is None
    assert sc.max_value_mismatches is None
    assert sc.max_missing_in_target is None
    assert sc.max_missing_in_source is None


def test_step_condition_existing_fields_unchanged():
    from api.schemas import StepCondition
    sc = StepCondition(require_status=["PASSED"], max_mismatch_count=5)
    assert sc.require_status == ["PASSED"]
    assert sc.max_mismatch_count == 5


def test_step_condition_accepts_new_fields():
    from api.schemas import StepCondition
    sc = StepCondition(min_row_count=1, max_row_count=1000, max_value_mismatches=0,
                       max_missing_in_target=2, max_missing_in_source=2)
    assert sc.min_row_count == 1
    assert sc.max_value_mismatches == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_pass_condition_schema.py -v
```

Expected: `ImportError` or `ValidationError` — `PassCondition` does not yet exist.

- [ ] **Step 3: Add `PassCondition` to `api/schemas.py`**

In `api/schemas.py`, add this class **before** the existing `StepCondition` class (currently at line 120):

```python
class PassCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_row_count: int | None = None
    max_row_count: int | None = None
    max_value_mismatches: int | None = None
    max_missing_in_target: int | None = None
    max_missing_in_source: int | None = None
    require_status: list[str] = Field(default_factory=list)
    pass_sql: str | None = None
    pass_sql_mode: Literal["rows_mean_pass", "rows_mean_fail"] = "rows_mean_pass"
```

- [ ] **Step 4: Extend `StepCondition` in `api/schemas.py`**

Replace the existing `StepCondition` class body (lines 121–122):

```python
class StepCondition(BaseModel):
    require_status: list[str] = Field(default_factory=lambda: ["PASSED"])
    max_mismatch_count: int | None = None
    min_row_count: int | None = None
    max_row_count: int | None = None
    max_value_mismatches: int | None = None
    max_missing_in_target: int | None = None
    max_missing_in_source: int | None = None
```

- [ ] **Step 5: Add `pass_condition` field to `JobDefinition` in `api/schemas.py`**

In `JobDefinition` (around line 283), add after the `rules` field:

```python
    rules: list[DQRule] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    pass_condition: PassCondition | None = None
```

- [ ] **Step 6: Run tests to verify they pass**

```
pytest tests/unit/test_pass_condition_schema.py -v
```

Expected: all 9 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add api/schemas.py tests/unit/test_pass_condition_schema.py
git commit -m "feat(schema): add PassCondition model and extend StepCondition with granular fields"
```

---

## Task 2: API Persistence — `_job_to_data` and `_job_to_schema`

**Files:**
- Modify: `api/routes/jobs.py`
- Modify: `tests/unit/test_api.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_api.py`:

```python
# --- pass_condition round-trip ---

def test_create_job_with_pass_condition(client):
    body = {
        "name": "pc_job",
        "job_type": "reconciliation",
        "query": "SELECT * FROM orders",
        "key_columns": ["id"],
        "pass_condition": {
            "min_row_count": 1,
            "max_value_mismatches": 0,
            "require_status": ["PASSED"],
        },
    }
    resp = client.post("/api/jobs", json=body)
    assert resp.status_code == 201
    data = resp.json()
    assert data["pass_condition"]["min_row_count"] == 1
    assert data["pass_condition"]["max_value_mismatches"] == 0
    assert data["pass_condition"]["require_status"] == ["PASSED"]


def test_update_job_pass_condition_round_trips(client):
    body = {
        "name": "pc_update_job",
        "job_type": "reconciliation",
        "query": "SELECT * FROM orders",
        "key_columns": ["id"],
    }
    client.post("/api/jobs", json=body)
    update = {**body, "pass_condition": {"min_row_count": 5, "pass_sql": "SELECT 1"}}
    resp = client.put("/api/jobs/pc_update_job", json=update)
    assert resp.status_code == 200
    pc = resp.json()["pass_condition"]
    assert pc["min_row_count"] == 5
    assert pc["pass_sql"] == "SELECT 1"
    assert pc["pass_sql_mode"] == "rows_mean_pass"


def test_update_job_pass_condition_null_clears_it(client):
    body = {
        "name": "pc_null_job",
        "job_type": "reconciliation",
        "query": "SELECT * FROM orders",
        "key_columns": ["id"],
        "pass_condition": {"min_row_count": 1},
    }
    client.post("/api/jobs", json=body)
    update = {**body, "pass_condition": None}
    resp = client.put("/api/jobs/pc_null_job", json=update)
    assert resp.status_code == 200
    assert resp.json()["pass_condition"] is None


def test_list_jobs_includes_pass_condition(client):
    body = {
        "name": "pc_list_job",
        "job_type": "reconciliation",
        "query": "SELECT * FROM orders",
        "key_columns": ["id"],
        "pass_condition": {"max_value_mismatches": 0},
    }
    client.post("/api/jobs", json=body)
    resp = client.get("/api/jobs")
    assert resp.status_code == 200
    jobs = resp.json()
    pc_job = next((j for j in jobs if j["name"] == "pc_list_job"), None)
    assert pc_job is not None
    assert pc_job["pass_condition"]["max_value_mismatches"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_api.py::test_create_job_with_pass_condition tests/unit/test_api.py::test_update_job_pass_condition_round_trips tests/unit/test_api.py::test_update_job_pass_condition_null_clears_it tests/unit/test_api.py::test_list_jobs_includes_pass_condition -v
```

Expected: FAIL — `pass_condition` field not preserved on save/load.

- [ ] **Step 3: Update `_job_to_data()` in `api/routes/jobs.py`**

Replace the existing `_job_to_data` function (lines 94–102):

```python
def _job_to_data(job: JobDefinition) -> dict:
    data = job.model_dump(exclude={"rules", "depends_on", "pass_condition"})
    params = dict(data.get("params") or {})
    if job.rules:
        params["rules"] = [r.model_dump() for r in job.rules]
    if job.depends_on:
        params["depends_on"] = list(job.depends_on)
    if job.pass_condition:
        params["pass_condition"] = job.pass_condition.model_dump(exclude_none=True)
    data["params"] = params
    return data
```

- [ ] **Step 4: Update `_job_to_schema()` in `api/routes/jobs.py`**

Replace the existing `_job_to_schema` function (lines 71–91):

```python
def _job_to_schema(job: SavedJob) -> JobDefinition:
    params = dict(job.params or {})
    rules_raw = params.pop("rules", [])
    depends_on = params.pop("depends_on", [])
    pass_condition_raw = params.pop("pass_condition", None)
    from api.schemas import DQRule, PassCondition
    rules = [DQRule.model_validate(r) for r in (rules_raw or [])]
    pass_condition = PassCondition.model_validate(pass_condition_raw) if pass_condition_raw else None
    return JobDefinition(
        name=job.name,
        description=job.description,
        tags=job.tags or [],
        job_type=job.job_type,
        query=job.query,
        key_columns=job.key_columns or [],
        exclude_columns=job.exclude_columns or [],
        source_env=job.source_env,
        target_env=job.target_env,
        params=params,
        enabled=job.enabled,
        rules=rules,
        depends_on=depends_on,
        pass_condition=pass_condition,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```
pytest tests/unit/test_api.py::test_create_job_with_pass_condition tests/unit/test_api.py::test_update_job_pass_condition_round_trips tests/unit/test_api.py::test_update_job_pass_condition_null_clears_it tests/unit/test_api.py::test_list_jobs_includes_pass_condition -v
```

Expected: all 4 PASS.

- [ ] **Step 6: Run full unit test suite to catch regressions**

```
pytest tests/unit/test_api.py -v
```

Expected: all pre-existing tests still PASS.

- [ ] **Step 7: Commit**

```bash
git add api/routes/jobs.py tests/unit/test_api.py
git commit -m "feat(api): serialize/deserialize pass_condition in job CRUD"
```

---

## Task 3: Executor — `_apply_pass_condition`, `_job_to_definition` Fix, `_check_condition` Extension

**Files:**
- Modify: `api/services/run_executor.py`
- Create: `tests/unit/test_pass_condition_executor.py`

- [ ] **Step 1: Write failing unit tests for `_apply_pass_condition`**

Create `tests/unit/test_pass_condition_executor.py`:

```python
"""Unit + integration tests for pass condition evaluation in RunExecutor."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.schemas import JobDefinition, PassCondition, RunSettings, StepCondition
from api.services.run_executor import RunExecutor
from etl_framework.reconciliation.models import ReconciliationResult
from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import JobRepository, RunRepository
from etl_framework.runner.state import TestStatus


def _db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _make_executor(db=None):
    db = db or _db()
    return RunExecutor(
        db=db,
        run_id="test-pc",
        source_env="dev",
        target_env="prod",
        job_sequence=[],
        run_settings=RunSettings(metrics_enabled=False),
    )


def _result(**kwargs):
    defaults = dict(
        query_name="test",
        source_env="dev",
        target_env="prod",
        source_row_count=5,
        target_row_count=5,
        matched_count=5,
        missing_in_target_count=0,
        missing_in_source_count=0,
        value_mismatch_count=0,
        mismatches=[],
        status=TestStatus.PASSED,
        executed_at=datetime.now(timezone.utc),
        duration_seconds=0.1,
    )
    defaults.update(kwargs)
    return ReconciliationResult(**defaults)


def _job(**kwargs):
    defaults = dict(name="test_job", query="SELECT 1", key_columns=["id"])
    defaults.update(kwargs)
    return JobDefinition(**defaults)


@dataclass
class _Engine:
    df: pd.DataFrame

    def execute_query(self, query, params=None):
        return self.df


@dataclass
class _ErrorEngine:
    def execute_query(self, query, params=None):
        raise RuntimeError("connection refused")


# ---------------------------------------------------------------------------
# _apply_pass_condition
# ---------------------------------------------------------------------------

def test_apply_pass_condition_no_condition_returns_same_object():
    ex = _make_executor()
    r = _result()
    j = _job()
    out = ex._apply_pass_condition(r, j, _Engine(pd.DataFrame()))
    assert out is r


def test_apply_pass_condition_all_pass_returns_unchanged():
    ex = _make_executor()
    r = _result(source_row_count=5, value_mismatch_count=0)
    j = _job(pass_condition=PassCondition(min_row_count=1, max_value_mismatches=0))
    out = ex._apply_pass_condition(r, j, _Engine(pd.DataFrame()))
    assert out.status == TestStatus.PASSED
    assert out.value_mismatch_count == 0


def test_apply_pass_condition_min_row_count_violation():
    ex = _make_executor()
    r = _result(source_row_count=0)
    j = _job(pass_condition=PassCondition(min_row_count=1))
    out = ex._apply_pass_condition(r, j, _Engine(pd.DataFrame()))
    assert out.status == TestStatus.FAILED
    assert out.value_mismatch_count == 1
    assert out.mismatches[0].mismatch_type == "pass_condition_violation"
    assert "row_count 0 < min 1" in out.mismatches[0].key_values["pass_condition"]


def test_apply_pass_condition_max_row_count_violation():
    ex = _make_executor()
    r = _result(source_row_count=100)
    j = _job(pass_condition=PassCondition(max_row_count=10))
    out = ex._apply_pass_condition(r, j, _Engine(pd.DataFrame()))
    assert out.status == TestStatus.FAILED
    assert "row_count 100 > max 10" in out.mismatches[0].key_values["pass_condition"]


def test_apply_pass_condition_max_value_mismatches_violation():
    ex = _make_executor()
    r = _result(value_mismatch_count=3)
    j = _job(pass_condition=PassCondition(max_value_mismatches=0))
    out = ex._apply_pass_condition(r, j, _Engine(pd.DataFrame()))
    assert out.status == TestStatus.FAILED
    assert "value_mismatches 3 > 0" in out.mismatches[0].key_values["pass_condition"]


def test_apply_pass_condition_max_missing_in_target_violation():
    ex = _make_executor()
    r = _result(missing_in_target_count=2)
    j = _job(pass_condition=PassCondition(max_missing_in_target=0))
    out = ex._apply_pass_condition(r, j, _Engine(pd.DataFrame()))
    assert out.status == TestStatus.FAILED
    assert "missing_in_target 2 > 0" in out.mismatches[0].key_values["pass_condition"]


def test_apply_pass_condition_max_missing_in_source_violation():
    ex = _make_executor()
    r = _result(missing_in_source_count=1)
    j = _job(pass_condition=PassCondition(max_missing_in_source=0))
    out = ex._apply_pass_condition(r, j, _Engine(pd.DataFrame()))
    assert out.status == TestStatus.FAILED
    assert "missing_in_source 1 > 0" in out.mismatches[0].key_values["pass_condition"]


def test_apply_pass_condition_require_status_violation():
    ex = _make_executor()
    r = _result(status=TestStatus.FAILED)
    j = _job(pass_condition=PassCondition(require_status=["PASSED"]))
    out = ex._apply_pass_condition(r, j, _Engine(pd.DataFrame()))
    texts = [m.key_values["pass_condition"] for m in out.mismatches]
    assert any("FAILED" in t for t in texts)


def test_apply_pass_condition_pass_sql_rows_mean_pass_no_rows_fails():
    ex = _make_executor()
    r = _result()
    j = _job(pass_condition=PassCondition(pass_sql="SELECT 1", pass_sql_mode="rows_mean_pass"))
    out = ex._apply_pass_condition(r, j, _Engine(pd.DataFrame()))  # empty df
    assert out.status == TestStatus.FAILED
    assert "pass_sql returned no rows" in out.mismatches[0].key_values["pass_condition"]


def test_apply_pass_condition_pass_sql_rows_mean_pass_with_rows_passes():
    ex = _make_executor()
    r = _result()
    j = _job(pass_condition=PassCondition(pass_sql="SELECT 1", pass_sql_mode="rows_mean_pass"))
    out = ex._apply_pass_condition(r, j, _Engine(pd.DataFrame({"x": [1]})))
    assert out.status == TestStatus.PASSED


def test_apply_pass_condition_pass_sql_rows_mean_fail_with_rows_fails():
    ex = _make_executor()
    r = _result()
    j = _job(pass_condition=PassCondition(pass_sql="SELECT bad", pass_sql_mode="rows_mean_fail"))
    out = ex._apply_pass_condition(r, j, _Engine(pd.DataFrame({"bad": [1]})))
    assert out.status == TestStatus.FAILED
    assert "pass_sql returned rows" in out.mismatches[0].key_values["pass_condition"]


def test_apply_pass_condition_pass_sql_rows_mean_fail_no_rows_passes():
    ex = _make_executor()
    r = _result()
    j = _job(pass_condition=PassCondition(pass_sql="SELECT bad", pass_sql_mode="rows_mean_fail"))
    out = ex._apply_pass_condition(r, j, _Engine(pd.DataFrame()))
    assert out.status == TestStatus.PASSED


def test_apply_pass_condition_pass_sql_error_is_violation():
    ex = _make_executor()
    r = _result()
    j = _job(pass_condition=PassCondition(pass_sql="SELECT 1"))
    out = ex._apply_pass_condition(r, j, _ErrorEngine())
    assert out.status == TestStatus.FAILED
    assert "pass_sql error" in out.mismatches[0].key_values["pass_condition"]


def test_apply_pass_condition_multiple_violations_all_recorded():
    ex = _make_executor()
    r = _result(source_row_count=0, value_mismatch_count=5)
    j = _job(pass_condition=PassCondition(min_row_count=1, max_value_mismatches=0))
    out = ex._apply_pass_condition(r, j, _Engine(pd.DataFrame()))
    assert out.status == TestStatus.FAILED
    violation_mismatches = [m for m in out.mismatches if m.mismatch_type == "pass_condition_violation"]
    assert len(violation_mismatches) == 2
    assert out.value_mismatch_count == 5 + 2


# ---------------------------------------------------------------------------
# _check_condition — new fields
# ---------------------------------------------------------------------------

def test_check_condition_min_row_count_blocks_next_step():
    ex = _make_executor()
    r = _result(source_row_count=0)
    assert ex._check_condition(StepCondition(min_row_count=1), r) is False


def test_check_condition_min_row_count_passes():
    ex = _make_executor()
    r = _result(source_row_count=5)
    assert ex._check_condition(StepCondition(min_row_count=1), r) is True


def test_check_condition_max_row_count_blocks():
    ex = _make_executor()
    r = _result(source_row_count=100)
    assert ex._check_condition(StepCondition(max_row_count=10), r) is False


def test_check_condition_max_row_count_passes():
    ex = _make_executor()
    r = _result(source_row_count=5)
    assert ex._check_condition(StepCondition(max_row_count=10), r) is True


def test_check_condition_max_value_mismatches_blocks():
    ex = _make_executor()
    r = _result(value_mismatch_count=5)
    assert ex._check_condition(StepCondition(max_value_mismatches=4), r) is False


def test_check_condition_max_missing_in_target_blocks():
    ex = _make_executor()
    r = _result(missing_in_target_count=3)
    assert ex._check_condition(StepCondition(max_missing_in_target=2), r) is False


def test_check_condition_max_missing_in_source_blocks():
    ex = _make_executor()
    r = _result(missing_in_source_count=1)
    assert ex._check_condition(StepCondition(max_missing_in_source=0), r) is False


def test_check_condition_existing_fields_still_work():
    ex = _make_executor()
    r = _result(status=TestStatus.FAILED, value_mismatch_count=3, missing_in_target_count=1,
                missing_in_source_count=1)
    # require_status blocks because status is FAILED, not PASSED
    assert ex._check_condition(StepCondition(require_status=["PASSED"]), r) is False
    # max_mismatch_count=4 blocks because total 3+1+1=5 > 4
    assert ex._check_condition(StepCondition(max_mismatch_count=4), r) is False
    # max_mismatch_count=6 passes because total 3+1+1=5 <= 6
    assert ex._check_condition(StepCondition(max_mismatch_count=6), r) is True


# ---------------------------------------------------------------------------
# Integration: full executor run with pass_condition stored in DB
# ---------------------------------------------------------------------------

def _create_job_with_params(db, name, source_rows, target_rows, extra_params=None):
    params = {
        "source_rows": source_rows,
        "target_rows": target_rows,
    }
    if extra_params:
        params.update(extra_params)
    JobRepository(db).create({
        "name": name,
        "description": "",
        "tags": [],
        "job_type": "reconciliation",
        "query": f"SELECT * FROM {name}",
        "key_columns": ["id"],
        "exclude_columns": [],
        "source_env": None,
        "target_env": None,
        "params": params,
        "enabled": True,
    })


def test_executor_applies_pass_condition_and_passes():
    db = _db()
    RunRepository(db).create_run("run-pc-pass", "dev", "prod", {})
    _create_job_with_params(
        db, "pc_pass",
        source_rows=[{"id": 1, "val": "a"}],
        target_rows=[{"id": 1, "val": "a"}],
        extra_params={"pass_condition": {"min_row_count": 1, "max_value_mismatches": 0}},
    )
    RunExecutor(
        db=db, run_id="run-pc-pass",
        source_env="dev", target_env="prod",
        job_sequence=["pc_pass"],
        run_settings=RunSettings(metrics_enabled=False),
    ).execute()
    run = RunRepository(db).get_run("run-pc-pass")
    assert run.status == "PASSED"


def test_executor_applies_pass_condition_and_fails_on_violation():
    db = _db()
    RunRepository(db).create_run("run-pc-fail", "dev", "prod", {})
    _create_job_with_params(
        db, "pc_fail",
        source_rows=[{"id": 1, "val": "a"}],
        target_rows=[{"id": 1, "val": "a"}],
        extra_params={"pass_condition": {"min_row_count": 100}},  # 1 row < 100
    )
    RunExecutor(
        db=db, run_id="run-pc-fail",
        source_env="dev", target_env="prod",
        job_sequence=["pc_fail"],
        run_settings=RunSettings(metrics_enabled=False),
    ).execute()
    run = RunRepository(db).get_run("run-pc-fail")
    assert run.status == "FAILED"
    mismatch_types = [m.mismatch_type for m in run.results[0].mismatches]
    assert "pass_condition_violation" in mismatch_types
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_pass_condition_executor.py -v
```

Expected: `AttributeError: 'RunExecutor' object has no attribute '_apply_pass_condition'` or similar.

- [ ] **Step 3: Fix `_job_to_definition()` in `api/services/run_executor.py`**

Replace the existing `_job_to_definition` method (lines 345–360):

```python
def _job_to_definition(self, job) -> JobDefinition:
    params = dict(job.params or {})
    rules_raw = params.pop("rules", [])
    depends_on = params.pop("depends_on", [])
    pass_condition_raw = params.pop("pass_condition", None)
    from api.schemas import DQRule, PassCondition
    rules = [DQRule.model_validate(r) for r in (rules_raw or [])]
    pass_condition = PassCondition.model_validate(pass_condition_raw) if pass_condition_raw else None
    return JobDefinition(
        name=job.name,
        description=job.description,
        tags=job.tags or [],
        job_type=job.job_type,
        query=job.query,
        key_columns=job.key_columns or [],
        exclude_columns=job.exclude_columns or [],
        source_env=job.source_env,
        target_env=job.target_env,
        params=params,
        enabled=job.enabled,
        rules=rules,
        depends_on=depends_on,
        pass_condition=pass_condition,
    )
```

- [ ] **Step 4: Add `_apply_pass_condition()` to `RunExecutor` in `api/services/run_executor.py`**

Add this method after `_apply_dq_rules()` (after line 442):

```python
def _apply_pass_condition(
    self, result: ReconciliationResult, job: JobDefinition, source_engine
) -> ReconciliationResult:
    c = job.pass_condition
    if c is None:
        return result

    violations: list[str] = []

    if c.min_row_count is not None and result.source_row_count < c.min_row_count:
        violations.append(f"row_count {result.source_row_count} < min {c.min_row_count}")
    if c.max_row_count is not None and result.source_row_count > c.max_row_count:
        violations.append(f"row_count {result.source_row_count} > max {c.max_row_count}")
    if c.max_value_mismatches is not None and result.value_mismatch_count > c.max_value_mismatches:
        violations.append(f"value_mismatches {result.value_mismatch_count} > {c.max_value_mismatches}")
    if c.max_missing_in_target is not None and result.missing_in_target_count > c.max_missing_in_target:
        violations.append(f"missing_in_target {result.missing_in_target_count} > {c.max_missing_in_target}")
    if c.max_missing_in_source is not None and result.missing_in_source_count > c.max_missing_in_source:
        violations.append(f"missing_in_source {result.missing_in_source_count} > {c.max_missing_in_source}")

    if c.require_status:
        cur = result.status.value if hasattr(result.status, "value") else str(result.status)
        if cur not in c.require_status:
            violations.append(f"status {cur!r} not in {c.require_status}")

    if c.pass_sql:
        try:
            df = source_engine.execute_query(c.pass_sql)
            has_rows = not df.empty
            if c.pass_sql_mode == "rows_mean_pass" and not has_rows:
                violations.append("pass_sql returned no rows")
            elif c.pass_sql_mode == "rows_mean_fail" and has_rows:
                violations.append("pass_sql returned rows (expected none)")
        except Exception as exc:
            violations.append(f"pass_sql error: {exc}")

    if not violations:
        return result

    extra = [
        MismatchRecord(
            key_values={"pass_condition": v},
            column_name="",
            source_value="FAIL",
            target_value="PASS",
            mismatch_type="pass_condition_violation",
        )
        for v in violations
    ]
    from dataclasses import replace as _replace
    from etl_framework.runner.state import TestStatus as _TS
    return _replace(
        result,
        mismatches=result.mismatches + extra,
        value_mismatch_count=result.value_mismatch_count + len(extra),
        status=_TS.FAILED,
    )
```

- [ ] **Step 5: Wire `_apply_pass_condition()` into the `run_job` closure in `_build_case()`**

In `_build_case()`, locate the lines inside `run_job()` (around line 392):

```python
            if job.rules:
                result = self._apply_dq_rules(result, job, source_engine)
            return result
```

Replace with:

```python
            if job.rules:
                result = self._apply_dq_rules(result, job, source_engine)
            if job.pass_condition:
                result = self._apply_pass_condition(result, job, source_engine)
            return result
```

- [ ] **Step 6: Extend `_check_condition()` in `api/services/run_executor.py`**

Replace the existing `_check_condition` method (lines 264–276):

```python
def _check_condition(self, condition: StepCondition, prev_result: ReconciliationResult) -> bool:
    prev_status = prev_result.status.value if hasattr(prev_result.status, "value") else str(prev_result.status)
    if condition.require_status and prev_status not in condition.require_status:
        return False
    if condition.max_mismatch_count is not None:
        total = (
            prev_result.value_mismatch_count
            + prev_result.missing_in_target_count
            + prev_result.missing_in_source_count
        )
        if total > condition.max_mismatch_count:
            return False
    if condition.min_row_count is not None and prev_result.source_row_count < condition.min_row_count:
        return False
    if condition.max_row_count is not None and prev_result.source_row_count > condition.max_row_count:
        return False
    if condition.max_value_mismatches is not None and prev_result.value_mismatch_count > condition.max_value_mismatches:
        return False
    if condition.max_missing_in_target is not None and prev_result.missing_in_target_count > condition.max_missing_in_target:
        return False
    if condition.max_missing_in_source is not None and prev_result.missing_in_source_count > condition.max_missing_in_source:
        return False
    return True
```

- [ ] **Step 7: Run tests to verify they pass**

```
pytest tests/unit/test_pass_condition_executor.py -v
```

Expected: all tests PASS. Fix the test `test_check_condition_existing_fields_still_work` if the assertion about `max_mismatch_count=4` with total=5 needs adjustment — the total of 3+1+1=5 > 4, so it should return False.

- [ ] **Step 8: Run full executor test suite to catch regressions**

```
pytest tests/unit/test_run_executor.py tests/unit/test_run_steps.py -v
```

Expected: all pre-existing tests PASS.

- [ ] **Step 9: Commit**

```bash
git add api/services/run_executor.py tests/unit/test_pass_condition_executor.py
git commit -m "feat(executor): add _apply_pass_condition and extend _check_condition with granular fields"
```

---

## Task 4: Frontend JS — Conditions Tab + Step Settings

**Files:**
- Modify: `frontend/app.js`

(No automated tests for Alpine.js. Backend correctness is covered by Tasks 1–3. Verify manually after Task 5.)

- [ ] **Step 1: Add `conditions` to `jobModalTabs` in `app.js`**

Find the `jobModalTabs` array (around line 344):

```js
    jobModalTabs: [
      { id: 'basic', label: 'Basic Info' },
      { id: 'settings', label: 'Settings' },
      { id: 'deps', label: 'Dependencies' },
```

Add `{ id: 'conditions', label: 'Conditions' }` as the last entry before the closing `]`. The full array becomes:

```js
    jobModalTabs: [
      { id: 'basic', label: 'Basic Info' },
      { id: 'settings', label: 'Settings' },
      { id: 'deps', label: 'Dependencies' },
      { id: 'rules', label: 'DQ Rules' },
      { id: 'tags', label: 'Tags' },
      { id: 'conditions', label: 'Conditions' },
    ],
```

- [ ] **Step 2: Add pass condition fields to `openNewJobModal()` in `app.js`**

Find `openNewJobModal()` (around line 696). After the last existing field in the `this.jobModal = { ... }` block (e.g. `dbt_run_results_path: ''`), add:

```js
        pass_min_row_count: '',
        pass_max_row_count: '',
        pass_max_value_mismatches: '',
        pass_max_missing_in_target: '',
        pass_max_missing_in_source: '',
        pass_require_status: '',
        pass_sql: '',
        pass_sql_mode: 'rows_mean_pass',
```

- [ ] **Step 3: Populate pass condition fields in `openEditJobModal()` in `app.js`**

Find `openEditJobModal(job)` (around line 712). After the last existing field in the `this.jobModal = { ... }` block, add:

```js
        pass_min_row_count: job.pass_condition?.min_row_count ?? '',
        pass_max_row_count: job.pass_condition?.max_row_count ?? '',
        pass_max_value_mismatches: job.pass_condition?.max_value_mismatches ?? '',
        pass_max_missing_in_target: job.pass_condition?.max_missing_in_target ?? '',
        pass_max_missing_in_source: job.pass_condition?.max_missing_in_source ?? '',
        pass_require_status: (job.pass_condition?.require_status || []).join(', '),
        pass_sql: job.pass_condition?.pass_sql || '',
        pass_sql_mode: job.pass_condition?.pass_sql_mode || 'rows_mean_pass',
```

- [ ] **Step 4: Assemble and send `pass_condition` in `saveJob()` in `app.js`**

Find `saveJob()` (around line 762). Just before the `const body = { ... }` block, add:

```js
      const pc = {};
      if (m.pass_min_row_count !== '') pc.min_row_count = Number(m.pass_min_row_count);
      if (m.pass_max_row_count !== '') pc.max_row_count = Number(m.pass_max_row_count);
      if (m.pass_max_value_mismatches !== '') pc.max_value_mismatches = Number(m.pass_max_value_mismatches);
      if (m.pass_max_missing_in_target !== '') pc.max_missing_in_target = Number(m.pass_max_missing_in_target);
      if (m.pass_max_missing_in_source !== '') pc.max_missing_in_source = Number(m.pass_max_missing_in_source);
      if (m.pass_require_status) pc.require_status = m.pass_require_status.split(',').map(s => s.trim()).filter(Boolean);
      if (m.pass_sql?.trim()) { pc.pass_sql = m.pass_sql.trim(); pc.pass_sql_mode = m.pass_sql_mode; }
```

Then in the `body` object (after `rules: ...`), add:

```js
        pass_condition: Object.keys(pc).length ? pc : null,
```

- [ ] **Step 5: Extend `getStepCfg()` defaults in `app.js`**

Find `getStepCfg(name)` (around line 851). Replace the default object:

```js
    getStepCfg(name) {
      if (!this.stepSettings[name]) {
        this.stepSettings[name] = {
          hold_after: false, wait_seconds: 0,
          require_status: '', max_mismatch_count: '',
          min_row_count: '', max_row_count: '',
          max_value_mismatches: '', max_missing_in_target: '', max_missing_in_source: '',
        };
      }
      return this.stepSettings[name];
    },
```

- [ ] **Step 6: Extend `_buildJobSequence()` in `app.js`**

Find `_buildJobSequence()` (around line 858). Replace with:

```js
    _buildJobSequence() {
      return this.selectedJobs.map(name => {
        const s = this.stepSettings[name] || {};
        const step = { job_name: name };
        if (s.hold_after) step.hold_after = true;
        if (Number(s.wait_seconds) > 0) step.wait_seconds = Number(s.wait_seconds);
        const hasCondition = s.require_status
          || (s.max_mismatch_count !== '' && s.max_mismatch_count != null)
          || (s.min_row_count !== '' && s.min_row_count != null)
          || (s.max_row_count !== '' && s.max_row_count != null)
          || (s.max_value_mismatches !== '' && s.max_value_mismatches != null)
          || (s.max_missing_in_target !== '' && s.max_missing_in_target != null)
          || (s.max_missing_in_source !== '' && s.max_missing_in_source != null);
        if (hasCondition) {
          step.condition = {};
          if (s.require_status) step.condition.require_status = s.require_status.split(',').map(x => x.trim()).filter(Boolean);
          if (s.max_mismatch_count !== '' && s.max_mismatch_count != null) step.condition.max_mismatch_count = Number(s.max_mismatch_count);
          if (s.min_row_count !== '' && s.min_row_count != null) step.condition.min_row_count = Number(s.min_row_count);
          if (s.max_row_count !== '' && s.max_row_count != null) step.condition.max_row_count = Number(s.max_row_count);
          if (s.max_value_mismatches !== '' && s.max_value_mismatches != null) step.condition.max_value_mismatches = Number(s.max_value_mismatches);
          if (s.max_missing_in_target !== '' && s.max_missing_in_target != null) step.condition.max_missing_in_target = Number(s.max_missing_in_target);
          if (s.max_missing_in_source !== '' && s.max_missing_in_source != null) step.condition.max_missing_in_source = Number(s.max_missing_in_source);
        }
        return step;
      });
    },
```

- [ ] **Step 7: Commit**

```bash
git add frontend/app.js
git commit -m "feat(frontend): add Conditions tab data and extend step settings in app.js"
```

---

## Task 5: Frontend HTML — Conditions Tab Panel + Step Settings Inputs

**Files:**
- Modify: `frontend/index.html`

- [ ] **Step 1: Add the Conditions tab panel to the job modal**

Find the Tags tab panel (around line 820):

```html
      <!-- Task 8: Tags tab panel -->
      <div x-show="jobModalTab === 'tags'" class="space-y-3">
```

Insert the following **after** the closing `</div>` of the Tags tab panel (before the final `<div class="flex justify-end gap-3 mt-6">`):

```html
      <!-- Conditions tab panel -->
      <div x-show="jobModalTab === 'conditions'" class="space-y-3">
        <div class="grid-2">
          <div>
            <label class="field-label">Min Source Row Count</label>
            <input type="number" min="0" x-model="jobModal.pass_min_row_count" class="field-input" placeholder="e.g. 1 (blank = skip)" />
          </div>
          <div>
            <label class="field-label">Max Source Row Count</label>
            <input type="number" min="0" x-model="jobModal.pass_max_row_count" class="field-input" placeholder="e.g. 1000000 (blank = skip)" />
          </div>
          <div>
            <label class="field-label">Max Value Mismatches</label>
            <input type="number" min="0" x-model="jobModal.pass_max_value_mismatches" class="field-input" placeholder="e.g. 0 (blank = skip)" />
          </div>
          <div>
            <label class="field-label">Max Missing in Target</label>
            <input type="number" min="0" x-model="jobModal.pass_max_missing_in_target" class="field-input" placeholder="e.g. 0 (blank = skip)" />
          </div>
          <div>
            <label class="field-label">Max Missing in Source</label>
            <input type="number" min="0" x-model="jobModal.pass_max_missing_in_source" class="field-input" placeholder="e.g. 0 (blank = skip)" />
          </div>
          <div>
            <label class="field-label">Required Status (comma-sep)</label>
            <input type="text" x-model="jobModal.pass_require_status" class="field-input" placeholder="PASSED" />
          </div>
        </div>
        <div x-show="jobModal.job_type === 'reconciliation'">
          <label class="field-label">Pass SQL <span class="text-muted text-xs font-normal">(runs against source after reconciliation)</span></label>
          <textarea x-model="jobModal.pass_sql" rows="3" class="field-input font-mono text-xs"
                    placeholder="SELECT 1 WHERE EXISTS (SELECT 1 FROM orders WHERE status = 'ok')"></textarea>
          <div class="mt-2">
            <label class="field-label text-xs">Pass SQL Mode</label>
            <select x-model="jobModal.pass_sql_mode" class="field-input field-select text-sm">
              <option value="rows_mean_pass">Rows returned → PASS (assert good state exists)</option>
              <option value="rows_mean_fail">Rows returned → FAIL (assert no bad rows exist)</option>
            </select>
          </div>
        </div>
      </div>
```

- [ ] **Step 2: Add the 5 new inputs to the step settings panel**

Find the `require_status` input inside the step settings panel (around line 892):

```html
            <div>
              <label class="field-label text-xs">Required prior status (comma-sep, e.g. PASSED)</label>
              <input type="text" class="field-input text-sm py-1" x-model="getStepCfg(name).require_status" placeholder="PASSED" />
            </div>
          </div>
        </div>
```

Insert the following 5 inputs **after** the `require_status` `<div>` and before the closing `</div>` of the step-settings expansion panel:

```html
            <div class="grid grid-cols-2 gap-2">
              <div>
                <label class="field-label text-xs">Min source row count</label>
                <input type="number" min="0" class="field-input text-sm py-1" x-model="getStepCfg(name).min_row_count" placeholder="blank = skip" />
              </div>
              <div>
                <label class="field-label text-xs">Max source row count</label>
                <input type="number" min="0" class="field-input text-sm py-1" x-model="getStepCfg(name).max_row_count" placeholder="blank = skip" />
              </div>
              <div>
                <label class="field-label text-xs">Max value mismatches</label>
                <input type="number" min="0" class="field-input text-sm py-1" x-model="getStepCfg(name).max_value_mismatches" placeholder="blank = skip" />
              </div>
              <div>
                <label class="field-label text-xs">Max missing in target</label>
                <input type="number" min="0" class="field-input text-sm py-1" x-model="getStepCfg(name).max_missing_in_target" placeholder="blank = skip" />
              </div>
              <div>
                <label class="field-label text-xs">Max missing in source</label>
                <input type="number" min="0" class="field-input text-sm py-1" x-model="getStepCfg(name).max_missing_in_source" placeholder="blank = skip" />
              </div>
            </div>
```

- [ ] **Step 3: Manually verify the UI**

Start the dev server (`uvicorn api.main:app --reload`) and open the frontend. Check:

1. Open **New Job** modal → click **Conditions** tab → all 6 numeric/text inputs and the Pass SQL textarea appear.
2. `pass_sql` textarea and mode selector are hidden for `bo_report` job type, visible for `reconciliation`.
3. Create a reconciliation job with `min_row_count: 1` and `max_value_mismatches: 0` via the Conditions tab. Save. Re-open the job — the Conditions tab pre-fills those values.
4. Open the **Launch** tab, select a job, click ⚙. The step settings panel shows 5 new inputs below `Required prior status`.
5. Set a value in `Max value mismatches`, launch a run, inspect the triggered run payload in browser DevTools Network tab — confirm the `condition` object includes `max_value_mismatches`.

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html
git commit -m "feat(frontend): add Conditions tab panel and step settings inputs"
```

---

## Final: Full Test Suite

- [ ] **Step 1: Run all tests**

```
pytest tests/ -v --tb=short
```

Expected: all tests PASS with no regressions.

- [ ] **Step 2: Run integration smoke test**

```
pytest tests/integration/test_api_frontend_smoke.py -v
```

Expected: PASS.
