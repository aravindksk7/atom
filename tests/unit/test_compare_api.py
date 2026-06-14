from __future__ import annotations
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base, get_db
import etl_framework.repository.models  # noqa: F401
from api.main import app
from api.routes import runs as runs_module


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    def override_get_db():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(runs_module, "_execute_run", lambda *a, **kw: None)
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_bo_compare_rejects_bad_source(client):
    resp = client.post("/api/compare/bo-report", json={
        "source_a": {"source_type": "live"},   # missing config_id → 422
        "source_b": {"source_type": "path", "file_path": "/tmp/x.csv"},
    })
    assert resp.status_code == 422


def test_bo_compare_upload_returns_202(client, monkeypatch, tmp_path):
    import base64, io, pandas as pd
    buf = io.BytesIO()
    pd.DataFrame({"id": [1], "v": [1]}).to_csv(buf, index=False)
    b64a = base64.b64encode(buf.getvalue()).decode()
    buf2 = io.BytesIO()
    pd.DataFrame({"id": [1], "v": [1]}).to_csv(buf2, index=False)
    b64b = base64.b64encode(buf2.getvalue()).decode()

    import api.routes.compare as cmp_module
    monkeypatch.setattr(cmp_module, "_run_bo_bg", lambda *a, **kw: None)

    resp = client.post("/api/compare/bo-report", json={
        "source_a": {"source_type": "upload", "file_content_b64": b64a, "file_name": "a.csv"},
        "source_b": {"source_type": "upload", "file_content_b64": b64b, "file_name": "b.csv"},
        "key_columns": ["id"],
        "label_a": "Env A", "label_b": "Env B",
    })
    assert resp.status_code == 202
    data = resp.json()
    assert "run_id" in data
    assert data["run_type"] == "bo_comparison"


def test_dual_env_launch_returns_pair(client, monkeypatch):
    import api.routes.compare as cmp_module
    monkeypatch.setattr(cmp_module, "_launch_dual_env_bg", lambda *a, **kw: None)

    c1 = client.post("/api/configs", json={"name": "cfg-a", "env_name": "a", "config_data": {}})
    c2 = client.post("/api/configs", json={"name": "cfg-b", "env_name": "b", "config_data": {}})
    cid_a, cid_b = c1.json()["id"], c2.json()["id"]

    resp = client.post("/api/compare/dual-env", json={
        "config_id_a": cid_a, "config_id_b": cid_b,
        "source_env_a": "src-a", "target_env_a": "tgt-a",
        "source_env_b": "src-b", "target_env_b": "tgt-b",
        "job_names": [],
    })
    assert resp.status_code == 202
    data = resp.json()
    assert "pair_id" in data
    assert "run_id_a" in data
    assert "run_id_b" in data


def test_get_pair_runs(client, monkeypatch):
    import api.routes.compare as cmp_module
    monkeypatch.setattr(cmp_module, "_launch_dual_env_bg", lambda *a, **kw: None)
    c1 = client.post("/api/configs", json={"name": "cfg-c", "env_name": "c", "config_data": {}})
    c2 = client.post("/api/configs", json={"name": "cfg-d", "env_name": "d", "config_data": {}})
    cid_a, cid_b = c1.json()["id"], c2.json()["id"]
    launch = client.post("/api/compare/dual-env", json={
        "config_id_a": cid_a, "config_id_b": cid_b,
        "source_env_a": "s", "target_env_a": "t",
        "source_env_b": "s2", "target_env_b": "t2",
        "job_names": [],
    })
    pair_id = launch.json()["pair_id"]
    resp = client.get(f"/api/compare/pairs/{pair_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["pair_id"] == pair_id
    assert "run_a" in data and "run_b" in data


def test_list_pairs(client, monkeypatch):
    import api.routes.compare as cmp_module
    monkeypatch.setattr(cmp_module, "_launch_dual_env_bg", lambda *a, **kw: None)
    c1 = client.post("/api/configs", json={"name": "cfg-e", "env_name": "e", "config_data": {}})
    c2 = client.post("/api/configs", json={"name": "cfg-f", "env_name": "f", "config_data": {}})
    cid_a, cid_b = c1.json()["id"], c2.json()["id"]
    client.post("/api/compare/dual-env", json={
        "config_id_a": cid_a, "config_id_b": cid_b,
        "source_env_a": "s", "target_env_a": "t",
        "source_env_b": "s2", "target_env_b": "t2",
        "job_names": [],
    })
    resp = client.get("/api/compare/pairs")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1
