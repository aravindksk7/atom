"""Step timestamps, sequence_id and the upgrade of existing databases."""
from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.schemas import SequenceStepRef
from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import RunRepository, RunStepRepository


def _session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _step(db, run_id="r1"):
    RunRepository(db).create_run(run_id, "dev", "prod", {})
    RunStepRepository(db).materialize_steps(run_id, [
        SequenceStepRef(step_id="a", job_name="job_a"),
    ])
    return RunStepRepository(db)


def test_new_step_has_no_times():
    db = _session()
    repo = _step(db)
    step = repo.get_step_by_step_id("r1", "a")
    assert step.started_at is None
    assert step.finished_at is None


def test_running_sets_start_and_leaving_running_sets_end():
    db = _session()
    repo = _step(db)
    repo.set_status_by_step_id("r1", "a", "RUNNING")
    step = repo.get_step_by_step_id("r1", "a")
    assert step.started_at is not None
    assert step.finished_at is None

    repo.set_status_by_step_id("r1", "a", "PASSED")
    step = repo.get_step_by_step_id("r1", "a")
    assert step.finished_at is not None
    assert step.finished_at >= step.started_at


def test_running_again_does_not_reset_start():
    db = _session()
    repo = _step(db)
    repo.set_status_by_step_id("r1", "a", "RUNNING")
    first = repo.get_step_by_step_id("r1", "a").started_at
    repo.set_status_by_step_id("r1", "a", "RUNNING")
    assert repo.get_step_by_step_id("r1", "a").started_at == first


def test_later_status_changes_do_not_move_the_end():
    db = _session()
    repo = _step(db)
    repo.set_status_by_step_id("r1", "a", "RUNNING")
    repo.set_status_by_step_id("r1", "a", "HELD")
    held_end = repo.get_step_by_step_id("r1", "a").finished_at
    assert held_end is not None

    repo.set_status_by_step_id("r1", "a", "APPROVED")
    assert repo.get_step_by_step_id("r1", "a").finished_at == held_end


def test_never_started_step_gets_no_times():
    db = _session()
    repo = _step(db)
    repo.set_status_by_step_id("r1", "a", "BLOCKED")
    step = repo.get_step_by_step_id("r1", "a")
    assert step.started_at is None
    assert step.finished_at is None


def test_create_run_sets_sequence_id_from_snapshot():
    db = _session()
    run = RunRepository(db).create_run("r2", "dev", "prod", {"sequence": {"id": 7, "name": "s"}})
    assert run.sequence_id == 7


def test_create_run_without_sequence_leaves_it_empty():
    db = _session()
    assert RunRepository(db).create_run("r3", "dev", "prod", {}).sequence_id is None
    assert RunRepository(db).create_run("r4", "dev", "prod", None).sequence_id is None
    assert RunRepository(db).create_run("r5", "dev", "prod", {"sequence": "odd"}).sequence_id is None


def test_list_runs_for_sequence_is_newest_first_and_pages():
    db = _session()
    repo = RunRepository(db)
    for index in range(3):
        repo.create_run(f"s-{index}", "dev", "prod", {"sequence": {"id": 7}})
    repo.create_run("other", "dev", "prod", {"sequence": {"id": 8}})

    assert [r.run_id for r in repo.list_runs_for_sequence(7, limit=2, offset=0)] == ["s-2", "s-1"]
    assert [r.run_id for r in repo.list_runs_for_sequence(7, limit=2, offset=2)] == ["s-0"]


def _old_database():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE test_runs (id INTEGER PRIMARY KEY, run_id VARCHAR(36), config_snapshot JSON)"
        ))
        conn.execute(text("CREATE TABLE run_steps (id INTEGER PRIMARY KEY, run_id VARCHAR(36))"))
        conn.execute(text(
            "INSERT INTO test_runs (run_id, config_snapshot) VALUES "
            "('seq', '{\"sequence\": {\"id\": 5}}'), ('plain', '{}'), ('bad', 'not json'), ('none', NULL)"
        ))
    return engine


def test_upgrade_adds_columns_and_backfills_once():
    from etl_framework.repository.database import _ensure_run_timing_columns

    engine = _old_database()
    _ensure_run_timing_columns(engine)

    inspector = inspect(engine)
    assert {"started_at", "finished_at"} <= {c["name"] for c in inspector.get_columns("run_steps")}
    assert "sequence_id" in {c["name"] for c in inspector.get_columns("test_runs")}
    assert "ix_test_runs_sequence_id" in {i["name"] for i in inspector.get_indexes("test_runs")}

    with engine.connect() as conn:
        rows = dict(conn.execute(text("SELECT run_id, sequence_id FROM test_runs")).fetchall())
    assert rows == {"seq": 5, "plain": None, "bad": None, "none": None}

    # Second start: nothing to add, and no backfill overwriting current values.
    with engine.begin() as conn:
        conn.execute(text("UPDATE test_runs SET sequence_id = 99 WHERE run_id = 'seq'"))
    _ensure_run_timing_columns(engine)
    with engine.connect() as conn:
        value = conn.execute(text("SELECT sequence_id FROM test_runs WHERE run_id = 'seq'")).scalar()
    assert value == 99
