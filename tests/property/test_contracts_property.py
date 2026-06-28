"""Property-based tests for contract breach math invariants."""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone
from hypothesis import given, settings, assume
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
import etl_framework.repository.contract_models  # noqa: F401
from etl_framework.repository.contract_models import ContractBreach
from etl_framework.repository.contract_repository import ContractRepository


def _db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _make_contract(repo: ContractRepository, name: str, sla_hours: float) -> object:
    return repo.create({
        "name": name,
        "source_job": f"job_{name}",
        "owner": "test@co.com",
        "sla_hours": sla_hours,
        "consumers": [],
    })


# ---------------------------------------------------------------------------
# Invariant 1: duration_hours is always non-negative
# ---------------------------------------------------------------------------

@given(
    delay_seconds=st.integers(min_value=0, max_value=3600),
)
@settings(max_examples=50)
def test_duration_hours_always_non_negative(delay_seconds):
    db = _db()
    repo = ContractRepository(db)
    contract = _make_contract(repo, "prop_dur", sla_hours=24.0)

    repo.open_breach(contract.id, "run-001", "dq_violation")

    # Back-date the opened_at to simulate elapsed time
    raw = db.query(ContractBreach).filter(ContractBreach.contract_id == contract.id).first()
    raw.opened_at = datetime.now(timezone.utc) - timedelta(seconds=delay_seconds)
    db.commit()

    resolved = repo.resolve_breaches_for_job(f"job_prop_dur", "run-002")
    assert len(resolved) == 1
    breach, _ = resolved[0]
    assert breach.duration_hours >= 0


# ---------------------------------------------------------------------------
# Invariant 2: open_breach is idempotent (only one open breach per contract)
# ---------------------------------------------------------------------------

@given(n_attempts=st.integers(min_value=2, max_value=10))
@settings(max_examples=30)
def test_open_breach_idempotent(n_attempts):
    db = _db()
    repo = ContractRepository(db)
    contract = _make_contract(repo, "prop_idem", sla_hours=4.0)

    results = []
    for i in range(n_attempts):
        result = repo.open_breach(contract.id, f"run-{i:03d}", "dq_violation")
        results.append(result)

    # First call succeeds, rest return None
    assert results[0] is not None
    assert all(r is None for r in results[1:])
    # Only one open breach
    assert len(repo.list_open_breaches(contract.id)) == 1


# ---------------------------------------------------------------------------
# Invariant 3: version bump always increments monotonically
# ---------------------------------------------------------------------------

@given(
    bumps=st.lists(st.sampled_from(["minor", "major"]), min_size=1, max_size=8),
)
@settings(max_examples=40)
def test_version_bump_monotonically_increases(bumps):
    db = _db()
    repo = ContractRepository(db)
    _make_contract(repo, "prop_ver", sla_hours=4.0)

    versions = []
    for bump_type in bumps:
        cv = repo.bump_version("prop_ver", bump_type)
        versions.append(cv.version)

    # Each version must parse as two non-negative integers
    parsed = [tuple(int(x) for x in v.split(".")) for v in versions]

    # Each entry must be strictly greater than the previous
    for a, b in zip(parsed, parsed[1:]):
        assert b > a, f"Version {b} should be > {a}"


# ---------------------------------------------------------------------------
# Invariant 4: escalate_overdue never re-escalates already-escalated breaches
# ---------------------------------------------------------------------------

@given(n_escalation_calls=st.integers(min_value=2, max_value=6))
@settings(max_examples=30)
def test_escalate_overdue_never_double_escalates(n_escalation_calls):
    db = _db()
    repo = ContractRepository(db)
    contract = _make_contract(repo, "prop_esc", sla_hours=0.001)

    breach = repo.open_breach(contract.id, "run-001", "dq_violation")

    # Back-date opened_at so it's definitely overdue
    raw = db.query(ContractBreach).filter(ContractBreach.id == breach.id).first()
    raw.opened_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.commit()

    all_escalated = []
    for _ in range(n_escalation_calls):
        escalated = repo.escalate_overdue()
        all_escalated.extend(escalated)

    # Exactly one escalation should ever happen
    assert len(all_escalated) == 1
    # The breach must be marked escalated exactly once
    open_breaches = repo.list_open_breaches(contract.id)
    assert len(open_breaches) == 1
    assert open_breaches[0].escalated is True


# ---------------------------------------------------------------------------
# Invariant 5: resolve clears all open breaches for a job
# ---------------------------------------------------------------------------

@given(n_breaches=st.integers(min_value=0, max_value=1))
@settings(max_examples=20)
def test_resolve_then_status_is_ok(n_breaches):
    """After resolving, the contract status must always be OK."""
    db = _db()
    repo = ContractRepository(db)
    contract = _make_contract(repo, "prop_resolve", sla_hours=4.0)

    if n_breaches > 0:
        repo.open_breach(contract.id, "run-init", "dq_violation")

    repo.resolve_breaches_for_job("job_prop_resolve", "run-resolve")

    status = repo.get_status("prop_resolve")
    assert status["status"] == "OK"
