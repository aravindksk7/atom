"""Tests for /api/sequences CRUD, validation, and usage endpoints."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def client(monkeypatch):
    from api.main import app
    from etl_framework.repository.database import Base
    from etl_framework.repository import database as _db_module
    import etl_framework.repository.models  # noqa: F401
    from etl_framework.repository.repository import JobRepository, TokenRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr("api.routes.sequences._execute_run", lambda *a, **k: None)
    # The batch runner waits for each run to finish, and stubbed runs never do.
    # It also used the route's own SessionLocal (ATOM's real database), so these
    # tests only passed when some earlier test had created its tables.
    monkeypatch.setattr("api.routes.sequences.run_batch", lambda *a, **k: None)

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test-runner")
        for name in ("orders_recon", "load_orders"):
            JobRepository(db).create({
                "name": name, "description": "", "tags": [],
                "job_type": "reconciliation", "query": "SELECT 1",
                "key_columns": ["id"], "exclude_columns": [], "params": {},
                "enabled": True,
            })

    return TestClient(app, headers={"Authorization": f"Bearer {raw}"})


CHAIN = [
    {"step_id": "load", "job_name": "load_orders", "depends_on": []},
    {"step_id": "recon", "job_name": "orders_recon", "depends_on": ["load"]},
]


def _create(client, name="nightly", steps=None):
    return client.post("/api/sequences", json={
        "name": name, "description": "d", "tags": ["t"], "steps": steps if steps is not None else CHAIN,
    })


def test_create_returns_201_with_version_one(client):
    resp = _create(client)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["latest_version"] == 1
    assert body["step_count"] == 2


def test_create_rejects_duplicate_name(client):
    _create(client)
    assert _create(client).status_code == 409


def test_create_rejects_cycle_with_422(client):
    resp = _create(client, steps=[
        {"step_id": "a", "job_name": "orders_recon", "depends_on": ["b"]},
        {"step_id": "b", "job_name": "load_orders", "depends_on": ["a"]},
    ])
    assert resp.status_code == 422
    assert any("cycle" in e["message"].lower() for e in resp.json()["detail"])


def test_create_rejects_unknown_job(client):
    resp = _create(client, steps=[{"step_id": "a", "job_name": "ghost", "depends_on": []}])
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["field"] == "job_name"


def test_create_rejects_empty_steps(client):
    resp = _create(client, steps=[])
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["field"] == "steps"


def test_create_accepts_a_trigger_rule(client):
    resp = _create(client, steps=[
        {"step_id": "a", "job_name": "orders_recon", "depends_on": []},
        {"step_id": "cleanup", "job_name": "load_orders", "depends_on": ["a"],
         "trigger_rule": "all_done"},
    ])
    assert resp.status_code == 201, resp.text



def test_create_accepts_retry_and_failure_policy(client):
    resp = _create(client, steps=[
        {"step_id": "a", "job_name": "orders_recon", "depends_on": [],
         "max_retries": 3, "retry_delay_seconds": 10, "on_failure": "continue"},
    ])
    assert resp.status_code == 201, resp.text


def test_create_accepts_preconditions(client):
    resp = client.post("/api/sequences", json={
        "name": "gated", "steps": CHAIN,
        "preconditions": {
            "time_window": {"start": "01:00", "end": "05:00"},
            "weekdays": [0, 1, 2, 3, 4],
        },
    })
    assert resp.status_code == 201, resp.text

    detail = client.get(f"/api/sequences/{resp.json()['id']}").json()
    stored = detail["versions"][0]["preconditions"]
    assert stored["time_window"] == {"start": "01:00", "end": "05:00"}
    assert stored["weekdays"] == [0, 1, 2, 3, 4]


def test_validate_endpoint_accepts_preconditions(client):
    resp = client.post("/api/sequences/validate", json={
        "steps": CHAIN, "preconditions": {"weekdays": [0]},
    })
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_get_returns_detail_with_versions(client):
    seq_id = _create(client).json()["id"]
    body = client.get(f"/api/sequences/{seq_id}").json()
    assert len(body["versions"]) == 1
    assert [s["step_id"] for s in body["versions"][0]["steps"]] == ["load", "recon"]


def test_list_excludes_archived(client):
    seq_id = _create(client).json()["id"]
    assert len(client.get("/api/sequences").json()) == 1
    assert client.delete(f"/api/sequences/{seq_id}").status_code == 204
    assert client.get("/api/sequences").json() == []
    assert len(client.get("/api/sequences?include_archived=true").json()) == 1


def test_patch_updates_metadata_only(client):
    seq_id = _create(client).json()["id"]
    body = client.patch(f"/api/sequences/{seq_id}", json={"name": "renamed"}).json()
    assert body["name"] == "renamed"
    assert body["latest_version"] == 1


def test_post_version_increments_and_validates(client):
    seq_id = _create(client).json()["id"]
    resp = client.post(f"/api/sequences/{seq_id}/versions", json={
        "steps": [{"step_id": "solo", "job_name": "orders_recon", "depends_on": []}],
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["version_number"] == 2
    assert client.get(f"/api/sequences/{seq_id}/versions/1").json()["version_number"] == 1


def test_get_missing_version_returns_404(client):
    seq_id = _create(client).json()["id"]
    assert client.get(f"/api/sequences/{seq_id}/versions/9").status_code == 404


def test_validate_endpoint_reports_order_when_valid(client):
    resp = client.post("/api/sequences/validate", json={"steps": CHAIN})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["errors"] == []
    assert body["order"] == ["load", "recon"]
    assert body["flow_mode"] == "serial"


def test_validate_endpoint_reports_errors_without_persisting(client):
    resp = client.post("/api/sequences/validate", json={
        "steps": [{"step_id": "a", "job_name": "ghost", "depends_on": []}],
    })
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert client.get("/api/sequences").json() == []


def test_usage_is_empty_for_a_fresh_sequence(client):
    seq_id = _create(client).json()["id"]
    assert client.get(f"/api/sequences/{seq_id}/usage").json() == {
        "selections": [], "schedules": [],
    }


def test_launch_creates_run_and_returns_202(client):
    created = _create(client).json()
    resp = client.post(f"/api/sequences/{created['id']}/launch",
                        json={"source_env": "dev", "target_env": "qa"})
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "PENDING"
    assert resp.json()["run_id"]


def test_launch_batch_creates_batch_for_date_variable(client):
    from etl_framework.repository.database import SessionLocal
    from etl_framework.repository.repository import CustomVariableRepository

    with SessionLocal() as db:
        CustomVariableRepository(db).create("business_date", "date", "today", "")
    created = _create(client).json()
    resp = client.post(f"/api/sequences/{created['id']}/launch-batch", json={
        "source_env": "dev", "target_env": "qa",
        "batch": {
            "variable_name": "business_date", "start_value": "2026-09-11",
            "iterations": 3, "step_days": 1, "weekend_policy": "skip",
        },
    })
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["target_type"] == "sequence"
    assert body["iterations"] == 3


def test_launch_batch_rejects_non_date_variable(client):
    from etl_framework.repository.database import SessionLocal
    from etl_framework.repository.repository import CustomVariableRepository

    with SessionLocal() as db:
        CustomVariableRepository(db).create("batch_id", "text", "B1", "")
    created = _create(client).json()
    resp = client.post(f"/api/sequences/{created['id']}/launch-batch", json={
        "source_env": "dev", "target_env": "qa",
        "batch": {"variable_name": "batch_id", "start_value": "2026-09-11", "iterations": 3},
    })
    assert resp.status_code == 422


def test_launch_stores_sequence_provenance_on_run(client):
    created = _create(client).json()
    resp = client.post(f"/api/sequences/{created['id']}/launch",
                        json={"source_env": "dev", "target_env": "qa"})
    run_id = resp.json()["run_id"]

    from etl_framework.repository import database as _db_module
    from etl_framework.repository.repository import RunRepository
    with _db_module.SessionLocal() as db:
        run = RunRepository(db).get_run(run_id)
        assert run.selection_id is None
        assert run.config_snapshot["sequence"] == {
            "id": created["id"], "name": "nightly", "version": 1,
        }


def test_launch_stores_ci_context_on_run(client):
    created = _create(client).json()
    ctx = {"commit_sha": "deadbeef", "pipeline_url": "https://gitlab.example.com/p/9", "ref": "main"}
    resp = client.post(
        f"/api/sequences/{created['id']}/launch",
        json={"source_env": "dev", "target_env": "qa", "ci_context": ctx},
    )
    assert resp.status_code == 202

    from etl_framework.repository import database as _db_module
    from etl_framework.repository.repository import RunRepository
    with _db_module.SessionLocal() as db:
        run = RunRepository(db).get_run(resp.json()["run_id"])
        assert run.ci_context == ctx


def test_launch_unknown_sequence_returns_404(client):
    resp = client.post("/api/sequences/999/launch", json={"source_env": "dev"})
    assert resp.status_code == 404


def test_launch_unknown_pinned_version_returns_404(client):
    created = _create(client).json()
    resp = client.post(f"/api/sequences/{created['id']}/launch",
                        json={"source_env": "dev", "version": 99})
    assert resp.status_code == 404


def test_launch_falls_back_to_sequence_default_source_env(client):
    resp = client.post("/api/sequences", json={
        "name": "with-defaults", "description": "", "tags": [], "steps": CHAIN,
        "defaults": {"source_env": "dev", "target_env": "qa"},
    })
    seq_id = resp.json()["id"]
    launch_resp = client.post(f"/api/sequences/{seq_id}/launch", json={})
    assert launch_resp.status_code == 202, launch_resp.text


def test_launch_without_source_env_or_default_returns_422(client):
    created = _create(client).json()
    resp = client.post(f"/api/sequences/{created['id']}/launch", json={})
    assert resp.status_code == 422
    assert "source_env" in resp.json()["detail"]


def test_launch_precondition_failure_creates_no_run(client):
    resp = client.post("/api/sequences", json={
        "name": "gated", "description": "", "tags": [], "steps": CHAIN,
        "preconditions": {"weekdays": []},  # no day is ever allowed
    })
    seq_id = resp.json()["id"]
    launch_resp = client.post(f"/api/sequences/{seq_id}/launch",
                               json={"source_env": "dev", "target_env": "qa"})
    assert launch_resp.status_code == 422

    from etl_framework.repository import database as _db_module
    from etl_framework.repository.repository import RunRepository
    with _db_module.SessionLocal() as db:
        assert RunRepository(db).list_runs(limit=50) == []


def test_launch_dual_env_job_without_target_fails_clearly(client):
    created = _create(client).json()
    resp = client.post(f"/api/sequences/{created['id']}/launch",
                        json={"source_env": "dev"})
    assert resp.status_code == 422
    assert "load_orders" in resp.json()["detail"]
    assert "target_env" in resp.json()["detail"]


HYBRID_STAGES = [
    {"mode": "serial", "steps": [{"step_id": "a", "job_name": "load_orders"}]},
    {"mode": "parallel", "steps": [
        {"step_id": "b", "job_name": "orders_recon"},
        {"step_id": "c", "job_name": "load_orders"},
    ]},
    {"mode": "serial", "steps": [{"step_id": "d", "job_name": "orders_recon"}]},
]


def _edges(steps):
    return {s["step_id"]: s["depends_on"] for s in steps}


def test_create_with_stages_stores_compiled_edges(client):
    resp = client.post("/api/sequences", json={"name": "hybrid", "stages": HYBRID_STAGES})
    assert resp.status_code == 201, resp.text
    assert resp.json()["step_count"] == 4

    version = client.get(f"/api/sequences/{resp.json()['id']}").json()["versions"][0]
    assert _edges(version["steps"]) == {"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]}
    assert version["flow_mode"] == "custom"
    assert [s["mode"] for s in version["stages"]] == ["serial", "parallel", "serial"]


def test_create_with_flow_mode(client):
    for mode, expected in (("serial", {"a": [], "b": ["a"]}), ("parallel", {"a": [], "b": []})):
        resp = client.post("/api/sequences", json={
            "name": f"flat-{mode}", "flow_mode": mode,
            "steps": [
                {"step_id": "a", "job_name": "load_orders"},
                {"step_id": "b", "job_name": "orders_recon"},
            ],
        })
        assert resp.status_code == 201, resp.text
        version = client.get(f"/api/sequences/{resp.json()['id']}").json()["versions"][0]
        assert _edges(version["steps"]) == expected
        assert version["flow_mode"] == mode


def test_stage_step_with_depends_on_is_rejected(client):
    resp = client.post("/api/sequences", json={"name": "bad", "stages": [
        {"mode": "serial", "steps": [{"step_id": "a", "job_name": "load_orders", "depends_on": []}]},
    ]})
    assert resp.status_code == 422


def test_stage_issue_uses_the_sequence_issue_shape(client):
    resp = client.post("/api/sequences", json={"name": "bad", "stages": [
        {"mode": "serial", "steps": [{"step_id": "a", "job_name": "ghost"}]},
    ]})
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["field"] == "job_name"


def test_root_all_failed_in_stages_is_rejected(client):
    resp = client.post("/api/sequences", json={"name": "bad", "stages": [
        {"mode": "serial", "steps": [
            {"step_id": "a", "job_name": "load_orders", "trigger_rule": "all_failed"},
        ]},
    ]})
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["field"] == "trigger_rule"


def test_new_version_accepts_stages(client):
    seq_id = _create(client).json()["id"]
    resp = client.post(f"/api/sequences/{seq_id}/versions", json={"stages": HYBRID_STAGES})
    assert resp.status_code == 201, resp.text
    assert resp.json()["flow_mode"] == "custom"
    assert _edges(resp.json()["steps"])["d"] == ["b", "c"]


def test_existing_steps_payload_derives_its_flow(client):
    seq_id = _create(client).json()["id"]
    version = client.get(f"/api/sequences/{seq_id}").json()["versions"][0]
    assert version["flow_mode"] == "serial"
    assert [s["mode"] for s in version["stages"]] == ["serial"]


def test_graph_that_is_not_stage_shaped_has_no_stages(client):
    seq_id = _create(client, steps=[
        {"step_id": "a", "job_name": "load_orders", "depends_on": []},
        {"step_id": "b", "job_name": "orders_recon", "depends_on": ["a"]},
        {"step_id": "c", "job_name": "load_orders", "depends_on": []},
        {"step_id": "d", "job_name": "orders_recon", "depends_on": ["c"]},
    ]).json()["id"]
    version = client.get(f"/api/sequences/{seq_id}").json()["versions"][0]
    assert version["flow_mode"] == "custom"
    assert version["stages"] is None


def test_validate_returns_compiled_flow(client):
    body = client.post("/api/sequences/validate", json={"stages": HYBRID_STAGES}).json()
    assert body["ok"] is True
    assert body["order"] == ["a", "b", "c", "d"]
    assert _edges(body["steps"])["d"] == ["b", "c"]
    assert body["flow_mode"] == "custom"
    assert len(body["stages"]) == 3


def test_validate_of_nothing_is_not_ok(client):
    body = client.post("/api/sequences/validate", json={}).json()
    assert body["ok"] is False
    assert body["errors"][0]["field"] == "steps"


def test_invalid_run_settings_are_rejected_on_save(client):
    resp = client.post("/api/sequences", json={
        "name": "bad-settings", "steps": CHAIN,
        "defaults": {"run_settings": {"max_workers": 0}},
    })
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["field"] == "run_settings"

    seq_id = _create(client).json()["id"]
    resp = client.post(f"/api/sequences/{seq_id}/versions", json={
        "steps": CHAIN, "defaults": {"run_settings": {"not_a_setting": 1}},
    })
    assert resp.status_code == 422


def test_job_dependency_between_siblings_is_rejected_on_save(client):
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.repository import JobRepository

    with _db_module.SessionLocal() as db:
        JobRepository(db).create({
            "name": "needs_load", "description": "", "tags": [],
            "job_type": "reconciliation", "query": "SELECT 1",
            "key_columns": ["id"], "exclude_columns": [],
            "params": {"depends_on": ["load_orders"]}, "enabled": True,
        })

    resp = client.post("/api/sequences", json={"name": "sib", "stages": [
        {"mode": "parallel", "steps": [
            {"step_id": "a", "job_name": "load_orders"},
            {"step_id": "b", "job_name": "needs_load"},
        ]},
    ]})
    assert resp.status_code == 422
    assert "depends on 'load_orders'" in resp.json()["detail"][0]["message"]


def _launched_settings(client, monkeypatch, defaults, launch):
    captured = {}
    monkeypatch.setattr(
        "api.routes.sequences._execute_run",
        lambda run_id, steps, source, target, settings, snapshot: captured.update(
            settings=settings, snapshot=snapshot, steps=steps,
        ),
    )
    seq_id = client.post("/api/sequences", json={
        "name": "workers", "steps": CHAIN, "defaults": defaults,
    }).json()["id"]
    resp = client.post(f"/api/sequences/{seq_id}/launch", json={
        "source_env": "dev", "target_env": "prod", **launch,
    })
    assert resp.status_code == 202, resp.text
    return captured


def test_sequence_default_max_workers_applies(client, monkeypatch):
    got = _launched_settings(client, monkeypatch, {"run_settings": {"max_workers": 7}}, {})
    assert got["settings"].max_workers == 7


def test_launch_override_wins_and_keeps_other_settings(client, monkeypatch):
    got = _launched_settings(
        client, monkeypatch,
        {"run_settings": {"max_workers": 7, "max_retries": 2}},
        {"max_workers": 1},
    )
    assert got["settings"].max_workers == 1
    assert got["settings"].max_retries == 2
    assert got["snapshot"]["run_settings"]["max_workers"] == 1


def test_sequential_mode_pins_one_worker_despite_a_launch_override(client, monkeypatch):
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.repository import RunStepRepository
    from api.services.run_executor import RunExecutor

    got = _launched_settings(
        client, monkeypatch,
        {"run_settings": {"execution_mode": "sequential", "max_workers": 7}},
        {"max_workers": 3},
    )
    assert got["settings"].execution_mode == "sequential"
    assert got["settings"].max_workers == 3

    captured = {}
    monkeypatch.setattr("api.services.dag_executor.DagExecutor", lambda **kw: captured.update(kw))
    with _db_module.SessionLocal() as db:
        RunExecutor(
            db=db, run_id="any", source_env="dev", target_env="prod",
            job_sequence=[], run_settings=got["settings"],
        )._build_dag_executor(RunStepRepository(db), {})
    assert captured["max_workers"] == 1


def test_launch_batch_passes_max_workers_to_each_iteration(client, monkeypatch):
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.repository import CustomVariableRepository, RunRepository

    # The route binds SessionLocal when it is imported, so the fixture's patch of
    # the database module does not reach run_batch. Without this it would use the
    # real engine, where run_batches may not exist.
    factory = _db_module.SessionLocal
    monkeypatch.setattr("api.routes.sequences.SessionLocal", factory)
    # The fixture stubs the batch runner; this test needs the real one.
    from api.services.batch_launch import run_batch
    monkeypatch.setattr("api.routes.sequences.run_batch", run_batch)

    captured = []

    def fake_execute(run_id, steps, source, target, settings, snapshot):
        captured.append(settings.max_workers)
        # run_batch waits for each iteration to reach a terminal status.
        with factory() as db:
            RunRepository(db).update_run_status(run_id, "PASSED")

    monkeypatch.setattr("api.routes.sequences._execute_run", fake_execute)

    with factory() as db:
        CustomVariableRepository(db).create("business_date", "date", "today", "")
    created = _create(client).json()
    resp = client.post(f"/api/sequences/{created['id']}/launch-batch", json={
        "source_env": "dev", "target_env": "qa", "max_workers": 3,
        "batch": {
            "variable_name": "business_date", "start_value": "2026-09-11",
            "iterations": 2, "step_days": 1, "weekend_policy": "ignore",
        },
    })
    assert resp.status_code == 202, resp.text
    assert captured == [3, 3]


def test_launch_override_rejects_zero(client):
    seq_id = _create(client).json()["id"]
    resp = client.post(f"/api/sequences/{seq_id}/launch", json={
        "source_env": "dev", "target_env": "prod", "max_workers": 0,
    })
    assert resp.status_code == 422
