"""run_steps columns backing DAG execution."""
from __future__ import annotations

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401


def _db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def test_run_steps_has_dag_columns():
    cols = {c["name"] for c in inspect(_db().get_bind()).get_columns("run_steps")}
    assert {"step_id", "depends_on", "trigger_rule", "attempt", "max_retries", "on_failure"} <= cols


from api.schemas import SequenceStepRef
from etl_framework.repository.repository import RunRepository, RunStepRepository

DIAMOND = [
    SequenceStepRef(step_id="root", job_name="j0"),
    SequenceStepRef(step_id="left", job_name="j1", depends_on=["root"]),
    SequenceStepRef(step_id="right", job_name="j2", depends_on=["root"], trigger_rule="all_done"),
]


def test_materialize_persists_dag_fields():
    db = _db()
    RunRepository(db).create_run("r-1", "dev", "prod", {})
    RunStepRepository(db).materialize_steps("r-1", DIAMOND)

    rows = RunStepRepository(db).list_steps("r-1")
    assert [r.step_id for r in rows] == ["root", "left", "right"]
    assert [r.step_index for r in rows] == [0, 1, 2]
    assert rows[1].depends_on == ["root"]
    assert rows[2].trigger_rule == "all_done"


def test_set_status_by_step_id():
    db = _db()
    RunRepository(db).create_run("r-2", "dev", "prod", {})
    repo = RunStepRepository(db)
    repo.materialize_steps("r-2", DIAMOND)
    repo.set_status_by_step_id("r-2", "left", "BLOCKED")

    assert repo.get_step_by_step_id("r-2", "left").status == "BLOCKED"


def test_get_release_returns_none_while_held():
    db = _db()
    RunRepository(db).create_run("r-3", "dev", "prod", {})
    repo = RunStepRepository(db)
    repo.materialize_steps("r-3", DIAMOND)
    repo.set_status_by_step_id("r-3", "root", "HELD")

    assert repo.get_release_by_step_id("r-3", "root") is None


def test_get_release_treats_a_missing_materialized_step_as_approved():
    db = _db()
    RunRepository(db).create_run("r-missing", "dev", "prod", {})

    assert RunStepRepository(db).get_release_by_step_id("r-missing", "missing") == "approve"


def test_get_release_returns_the_action_after_release():
    db = _db()
    RunRepository(db).create_run("r-4", "dev", "prod", {})
    repo = RunStepRepository(db)
    repo.materialize_steps("r-4", DIAMOND)
    repo.set_status_by_step_id("r-4", "root", "HELD")
    repo.release_step("r-4", 0, "skip", "not needed", "alice")

    assert repo.get_release_by_step_id("r-4", "root") == "skip"


def test_legacy_string_steps_still_materialize():
    # Plain SequenceStep objects (no step_id) must keep working.
    from api.schemas import SequenceStep
    db = _db()
    RunRepository(db).create_run("r-5", "dev", "prod", {})
    RunStepRepository(db).materialize_steps("r-5", [SequenceStep(job_name="a")])

    row = RunStepRepository(db).list_steps("r-5")[0]
    assert (row.job_name, row.step_index, row.trigger_rule) == ("a", 0, "all_success")
