from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import RunRepository


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _make_run(db: Session, run_id: str = "run-001", status: str = "RUNNING") -> None:
    repo = RunRepository(db)
    repo.create_run(run_id, None, None, run_type="reconciliation")
    repo.update_run_status(run_id, status)


# --- request_cancel ---

def test_request_cancel_sets_flag():
    db = _session()
    _make_run(db)
    repo = RunRepository(db)
    result = repo.request_cancel("run-001")
    assert result is True
    run = repo.get_run("run-001")
    assert run.cancel_requested is True


def test_request_cancel_returns_false_for_missing_run():
    db = _session()
    repo = RunRepository(db)
    assert repo.request_cancel("no-such-run") is False


def test_request_cancel_returns_false_for_terminal_run():
    db = _session()
    _make_run(db, status="PASSED")
    repo = RunRepository(db)
    assert repo.request_cancel("run-001") is False


# --- is_cancel_requested ---

def test_is_cancel_requested_false_by_default():
    db = _session()
    _make_run(db)
    assert RunRepository(db).is_cancel_requested("run-001") is False


def test_is_cancel_requested_true_after_request():
    db = _session()
    _make_run(db)
    repo = RunRepository(db)
    repo.request_cancel("run-001")
    assert repo.is_cancel_requested("run-001") is True
