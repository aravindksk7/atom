"""Tests for PR4: job-level lineage DAG — edge creation and endpoints."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from api.main import app
from etl_framework.repository.database import Base, get_db
from etl_framework.repository import database as _db_module
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import JobRepository, LineageRepository, TokenRepository
from etl_framework.repository.models import JobLineageEdge


def _make_client(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    def override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_db] = override
    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test")
    return TestClient(app, headers={"Authorization": f"Bearer {raw}"}), engine


def _make_job(name: str, depends_on: list[str] | None = None) -> dict:
    return {
        "name": name,
        "description": "",
        "tags": [],
        "job_type": "reconciliation",
        "query": "SELECT 1",
        "key_columns": ["id"],
        "exclude_columns": [],
        "source_env": "dev",
        "target_env": "prod",
        "params": {},
        "enabled": True,
        "rules": [],
        "depends_on": depends_on or [],
    }


# ---------------------------------------------------------------------------
# Repository — edge creation
# ---------------------------------------------------------------------------

def _to_data(name: str, depends_on: list[str] | None = None) -> dict:
    """Build a job dict in the shape JobRepository.upsert expects."""
    d = _make_job(name, depends_on)
    rules = d.pop("rules", [])
    dep = d.pop("depends_on", [])
    params = dict(d.get("params") or {})
    if dep:
        params["depends_on"] = dep
    d["params"] = params
    return d


def test_upsert_job_creates_lineage_edges():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        repo = JobRepository(db)
        repo.upsert(_to_data("upstream_a"))
        repo.upsert(_to_data("upstream_b"))
        repo.upsert(_to_data("downstream", depends_on=["upstream_a", "upstream_b"]))

        edges = db.query(JobLineageEdge).filter_by(downstream_job="downstream").all()
        upstreams = {e.upstream_job for e in edges}
        assert upstreams == {"upstream_a", "upstream_b"}


def test_upsert_job_replaces_old_edges_on_update():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        repo = JobRepository(db)
        repo.upsert(_to_data("a"))
        repo.upsert(_to_data("b"))
        repo.upsert(_to_data("c"))
        repo.upsert(_to_data("child", depends_on=["a", "b"]))

        # Update child to only depend on c
        repo.upsert(_to_data("child", depends_on=["c"]))

        edges = db.query(JobLineageEdge).filter_by(downstream_job="child").all()
        assert len(edges) == 1
        assert edges[0].upstream_job == "c"


def test_job_with_no_depends_on_has_no_edges():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        repo = JobRepository(db)
        repo.upsert(_to_data("standalone"))

        edges = db.query(JobLineageEdge).filter_by(downstream_job="standalone").all()
        assert edges == []


# ---------------------------------------------------------------------------
# LineageRepository unit tests
# ---------------------------------------------------------------------------

def test_lineage_repo_job_graph_nodes_and_edges():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        job_repo = JobRepository(db)
        job_repo.upsert(_to_data("root"))
        job_repo.upsert(_to_data("leaf", depends_on=["root"]))

        graph = LineageRepository(db).job_graph()
        node_names = {n["name"] for n in graph["nodes"]}
        assert "root" in node_names
        assert "leaf" in node_names
        assert any(e["from"] == "root" and e["to"] == "leaf" for e in graph["edges"])


def test_lineage_repo_get_upstream():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        job_repo = JobRepository(db)
        job_repo.upsert(_to_data("a"))
        job_repo.upsert(_to_data("b"))
        job_repo.upsert(_to_data("c", depends_on=["a", "b"]))

        upstream = LineageRepository(db).get_upstream("c")
        assert set(upstream) == {"a", "b"}


def test_lineage_repo_get_downstream():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        job_repo = JobRepository(db)
        job_repo.upsert(_to_data("source"))
        job_repo.upsert(_to_data("child1", depends_on=["source"]))
        job_repo.upsert(_to_data("child2", depends_on=["source"]))

        downstream = LineageRepository(db).get_downstream("source")
        assert set(downstream) == {"child1", "child2"}


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

def test_lineage_jobs_endpoint_returns_graph(monkeypatch):
    client, engine = _make_client(monkeypatch)
    # Use import (upsert) to seed jobs and trigger edge creation
    client.post("/api/jobs/import", json=[_make_job("root"), _make_job("leaf", depends_on=["root"])])

    resp = client.get("/api/lineage/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert "nodes" in data and "edges" in data
    node_names = {n["name"] for n in data["nodes"]}
    assert "root" in node_names and "leaf" in node_names
    assert any(e["from"] == "root" and e["to"] == "leaf" for e in data["edges"])


def test_lineage_upstream_endpoint(monkeypatch):
    client, _ = _make_client(monkeypatch)
    client.post("/api/jobs/import", json=[_make_job("alpha"), _make_job("beta", depends_on=["alpha"])])

    resp = client.get("/api/lineage/jobs/beta/upstream")
    assert resp.status_code == 200
    data = resp.json()
    assert data["job"] == "beta"
    assert "alpha" in data["upstream"]


def test_lineage_downstream_endpoint(monkeypatch):
    client, _ = _make_client(monkeypatch)
    client.post("/api/jobs/import", json=[_make_job("parent"), _make_job("child", depends_on=["parent"])])

    resp = client.get("/api/lineage/jobs/parent/downstream")
    assert resp.status_code == 200
    data = resp.json()
    assert data["job"] == "parent"
    assert "child" in data["downstream"]


def test_lineage_empty_graph(monkeypatch):
    client, _ = _make_client(monkeypatch)
    resp = client.get("/api/lineage/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["nodes"] == []
    assert data["edges"] == []


def test_lineage_upstream_empty_for_root(monkeypatch):
    client, _ = _make_client(monkeypatch)
    client.post("/api/jobs/import", json=[_make_job("root_job")])
    resp = client.get("/api/lineage/jobs/root_job/upstream")
    assert resp.status_code == 200
    assert resp.json()["upstream"] == []
