from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.services.expectations_service import export_suites, sync_suites
from etl_framework.expectations.suite import ExpectationSuite, dump_suite, load_suite
from etl_framework.repository.database import Base
from etl_framework.repository.repository import JobRepository


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _seed_job(db, name: str = "orders_reconciliation"):
    return JobRepository(db).create({
        "name": name,
        "job_type": "reconciliation",
        "query": "SELECT * FROM orders",
        "params": {"key_columns": ["id"], "rules": [{"type": "unique", "column": "id"}]},
        "enabled": True,
    })


def test_sync_replaces_job_rules(db, tmp_path: Path) -> None:
    _seed_job(db)
    dump_suite(
        ExpectationSuite(job="orders_reconciliation",
                         rules=[{"type": "not_null", "column": "id", "severity": "error"}]),
        tmp_path / "orders_reconciliation.yml",
    )
    report = sync_suites(tmp_path, db)
    assert report.synced == ["orders_reconciliation"]
    assert report.errors == []
    job = JobRepository(db).get("orders_reconciliation")
    assert job.params["rules"] == [
        {"type": "not_null", "column": "id", "severity": "error"}
    ]


def test_sync_reports_missing_job(db, tmp_path: Path) -> None:
    dump_suite(ExpectationSuite(job="ghost_job", rules=[]), tmp_path / "ghost_job.yml")
    report = sync_suites(tmp_path, db)
    assert report.missing_jobs == ["ghost_job"]
    assert report.synced == []


def test_sync_rejects_invalid_rule(db, tmp_path: Path) -> None:
    _seed_job(db)
    dump_suite(
        ExpectationSuite(job="orders_reconciliation",
                         rules=[{"type": "not_a_rule_type"}]),
        tmp_path / "orders_reconciliation.yml",
    )
    report = sync_suites(tmp_path, db)
    assert report.synced == []
    assert len(report.errors) == 1
    assert "orders_reconciliation" in report.errors[0]
    # job rules untouched
    job = JobRepository(db).get("orders_reconciliation")
    assert job.params["rules"] == [{"type": "unique", "column": "id"}]


def test_export_writes_suite_per_job_with_rules(db, tmp_path: Path) -> None:
    _seed_job(db)
    written = export_suites(tmp_path, db)
    assert written == ["orders_reconciliation"]
    suite = load_suite(tmp_path / "orders_reconciliation.yml")
    assert suite.rules == [{"type": "unique", "column": "id"}]
