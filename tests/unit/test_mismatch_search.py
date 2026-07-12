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


def test_list_mismatches_default_order_unchanged(db_session):
    """No sort arg: missing_in_target rows first, then missing_in_source, then everything else, ordered by id within each group."""
    from etl_framework.repository.repository import RunRepository

    result_id = _seed(db_session)
    repo = RunRepository(db_session)

    rows = repo.list_mismatches(result_id=result_id)
    types = [r.mismatch_type for r in rows]
    # _seed() inserts ids 1-3 as value_diff, id 4 as missing_in_target, id 5 as missing_in_source
    assert types == ["missing_in_target", "missing_in_source", "value_diff", "value_diff", "value_diff"]


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
