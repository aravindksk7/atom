from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.routes import runs as runs_module
from etl_framework.repository.database import Base, get_db
from etl_framework.repository import database as _db_module
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import JobSelectionRepository, RunRepository, TokenRepository
from api.main import app


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    def override_get_db():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(runs_module, "_execute_run", lambda *args, **kwargs: None)
    app.dependency_overrides[get_db] = override_get_db

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test")

    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
        yield c
    app.dependency_overrides.clear()


def test_run_status_out_has_ci_context_fields(client):
    resp = client.get("/api/runs")
    assert resp.status_code == 200
    assert resp.json() == []

    with _db_module.SessionLocal() as db:
        repo = RunRepository(db)
        repo.create_run("run-manual", "dev", "prod", config_snapshot={})
        repo.create_run(
            "run-ci-selection",
            "dev",
            "prod",
            selection_id=1,
            config_snapshot={},
            ci_context={"commit_sha": "abc123", "pipeline_url": "https://example.com/p", "ref": "main"},
        )

    resp = client.get("/api/runs")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2

    ci_run = next((r for r in data if r["run_id"] == "run-ci-selection"), None)
    assert ci_run is not None
    assert ci_run["ci_context"] == {"commit_sha": "abc123", "pipeline_url": "https://example.com/p", "ref": "main"}


def test_run_status_out_sequence_target():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine)

    repo = RunRepository(db)
    repo.create_run(
        "run-seq",
        "dev",
        "prod",
        config_snapshot={
            "sequence": {
                "id": 1,
                "name": "nightly",
                "version": 1,
            }
        },
        ci_context={"commit_sha": "xyz"},
    )

    from api.routes.runs import _run_status_out
    run = repo.get_run("run-seq")
    out = _run_status_out(run, selection_names_map={})

    assert out.target_type == "sequence"
    assert out.target_name == "nightly"


def test_run_status_out_selection_target_resolved(client):
    with _db_module.SessionLocal() as db:
        sel_repo = JobSelectionRepository(db)
        sel = sel_repo.create("my-selection", "", [], [], {})

        run_repo = RunRepository(db)
        run_repo.create_run(
            "run-sel",
            "dev",
            "prod",
            selection_id=sel.id,
            config_snapshot={},
            ci_context={"commit_sha": "abc"},
        )

    resp = client.get("/api/runs")
    assert resp.status_code == 200
    data = resp.json()
    sel_run = next((r for r in data if r["run_id"] == "run-sel"), None)
    assert sel_run is not None
    assert sel_run["target_type"] == "selection"
    assert sel_run["target_name"] == "my-selection"


def test_run_status_out_selection_target_unresolved():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine)

    repo = RunRepository(db)
    repo.create_run(
        "run-deleted-sel",
        "dev",
        "prod",
        selection_id=999999,
        config_snapshot={},
        ci_context={"commit_sha": "abc"},
    )

    from api.routes.runs import _run_status_out
    run = repo.get_run("run-deleted-sel")
    out = _run_status_out(run, selection_names_map={})

    assert out.target_type is None
    assert out.target_name is None


def test_list_runs_batches_selection_names(client):
    engine = _db_module.SessionLocal.kw["bind"]
    with _db_module.SessionLocal() as db:
        sel_repo = JobSelectionRepository(db)
        sel1 = sel_repo.create("selection-1", "", [], [], {})
        sel2 = sel_repo.create("selection-2", "", [], [], {})

        run_repo = RunRepository(db)
        run_repo.create_run("r1", "dev", "prod", selection_id=sel1.id, config_snapshot={}, ci_context={"commit_sha": "a"})
        run_repo.create_run("r2", "dev", "prod", selection_id=sel2.id, config_snapshot={}, ci_context={"commit_sha": "b"})
        run_repo.create_run("r3", "dev", "prod", selection_id=sel1.id, config_snapshot={}, ci_context={"commit_sha": "c"})

    selection_queries = []

    def capture_selection_query(conn, cursor, statement, parameters, context, executemany):
        if "FROM job_selections" in statement:
            selection_queries.append(statement)

    event.listen(engine, "before_cursor_execute", capture_selection_query)
    resp = client.get("/api/runs")
    event.remove(engine, "before_cursor_execute", capture_selection_query)

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
    assert len(selection_queries) == 1

    for run_data in data:
        if run_data["run_id"] in ("r1", "r3"):
            assert run_data["target_name"] == "selection-1"
        elif run_data["run_id"] == "r2":
            assert run_data["target_name"] == "selection-2"


def test_list_runs_filters_before_pagination(client):
    with _db_module.SessionLocal() as db:
        selection = JobSelectionRepository(db).create("matching", "", [], [], {})
        repo = RunRepository(db)
        matching = repo.create_run("matching", "dev", "prod", selection_id=selection.id, ci_context={"commit_sha": "a"})
        matching.started_at = datetime.now(timezone.utc)
        db.commit()
        for index in range(3):
            repo.create_run(
                f"sequence-{index}",
                "dev",
                "prod",
                config_snapshot={"sequence": {"name": f"sequence-{index}"}},
                ci_context={"commit_sha": "b"},
            )

    resp = client.get("/api/runs?ci_only=true&target_type=selection&days=30&limit=1")

    assert resp.status_code == 200
    assert [run["run_id"] for run in resp.json()] == ["matching"]


@pytest.mark.parametrize(
    "query",
    ["days=0", "days=366", "days=abc", "target_type=job", "target_type="],
)
def test_list_runs_rejects_invalid_filter_params(client, query):
    assert client.get(f"/api/runs?{query}").status_code == 422


def test_list_runs_unknown_status_returns_no_matches(client):
    with _db_module.SessionLocal() as db:
        RunRepository(db).create_run("run-passed", "dev", "prod", ci_context={})
        RunRepository(db).update_run_status("run-passed", "PASSED")

    resp = client.get("/api/runs?status=UNKNOWN")

    assert resp.status_code == 200
    assert resp.json() == []


def test_list_runs_ci_only_filter(client):
    with _db_module.SessionLocal() as db:
        repo = RunRepository(db)
        repo.create_run("run-manual", "dev", "prod", config_snapshot={})
        repo.create_run("run-ci", "dev", "prod", config_snapshot={}, ci_context={"commit_sha": "abc"})

    resp = client.get("/api/runs")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2

    resp = client.get("/api/runs?ci_only=true")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["run_id"] == "run-ci"
