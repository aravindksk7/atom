from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api.services.coverage_service import (
    build_coverage,
    build_flaky_report,
    classify_level,
    compute_flakiness,
    extract_tables,
)
from etl_framework.repository.database import Base
from etl_framework.repository.models import ColumnProfile, SavedJob, TestResult, TestRun


# --- extract_tables ---

def test_extract_simple_from():
    assert extract_tables("SELECT * FROM orders") == {"orders"}


def test_extract_join_and_schema_prefix():
    sql = "SELECT * FROM dbo.orders o JOIN [dbo].[customers] c ON o.cid = c.id"
    assert extract_tables(sql) == {"dbo.orders", "dbo.customers"}


def test_extract_strips_quotes():
    assert extract_tables('SELECT * FROM "orders"') == {"orders"}


def test_extract_excludes_cte_names():
    sql = "WITH recent AS (SELECT * FROM orders) SELECT * FROM recent"
    assert extract_tables(sql) == {"orders"}


def test_extract_empty_query():
    assert extract_tables("") == set()


# --- classify_level ---

def test_tested_when_dq_rule_targets_column():
    assert classify_level(
        column="amt", rule_columns={"amt"}, reconciled_columns=set(), observed_columns=set()
    ) == "tested"


def test_tested_when_reconciled():
    assert classify_level(
        column="amt", rule_columns=set(), reconciled_columns={"amt"}, observed_columns={"amt"}
    ) == "tested"


def test_observed_when_only_profiled():
    assert classify_level(
        column="amt", rule_columns=set(), reconciled_columns=set(), observed_columns={"amt"}
    ) == "observed"


def test_untested_otherwise():
    assert classify_level(
        column="amt", rule_columns=set(), reconciled_columns=set(), observed_columns=set()
    ) == "untested"


# --- compute_flakiness ---

def test_flakiness_score_counts_transitions():
    # PASSED,FAILED,PASSED,FAILED = 3 transitions over window 4 -> 3/3 = 1.0
    statuses = ["PASSED", "FAILED", "PASSED", "FAILED"]
    assert compute_flakiness(statuses) == pytest.approx(1.0)


def test_flakiness_stable_history_is_zero():
    assert compute_flakiness(["PASSED"] * 10) == 0.0


def test_flakiness_short_history_is_zero():
    assert compute_flakiness(["PASSED"]) == 0.0
    assert compute_flakiness([]) == 0.0


def test_flakiness_one_transition():
    # 1 transition / 3 = 0.333...
    assert compute_flakiness(["PASSED", "PASSED", "FAILED", "FAILED"]) == pytest.approx(1 / 3)


# --- build_coverage / build_flaky_report (DB-backed) ---

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_build_coverage_classifies_columns_and_caches(db):
    db.add(SavedJob(
        name="orders_recon", job_type="reconciliation",
        query="SELECT * FROM orders", key_columns=["id"],
        exclude_columns=["region"],
        params={"rules": [{"type": "not_null", "column": "amt"}]},
    ))
    db.add(ColumnProfile(job_name="orders_recon", run_id=None, column_name="region", distinct_count=4))
    db.commit()

    result = build_coverage(db)
    table = next(t for t in result["tables"] if t["table"] == "orders")
    levels = {c["column"]: c["level"] for c in table["columns"]}
    assert levels["amt"] == "tested"   # DQ rule
    assert levels["id"] == "tested"    # key column
    assert levels["region"] == "observed"  # profiled but excluded from reconciliation
    assert result["summary"]["tables"] == 1

    # Cache hit: mutating the session without a new profile/snapshot/job
    # change must not be visible until cache expiry.
    cached = build_coverage(db)
    assert cached == result


def test_build_coverage_cache_busts_on_new_profile(db):
    db.add(SavedJob(name="j", job_type="reconciliation",
                     query="SELECT * FROM t", key_columns=["id"]))
    db.commit()
    first = build_coverage(db)
    table = next(t for t in first["tables"] if t["table"] == "t")
    assert {c["column"] for c in table["columns"]} == {"id"}

    db.add(ColumnProfile(job_name="j", run_id=None, column_name="amt", distinct_count=10))
    db.commit()
    second = build_coverage(db)
    table = next(t for t in second["tables"] if t["table"] == "t")
    assert {c["column"] for c in table["columns"]} == {"id", "amt"}


def test_build_flaky_report_scores_and_sorts():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        now = datetime.now(timezone.utc)
        statuses = ["PASSED", "FAILED", "PASSED", "FAILED"]  # flaky: score 1.0
        for i, status in enumerate(statuses):
            run_id = f"run-{i}"
            db.add(TestRun(run_id=run_id, completed_at=now + timedelta(minutes=i)))
            db.add(TestResult(run_id=run_id, query_name="flaky_job", status=status))
        stable_run = "run-stable"
        db.add(TestRun(run_id=stable_run, completed_at=now))
        db.add(TestResult(run_id=stable_run, query_name="stable_job", status="PASSED"))
        db.commit()

        report = build_flaky_report(db, window=20)
        by_job = {r["job"]: r for r in report}
        assert "stable_job" not in by_job  # score 0 excluded
        assert by_job["flaky_job"]["score"] == pytest.approx(1.0)
        assert by_job["flaky_job"]["transitions"] == 3
        assert by_job["flaky_job"]["flaky"] is True
