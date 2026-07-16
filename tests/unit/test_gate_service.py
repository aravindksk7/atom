from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.services.gate_service import evaluate_gate
from etl_framework.repository.contract_models import Contract, ContractBreach
from etl_framework.repository.database import Base
from etl_framework.repository.models import TestResult, TestRun


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _seed_run(db, job: str, status: str, run_id: str = "run-1"):
    db.add(TestRun(run_id=run_id, status="COMPLETED"))
    db.add(TestResult(
        run_id=run_id, query_name=job, status=status,
        executed_at=datetime.now(timezone.utc),
    ))
    db.commit()


def test_promote_when_latest_result_passed(db):
    _seed_run(db, "orders_reconciliation", "PASSED")
    verdict = evaluate_gate("orders_reconciliation", db)
    assert verdict.verdict == "PROMOTE"
    assert verdict.run_id == "run-1"


def test_hold_when_latest_result_failed(db):
    _seed_run(db, "orders_reconciliation", "FAILED")
    verdict = evaluate_gate("orders_reconciliation", db)
    assert verdict.verdict == "HOLD"
    assert any("FAILED" in r for r in verdict.reasons)


def test_hold_when_no_run_exists(db):
    verdict = evaluate_gate("orders_reconciliation", db)
    assert verdict.verdict == "HOLD"
    assert any("no run" in r.lower() for r in verdict.reasons)


def test_hold_on_open_contract_breach(db):
    _seed_run(db, "orders_reconciliation", "PASSED")
    contract = Contract(name="orders_contract", source_job="orders_reconciliation",
                        owner="team-data", sla_hours=4.0)
    db.add(contract)
    db.flush()
    db.add(ContractBreach(contract_id=contract.id, run_id="run-0",
                          breach_type="dq_violation"))
    db.commit()
    verdict = evaluate_gate("orders_reconciliation", db)
    assert verdict.verdict == "HOLD"
    assert any("breach" in r.lower() for r in verdict.reasons)


def test_latest_result_wins(db):
    _seed_run(db, "orders_reconciliation", "FAILED", run_id="run-old")
    db.add(TestRun(run_id="run-new", status="COMPLETED"))
    db.add(TestResult(
        run_id="run-new", query_name="orders_reconciliation", status="PASSED",
        executed_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
    ))
    db.commit()
    assert evaluate_gate("orders_reconciliation", db).verdict == "PROMOTE"
