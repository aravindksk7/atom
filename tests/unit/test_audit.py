from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import AuditRepository


def _session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def test_audit_log_writes_row():
    db = _session()
    repo = AuditRepository(db)
    event = repo.log(
        actor="ci-token",
        action="run.created",
        resource_type="run",
        resource_id="abc-123",
    )
    assert event.id is not None
    assert event.actor == "ci-token"
    assert event.action == "run.created"
    assert event.resource_type == "run"
    assert event.resource_id == "abc-123"
    assert event.diff is None
    assert event.created_at is not None


def test_audit_log_with_diff():
    db = _session()
    repo = AuditRepository(db)
    diff = {"note": "Known rounding difference", "accepted_by": "analyst"}
    event = repo.log(
        actor="analyst-token",
        action="mismatch.accepted",
        resource_type="mismatch",
        resource_id="99",
        diff=diff,
    )
    assert event.diff == diff


def test_audit_list_filters_by_resource_type():
    db = _session()
    repo = AuditRepository(db)
    repo.log(actor="a", action="run.created", resource_type="run", resource_id="r1")
    repo.log(actor="a", action="job.created", resource_type="job", resource_id="j1")
    repo.log(actor="a", action="run.deleted", resource_type="run", resource_id="r2")

    results = repo.list(resource_type="run")
    assert len(results) == 2
    assert all(e.resource_type == "run" for e in results)


def test_audit_list_filters_by_resource_id():
    db = _session()
    repo = AuditRepository(db)
    repo.log(actor="a", action="run.created", resource_type="run", resource_id="r1")
    repo.log(actor="a", action="run.deleted", resource_type="run", resource_id="r2")

    results = repo.list(resource_id="r1")
    assert len(results) == 1
    assert results[0].resource_id == "r1"


def test_audit_list_default_limit():
    db = _session()
    repo = AuditRepository(db)
    for i in range(60):
        repo.log(actor="a", action="run.created", resource_type="run", resource_id=str(i))
    results = repo.list()
    assert len(results) == 50  # default limit
