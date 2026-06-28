from __future__ import annotations
import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
import etl_framework.repository.contract_models  # noqa: F401
from etl_framework.repository.contract_models import Contract, ContractBreach
from etl_framework.repository.contract_repository import ContractRepository


def _db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _contract_data(**overrides) -> dict:
    base = {
        "name": "payments_v1",
        "source_job": "payments_reconciliation",
        "owner": "data-platform@co.com",
        "sla_hours": 4.0,
        "consumers": ["finance-team"],
        "breach_severity": "error",
        "version": "1.0",
    }
    base.update(overrides)
    return base


# --- CRUD ---

def test_create_and_get_contract():
    db = _db()
    repo = ContractRepository(db)
    contract = repo.create(_contract_data())
    assert contract.id is not None
    fetched = repo.get("payments_v1")
    assert fetched is not None
    assert fetched.owner == "data-platform@co.com"
    assert fetched.sla_hours == 4.0


def test_list_contracts():
    db = _db()
    repo = ContractRepository(db)
    repo.create(_contract_data(name="c1", source_job="job1"))
    repo.create(_contract_data(name="c2", source_job="job2"))
    result = repo.list()
    assert len(result) == 2


def test_update_contract():
    db = _db()
    repo = ContractRepository(db)
    repo.create(_contract_data())
    updated = repo.update("payments_v1", owner="new-owner@co.com", sla_hours=8.0)
    assert updated is not None
    assert updated.owner == "new-owner@co.com"
    assert updated.sla_hours == 8.0


def test_delete_contract_soft_deletes():
    db = _db()
    repo = ContractRepository(db)
    repo.create(_contract_data())
    deleted = repo.delete("payments_v1")
    assert deleted is True
    fetched = repo.get("payments_v1")
    assert fetched is None  # soft-deleted: active=False, get() only returns active


def test_create_duplicate_name_raises():
    db = _db()
    repo = ContractRepository(db)
    repo.create(_contract_data())
    with pytest.raises(Exception):
        repo.create(_contract_data())


def test_get_nonexistent_returns_none():
    db = _db()
    repo = ContractRepository(db)
    assert repo.get("does_not_exist") is None


def test_list_by_source_job():
    db = _db()
    repo = ContractRepository(db)
    repo.create(_contract_data(name="c1", source_job="job_a"))
    repo.create(_contract_data(name="c2", source_job="job_a"))
    repo.create(_contract_data(name="c3", source_job="job_b"))
    result = repo.list_by_source_job("job_a")
    assert len(result) == 2
    assert all(c.source_job == "job_a" for c in result)


# --- Breach lifecycle ---

def test_open_breach_creates_record():
    db = _db()
    repo = ContractRepository(db)
    contract = repo.create(_contract_data())
    breach = repo.open_breach(contract.id, "run-001", "dq_violation")
    assert breach is not None
    assert breach.contract_id == contract.id
    assert breach.run_id == "run-001"
    assert breach.breach_type == "dq_violation"
    assert breach.opened_at is not None
    assert breach.resolved_at is None


def test_open_breach_idempotent_when_already_open():
    db = _db()
    repo = ContractRepository(db)
    contract = repo.create(_contract_data())
    repo.open_breach(contract.id, "run-001", "dq_violation")
    breach2 = repo.open_breach(contract.id, "run-002", "dq_violation")
    assert breach2 is None  # second call returns None: breach already open


def test_resolve_breaches_for_job_sets_resolved_at_and_duration():
    db = _db()
    repo = ContractRepository(db)
    contract = repo.create(_contract_data())
    repo.open_breach(contract.id, "run-001", "dq_violation")
    resolved = repo.resolve_breaches_for_job("payments_reconciliation", "run-002")
    assert len(resolved) == 1
    breach, resolved_contract = resolved[0]
    assert breach.resolved_at is not None
    assert breach.resolution_run_id == "run-002"
    assert breach.duration_hours is not None
    assert breach.duration_hours >= 0
    assert resolved_contract.id == contract.id


def test_resolve_does_nothing_when_no_open_breach():
    db = _db()
    repo = ContractRepository(db)
    repo.create(_contract_data())
    resolved = repo.resolve_breaches_for_job("payments_reconciliation", "run-002")
    assert resolved == []


def test_escalate_overdue_marks_breach_escalated():
    from datetime import timedelta
    db = _db()
    repo = ContractRepository(db)
    contract = repo.create(_contract_data(sla_hours=0.001))
    breach = repo.open_breach(contract.id, "run-001", "dq_violation")
    raw = db.query(ContractBreach).filter(ContractBreach.id == breach.id).first()
    raw.opened_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.commit()
    escalated = repo.escalate_overdue()
    assert len(escalated) == 1
    assert escalated[0][0].escalated is True
    assert escalated[0][0].escalated_at is not None


def test_escalate_does_not_re_escalate():
    from datetime import timedelta
    db = _db()
    repo = ContractRepository(db)
    contract = repo.create(_contract_data(sla_hours=0.001))
    breach = repo.open_breach(contract.id, "run-001", "dq_violation")
    raw = db.query(ContractBreach).filter(ContractBreach.id == breach.id).first()
    raw.opened_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.commit()
    repo.escalate_overdue()
    escalated_again = repo.escalate_overdue()
    assert escalated_again == []


def test_list_breaches_returns_history():
    db = _db()
    repo = ContractRepository(db)
    contract = repo.create(_contract_data())
    repo.open_breach(contract.id, "run-001", "dq_violation")
    repo.resolve_breaches_for_job("payments_reconciliation", "run-002")
    repo.open_breach(contract.id, "run-003", "schema_change")
    breaches = repo.list_breaches(contract.id)
    assert len(breaches) == 2


def test_list_open_breaches():
    db = _db()
    repo = ContractRepository(db)
    contract = repo.create(_contract_data())
    repo.open_breach(contract.id, "run-001", "dq_violation")
    open_breaches = repo.list_open_breaches(contract.id)
    assert len(open_breaches) == 1
    assert open_breaches[0].resolved_at is None


def test_get_status_ok_when_no_breach():
    db = _db()
    repo = ContractRepository(db)
    repo.create(_contract_data())
    status = repo.get_status("payments_v1")
    assert status["status"] == "OK"
    assert status["open_breach"] is None


def test_get_status_breached():
    db = _db()
    repo = ContractRepository(db)
    contract = repo.create(_contract_data())
    repo.open_breach(contract.id, "run-001", "dq_violation")
    status = repo.get_status("payments_v1")
    assert status["status"] == "BREACHED"
    assert status["open_breach"]["breach_type"] == "dq_violation"


def test_get_status_overdue():
    from datetime import timedelta
    db = _db()
    repo = ContractRepository(db)
    contract = repo.create(_contract_data(sla_hours=0.001))
    breach = repo.open_breach(contract.id, "run-001", "dq_violation")
    raw = db.query(ContractBreach).filter(ContractBreach.id == breach.id).first()
    raw.opened_at = datetime.now(timezone.utc) - timedelta(hours=1)
    raw.escalated = True
    db.commit()
    status = repo.get_status("payments_v1")
    assert status["status"] == "OVERDUE"


# --- Version bump ---

def test_bump_version_records_history():
    db = _db()
    repo = ContractRepository(db)
    repo.create(_contract_data())
    v = repo.bump_version("payments_v1", "minor", note="added freshness check")
    assert v.version == "1.1"
    assert v.bump_type == "minor"
    versions = repo.list_versions("payments_v1")
    assert len(versions) == 1
    assert versions[0].version == "1.1"


def test_bump_major_version():
    db = _db()
    repo = ContractRepository(db)
    repo.create(_contract_data())
    v = repo.bump_version("payments_v1", "major")
    assert v.version == "2.0"
