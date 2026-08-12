"""ExecutionSequence ORM models and repository."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def db():
    from etl_framework.repository.database import Base
    import etl_framework.repository.models  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_new_tables_are_created(db):
    tables = set(inspect(db.get_bind()).get_table_names())
    assert {"execution_sequences", "execution_sequence_versions"} <= tables


def test_job_selection_version_has_sequence_ref_column(db):
    cols = {c["name"] for c in inspect(db.get_bind()).get_columns("job_selection_versions")}
    assert "sequence_ref" in cols


def test_scheduled_run_has_sequence_columns(db):
    cols = {c["name"] for c in inspect(db.get_bind()).get_columns("scheduled_runs")}
    assert {"sequence_id", "sequence_version"} <= cols


STEPS_V1 = [{"step_id": "a", "job_name": "orders_recon", "depends_on": []}]
STEPS_V2 = [
    {"step_id": "a", "job_name": "orders_recon", "depends_on": []},
    {"step_id": "b", "job_name": "load_orders", "depends_on": ["a"]},
]


def _repo(db):
    from etl_framework.repository.sequence_repository import ExecutionSequenceRepository
    return ExecutionSequenceRepository(db)


def test_create_writes_version_one(db):
    seq = _repo(db).create(name="nightly", description="d", tags=["t"], steps=STEPS_V1)
    assert seq.id is not None
    version = _repo(db).latest_version(seq.id)
    assert version.version_number == 1
    assert version.steps_json == STEPS_V1


def test_create_new_version_increments(db):
    seq = _repo(db).create(name="nightly", description="", tags=[], steps=STEPS_V1)
    version = _repo(db).create_new_version(seq.id, steps=STEPS_V2)
    assert version.version_number == 2
    assert _repo(db).latest_version(seq.id).steps_json == STEPS_V2
    assert _repo(db).get_version(seq.id, 1).steps_json == STEPS_V1


def test_get_by_name(db):
    _repo(db).create(name="nightly", description="", tags=[], steps=STEPS_V1)
    assert _repo(db).get_by_name("nightly") is not None
    assert _repo(db).get_by_name("missing") is None


def test_list_hides_archived_by_default(db):
    seq = _repo(db).create(name="nightly", description="", tags=[], steps=STEPS_V1)
    _repo(db).archive_or_raise(seq.id)
    assert _repo(db).list() == []
    assert len(_repo(db).list(include_archived=True)) == 1


def test_archive_raises_when_an_enabled_schedule_references_it(db):
    from etl_framework.repository.models import ScheduledRun
    seq = _repo(db).create(name="nightly", description="", tags=[], steps=STEPS_V1)
    db.add(ScheduledRun(name="s1", cron_expr="0 1 * * *", sequence_id=seq.id, enabled=True))
    db.commit()
    with pytest.raises(ValueError, match="enabled schedule"):
        _repo(db).archive_or_raise(seq.id)


def test_usage_lists_referencing_schedules_and_selections(db):
    from etl_framework.repository.models import JobSelection, JobSelectionVersion, ScheduledRun
    seq = _repo(db).create(name="nightly", description="", tags=[], steps=STEPS_V1)
    db.add(ScheduledRun(name="s1", cron_expr="0 1 * * *", sequence_id=seq.id,
                        sequence_version=1, enabled=True))
    selection = JobSelection(name="sel", description="", tags=[])
    db.add(selection)
    db.flush()
    db.add(JobSelectionVersion(
        selection_id=selection.id, version_number=1, job_sequence=[],
        run_settings_json={}, sequence_ref={"sequence_id": seq.id, "sequence_version": None},
    ))
    db.commit()

    usage = _repo(db).usage(seq.id)
    assert [s["name"] for s in usage["schedules"]] == ["s1"]
    assert [s["name"] for s in usage["selections"]] == ["sel"]


def test_update_metadata_changes_name_and_archived(db):
    seq = _repo(db).create(name="nightly", description="", tags=[], steps=STEPS_V1)
    updated = _repo(db).update_metadata(seq.id, name="renamed", archived=True)
    assert updated.name == "renamed"
    assert updated.archived is True
