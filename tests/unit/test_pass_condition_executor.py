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
    assert ex._check_condition(StepCondition(require_status=["PASSED"]), r) is False
    # total mismatches = 3+1+1 = 5; 5 > 4 → False; require_status=[] to isolate this check
    assert ex._check_condition(StepCondition(require_status=[], max_mismatch_count=4), r) is False
    # total 5 <= 6 → True
    assert ex._check_condition(StepCondition(require_status=[], max_mismatch_count=6), r) is True


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
