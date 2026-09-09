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
    from etl_framework.repository.repository import TokenRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test-runner")

    return TestClient(app, headers={"Authorization": f"Bearer {raw}"})


def test_launch_selection_resolves_variables_into_snapshot(client, monkeypatch):
    client.post("/api/variables", json={"name": "run_date", "var_type": "date", "default_value": "2026-09-08"})
    cfg = client.post("/api/configs", json={"name": "dev-vars", "env_name": "dev", "config_data": {}}).json()
    selection = client.post("/api/selections", json={
        "name": "sel-vars", "description": "", "tags": [], "job_sequence": [],
    }).json()

    captured = {}
    monkeypatch.setattr(
        "api.routes.selections._execute_run",
        lambda run_id, job_sequence, source_env, target_env, run_settings, config_snapshot: captured.update(
            config_snapshot=config_snapshot
        ),
    )

    resp = client.post(
        f"/api/selections/{selection['id']}/launch",
        json={"source_env": "dev", "target_env": "", "config_id": cfg["id"],
              "variable_overrides": {"run_date": "2026-09-09"}},
    )
    assert resp.status_code == 202, resp.text
    assert captured["config_snapshot"]["variables"] == {"run_date": "2026-09-09"}


def test_launch_sequence_resolves_variables_into_snapshot(client, monkeypatch):
    client.post("/api/jobs", json={
        "name": "seq_job", "description": "", "tags": [],
        "job_type": "reconciliation", "query": "SELECT 1", "key_columns": ["id"],
        "exclude_columns": [], "params": {}, "enabled": True,
    })
    client.post("/api/variables", json={"name": "run_date", "var_type": "date", "default_value": "2026-09-08"})
    cfg = client.post("/api/configs", json={"name": "dev-vars-seq", "env_name": "dev", "config_data": {}}).json()
    sequence = client.post("/api/sequences", json={
        "name": "seq-vars", "description": "", "tags": [],
        "steps": [{"step_id": "a", "job_name": "seq_job", "depends_on": []}],
    }).json()

    captured = {}
    monkeypatch.setattr(
        "api.routes.sequences._execute_run",
        lambda run_id, job_sequence, source_env, target_env, run_settings, config_snapshot: captured.update(
            config_snapshot=config_snapshot
        ),
    )

    resp = client.post(
        f"/api/sequences/{sequence['id']}/launch",
        json={"source_env": "dev", "target_env": "qa", "config_id": cfg["id"],
              "variable_overrides": {"run_date": "2026-09-09"}},
    )
    assert resp.status_code == 202, resp.text
    assert captured["config_snapshot"]["variables"] == {"run_date": "2026-09-09"}


def test_resolve_variables_called_exactly_once_per_launch(client, monkeypatch):
    from api.routes import selections as selections_module

    client.post("/api/variables", json={"name": "run_date", "var_type": "date", "default_value": "today"})
    cfg = client.post("/api/configs", json={"name": "dev-once", "env_name": "dev", "config_data": {}}).json()
    selection = client.post("/api/selections", json={
        "name": "sel-once", "description": "", "tags": [], "job_sequence": [],
    }).json()

    monkeypatch.setattr(selections_module, "_execute_run", lambda *a, **k: None)

    call_count = {"n": 0}
    import api.services.variable_resolution as variable_resolution_module
    original = variable_resolution_module.resolve_variables

    def counting_wrapper(*args, **kwargs):
        call_count["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(variable_resolution_module, "resolve_variables", counting_wrapper)

    resp = client.post(
        f"/api/selections/{selection['id']}/launch",
        json={"source_env": "dev", "target_env": "", "config_id": cfg["id"]},
    )
    assert resp.status_code == 202, resp.text
    assert call_count["n"] == 1
