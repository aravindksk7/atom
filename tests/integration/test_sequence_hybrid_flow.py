"""Serial -> parallel -> serial runs through the real API and executor.

run_steps has no timestamps, so concurrency is proved with a barrier that can only
pass if group members run at the same time, and ordering with recorded events.
"""
from __future__ import annotations

import os
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.services.run_executor import RunExecutor
from etl_framework.repository.database import Base, _ensure_compare_columns
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import (
    JobRepository, RunRepository, RunStepRepository, TokenRepository,
)


@pytest.fixture
def engine(tmp_path):
    url = os.environ.get("HYBRID_TEST_DATABASE_URL")
    if url:
        eng = create_engine(url)
        Base.metadata.drop_all(eng)
    else:
        # Deliberately the same connect args as production, so this sees the same
        # locking behaviour under parallel writes.
        eng = create_engine(
            f"sqlite:///{tmp_path / 'hybrid.db'}", connect_args={"check_same_thread": False}
        )
    Base.metadata.create_all(eng)
    _ensure_compare_columns(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def factory(engine):
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


@pytest.fixture
def client(monkeypatch, factory):
    from api.main import app
    from api.dependencies import get_session
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.database import get_db

    monkeypatch.setattr(_db_module, "SessionLocal", factory)

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session] = override_get_db

    with factory() as db:
        raw, _ = TokenRepository(db).create("test-runner")
        for name, target in (("good", 1.0), ("bad", 2.0), ("boom", 1.0), ("extract", 1.0),
                             ("load_x", 1.0), ("load_y", 1.0), ("reconcile", 1.0), ("publish", 1.0)):
            JobRepository(db).create({
                "name": name, "description": "", "tags": [],
                "job_type": "reconciliation", "query": "SELECT 1",
                "key_columns": ["id"], "exclude_columns": [],
                "source_env": None, "target_env": None,
                "params": {
                    "source_rows": [{"id": 1, "amount": 1.0}],
                    "target_rows": [{"id": 1, "amount": target}],
                },
                "enabled": True,
            })

    yield TestClient(app, headers={"Authorization": f"Bearer {raw}"})

    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_session, None)


class Recorder:
    """Wraps each job body to record entry and exit and track concurrency."""

    def __init__(self, barrier_jobs=(), parties=2):
        self.events: list[str] = []
        self.active = 0
        self.peak = 0
        self._lock = threading.Lock()
        self._barrier = threading.Barrier(parties)
        self._barrier_jobs = set(barrier_jobs)

    def wrap(self, job, case):
        def run_case():
            with self._lock:
                self.events.append(f"enter:{job.name}")
                self.active += 1
                self.peak = max(self.peak, self.active)
            try:
                if job.name == "boom":
                    raise RuntimeError("boom")
                if job.name in self._barrier_jobs:
                    self._barrier.wait(timeout=20)
                return case()
            finally:
                with self._lock:
                    self.active -= 1
                    self.events.append(f"exit:{job.name}")
        return run_case


@pytest.fixture
def record(monkeypatch):
    def install(**kw) -> Recorder:
        recorder = Recorder(**kw)
        original = RunExecutor._build_case
        monkeypatch.setattr(
            RunExecutor, "_build_case",
            lambda self, job: recorder.wrap(job, original(self, job)),
        )
        return recorder
    return install


HYBRID = [
    {"mode": "serial", "steps": [{"step_id": "extract", "job_name": "extract"}]},
    {"mode": "parallel", "steps": [
        {"step_id": "load_x", "job_name": "load_x"},
        {"step_id": "load_y", "job_name": "load_y"},
    ]},
    {"mode": "serial", "steps": [
        {"step_id": "reconcile", "job_name": "reconcile"},
        {"step_id": "publish", "job_name": "publish"},
    ]},
]


def _launch(client, stages, name="flow", **launch):
    resp = client.post("/api/sequences", json={"name": name, "stages": stages})
    assert resp.status_code == 201, resp.text
    resp = client.post(f"/api/sequences/{resp.json()['id']}/launch", json={
        "source_env": "dev", "target_env": "prod", **launch,
    })
    assert resp.status_code == 202, resp.text
    return resp.json()["run_id"]


def _statuses(factory, run_id):
    with factory() as db:
        return {s.step_id: s.status for s in RunStepRepository(db).list_steps(run_id)}


def _run_status(factory, run_id):
    with factory() as db:
        return RunRepository(db).get_run(run_id).status


def _step(step_id, job, **kw):
    return {"step_id": step_id, "job_name": job, **kw}


def test_group_overlaps_and_neighbouring_stages_do_not(client, factory, record):
    recorder = record(barrier_jobs=("load_x", "load_y"))
    run_id = _launch(client, HYBRID)

    assert _run_status(factory, run_id) == "PASSED"
    assert set(_statuses(factory, run_id).values()) == {"PASSED"}
    assert recorder.peak == 2

    at = recorder.events.index
    assert at("exit:extract") < at("enter:load_x")
    assert at("exit:extract") < at("enter:load_y")
    assert at("exit:load_x") < at("enter:reconcile")
    assert at("exit:load_y") < at("enter:reconcile")
    assert at("exit:reconcile") < at("enter:publish")


def test_max_workers_one_removes_the_overlap(client, factory, record):
    recorder = record()
    run_id = _launch(client, HYBRID, max_workers=1)

    assert _run_status(factory, run_id) == "PASSED"
    assert recorder.peak == 1


@pytest.mark.parametrize("second_member,rule,expected_next,expected_run", [
    ("good", "all_success", "PASSED", "PASSED"),
    ("bad", "all_success", "BLOCKED", "BLOCKED"),
    ("boom", "all_success", "BLOCKED", "BLOCKED"),
    ("bad", "all_done", "PASSED", "FAILED"),
    ("boom", "all_done", "PASSED", "ERROR"),
    ("bad", "any_success", "PASSED", "FAILED"),
])
def test_handover_after_a_group(client, factory, record, second_member, rule, expected_next, expected_run):
    record()
    run_id = _launch(client, [
        {"mode": "parallel", "steps": [_step("m1", "good"), _step("m2", second_member)]},
        {"mode": "serial", "steps": [_step("next", "good", trigger_rule=rule)]},
    ])

    assert _statuses(factory, run_id)["next"] == expected_next
    assert _run_status(factory, run_id) == expected_run


def test_any_success_blocks_when_every_member_failed(client, factory, record):
    record()
    run_id = _launch(client, [
        {"mode": "parallel", "steps": [_step("m1", "bad"), _step("m2", "bad")]},
        {"mode": "serial", "steps": [_step("next", "good", trigger_rule="any_success")]},
    ])
    assert _statuses(factory, run_id)["next"] == "BLOCKED"


def test_member_that_never_ran_blocks_the_next_stage_whatever_its_rule(client, factory, record):
    record()
    run_id = _launch(client, [
        {"mode": "serial", "steps": [
            _step("a", "bad"),
            _step("b", "good"),
            _step("c", "good", trigger_rule="all_done"),
        ]},
    ])
    assert _statuses(factory, run_id) == {"a": "FAILED", "b": "BLOCKED", "c": "BLOCKED"}


def test_on_failure_stop_cancels_steps_not_started(client, factory, record):
    record()
    run_id = _launch(client, [
        {"mode": "parallel", "steps": [_step("m1", "bad", on_failure="stop"), _step("m2", "good")]},
        {"mode": "serial", "steps": [_step("next", "good")]},
    ])
    statuses = _statuses(factory, run_id)
    assert statuses["m1"] == "FAILED"
    assert statuses["m2"] == "PASSED"
    assert statuses["next"] == "CANCELLED"
    assert _run_status(factory, run_id) == "CANCELLED"


def test_on_failure_continue_still_blocks_all_success(client, factory, record):
    record()
    run_id = _launch(client, [
        {"mode": "parallel", "steps": [_step("m1", "bad", on_failure="continue"), _step("m2", "good")]},
        {"mode": "serial", "steps": [_step("next", "good")]},
    ])
    assert _statuses(factory, run_id)["next"] == "BLOCKED"


def test_restart_keeps_edges_and_max_workers(client, factory, record):
    record()
    run_id = _launch(client, [
        {"mode": "serial", "steps": [_step("a", "good")]},
        {"mode": "parallel", "steps": [_step("m1", "bad"), _step("m2", "good")]},
        {"mode": "serial", "steps": [_step("next", "good")]},
    ], max_workers=3)

    resp = client.post(f"/api/runs/{run_id}/restart")
    assert resp.status_code == 202, resp.text
    new_run_id = resp.json()["run_id"]

    with factory() as db:
        steps = {s.step_id: s for s in RunStepRepository(db).list_steps(new_run_id)}
        snapshot = RunRepository(db).get_run(new_run_id).config_snapshot

    assert steps["next"].depends_on == ["m1", "m2"]
    assert steps["m1"].depends_on == ["a"]
    assert steps["a"].carried_over is True
    assert snapshot["run_settings"]["max_workers"] == 3


def test_rerun_downstream_runs_later_steps_again(client, factory, record):
    # rerun_downstream: without the option `next` is carried over and never sees
    # the failed member's new result. With it, `next` runs again and `other` does not.
    recorder = record()
    run_id = _launch(client, [
        {"mode": "parallel", "steps": [_step("m1", "bad"), _step("m2", "good")]},
        {"mode": "serial", "steps": [_step("next", "reconcile", trigger_rule="all_done")]},
    ])
    assert _statuses(factory, run_id)["next"] == "PASSED"

    recorder.events.clear()
    resp = client.post(f"/api/runs/{run_id}/restart")
    assert resp.status_code == 202, resp.text
    assert recorder.events == ["enter:bad", "exit:bad"]

    recorder.events.clear()
    resp = client.post(f"/api/runs/{run_id}/restart", json={"rerun_downstream": True})
    assert resp.status_code == 202, resp.text
    new_run_id = resp.json()["run_id"]

    assert "enter:reconcile" in recorder.events
    assert "enter:good" not in recorder.events
    with factory() as db:
        steps = {s.step_id: s for s in RunStepRepository(db).list_steps(new_run_id)}
    assert steps["m2"].carried_over is True
    assert steps["next"].carried_over is False
