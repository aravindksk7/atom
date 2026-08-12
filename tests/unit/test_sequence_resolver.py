"""Resolving a SequenceRef into an ordered, executable sequence."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


@pytest.fixture
def db():
    from etl_framework.repository.database import Base
    import etl_framework.repository.models  # noqa: F401
    from etl_framework.repository.repository import JobRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for name in ("orders_recon", "load_orders"):
            JobRepository(session).create({
                "name": name, "description": "", "tags": [],
                "job_type": "reconciliation", "query": "SELECT 1",
                "key_columns": ["id"], "exclude_columns": [], "params": {},
                "enabled": True,
            })
        yield session


BRANCHED = [
    {"step_id": "load", "job_name": "load_orders", "depends_on": []},
    {"step_id": "recon_b", "job_name": "orders_recon", "depends_on": ["load"]},
    {"step_id": "recon_a", "job_name": "orders_recon", "depends_on": ["load"]},
]


def _make(db, steps=None, defaults=None):
    from etl_framework.repository.sequence_repository import ExecutionSequenceRepository
    return ExecutionSequenceRepository(db).create(
        name="nightly", description="", tags=[], steps=steps or BRANCHED,
        defaults=defaults or {},
    )


def test_resolve_latest_returns_topological_order(db):
    from api.schemas import SequenceRef
    from api.services.sequence_resolver import resolve
    seq = _make(db)
    resolved = resolve(db, SequenceRef(sequence_id=seq.id))
    assert [s.step_id for s in resolved.steps] == ["load", "recon_b", "recon_a"]
    assert resolved.version_number == 1
    assert resolved.sequence_name == "nightly"


def test_resolve_pins_an_explicit_version(db):
    from api.schemas import SequenceRef
    from api.services.sequence_resolver import resolve
    from etl_framework.repository.sequence_repository import ExecutionSequenceRepository
    seq = _make(db, steps=[{"step_id": "a", "job_name": "orders_recon", "depends_on": []}])
    ExecutionSequenceRepository(db).create_new_version(seq.id, steps=BRANCHED)
    resolved = resolve(db, SequenceRef(sequence_id=seq.id, sequence_version=1))
    assert [s.step_id for s in resolved.steps] == ["a"]


def test_resolve_raises_for_missing_sequence(db):
    from api.schemas import SequenceRef
    from api.services.sequence_resolver import SequenceResolutionError, resolve
    with pytest.raises(SequenceResolutionError, match="not found"):
        resolve(db, SequenceRef(sequence_id=999))


def test_resolve_raises_for_missing_version(db):
    from api.schemas import SequenceRef
    from api.services.sequence_resolver import SequenceResolutionError, resolve
    seq = _make(db)
    with pytest.raises(SequenceResolutionError, match="version"):
        resolve(db, SequenceRef(sequence_id=seq.id, sequence_version=7))


def test_resolve_raises_naming_every_missing_job(db):
    from api.schemas import SequenceRef
    from api.services.sequence_resolver import SequenceResolutionError, resolve
    seq = _make(db, steps=[
        {"step_id": "a", "job_name": "ghost_one", "depends_on": []},
        {"step_id": "b", "job_name": "ghost_two", "depends_on": ["a"]},
    ])
    with pytest.raises(SequenceResolutionError) as exc:
        resolve(db, SequenceRef(sequence_id=seq.id))
    assert "ghost_one" in str(exc.value)
    assert "ghost_two" in str(exc.value)


def test_as_linear_steps_drops_dag_only_fields(db):
    from api.schemas import SequenceRef, SequenceStep
    from api.services.sequence_resolver import resolve
    seq = _make(db, steps=[
        {"step_id": "a", "job_name": "orders_recon", "depends_on": [],
         "hold_after": True, "wait_seconds": 3,
         "condition": {"require_status": ["PASSED"], "max_mismatch_count": 5}},
    ])
    linear = resolve(db, SequenceRef(sequence_id=seq.id)).as_linear_steps()
    assert all(isinstance(s, SequenceStep) for s in linear)
    assert linear[0].job_name == "orders_recon"
    assert linear[0].hold_after is True
    assert linear[0].wait_seconds == 3
    assert linear[0].condition.max_mismatch_count == 5


def test_defaults_are_returned(db):
    from api.schemas import SequenceRef
    from api.services.sequence_resolver import resolve
    seq = _make(db, defaults={"source_env": "dev", "config_id": 4})
    resolved = resolve(db, SequenceRef(sequence_id=seq.id))
    assert resolved.defaults.source_env == "dev"
    assert resolved.defaults.config_id == 4
