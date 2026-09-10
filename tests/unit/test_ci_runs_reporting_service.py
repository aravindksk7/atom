import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from fastapi.testclient import TestClient
from etl_framework.repository.database import Base, get_db
from etl_framework.repository import database as _db_module
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import TokenRepository, RunRepository
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


def test_ci_runs_filters_are_frozen():
    from dataclasses import FrozenInstanceError
    from api.services.ci_runs_reporting import CiRunsFilters

    filters = CiRunsFilters()
    with pytest.raises(FrozenInstanceError):
        filters.days = 7


def test_ci_runs_reporting_service_summary_empty():
    from api.services.ci_runs_reporting import CiRunsReportingService, CiRunsFilters

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine)
    
    service = CiRunsReportingService(db)
    result = service.summary(CiRunsFilters(days=30))
    
    assert result["total"] == 0
    assert result["passed"] == 0
    assert result["failed"] == 0
    assert result["error"] == 0
    assert result["cancelled"] == 0
    assert result["pass_rate"] == 0


def test_ci_runs_reporting_service_total_includes_non_terminal_status():
    from api.services.ci_runs_reporting import CiRunsReportingService, CiRunsFilters

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine)

    repo = RunRepository(db)
    repo.create_run("run-pending", "dev", "prod", ci_context={"commit_sha": "a"})
    repo.update_run_status("run-pending", "PENDING", started_at=datetime.now(timezone.utc))

    result = CiRunsReportingService(db).summary(CiRunsFilters())

    assert result["total"] == 1
    assert result["passed"] == 0
    assert result["pass_rate"] == 0


def test_ci_runs_reporting_service_excludes_json_null_but_counts_empty_ci_context():
    from api.services.ci_runs_reporting import CiRunsReportingService, CiRunsFilters

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine)
    repo = RunRepository(db)
    now = datetime.now(timezone.utc)

    repo.create_run("run-manual", "dev", "prod", ci_context=None)
    repo.update_run_status("run-manual", "PASSED", started_at=now)
    repo.create_run("run-ci-empty", "dev", "prod", ci_context={})
    repo.update_run_status("run-ci-empty", "PASSED", started_at=now)

    result = CiRunsReportingService(db).summary(CiRunsFilters(days=30))

    assert result["total"] == 1
    assert result["passed"] == 1


def test_ci_runs_reporting_service_summary_counts_ci_runs():
    from api.services.ci_runs_reporting import CiRunsReportingService, CiRunsFilters
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine)
    
    repo = RunRepository(db)
    now = datetime.now(timezone.utc)
    
    repo.create_run("run-1", "dev", "prod", ci_context={"commit_sha": "a"})
    repo.update_run_status("run-1", "PASSED", started_at=now)
    
    repo.create_run("run-2", "dev", "prod", ci_context={"commit_sha": "b"})
    repo.update_run_status("run-2", "FAILED", started_at=now)
    
    repo.create_run("run-manual", "dev", "prod")
    repo.update_run_status("run-manual", "PASSED", started_at=now)
    
    service = CiRunsReportingService(db)
    result = service.summary(CiRunsFilters(days=30))
    
    assert result["total"] == 2
    assert result["passed"] == 1
    assert result["failed"] == 1
    assert result["error"] == 0
    assert result["cancelled"] == 0
    assert result["pass_rate"] == 0.5


def test_ci_runs_reporting_service_respects_days_filter():
    from api.services.ci_runs_reporting import CiRunsReportingService, CiRunsFilters
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine)
    
    repo = RunRepository(db)
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=31)
    
    repo.create_run("run-new", "dev", "prod", ci_context={"commit_sha": "a"})
    repo.update_run_status("run-new", "PASSED", started_at=now)
    
    repo.create_run("run-old", "dev", "prod", ci_context={"commit_sha": "b"})
    repo.update_run_status("run-old", "PASSED", started_at=old)
    
    service = CiRunsReportingService(db)
    result = service.summary(CiRunsFilters(days=30))
    
    assert result["total"] == 1
    assert result["passed"] == 1


def test_ci_runs_reporting_service_respects_status_filter():
    from api.services.ci_runs_reporting import CiRunsReportingService, CiRunsFilters
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine)
    
    repo = RunRepository(db)
    now = datetime.now(timezone.utc)
    
    repo.create_run("run-1", "dev", "prod", ci_context={"commit_sha": "a"})
    repo.update_run_status("run-1", "PASSED", started_at=now)
    
    repo.create_run("run-2", "dev", "prod", ci_context={"commit_sha": "b"})
    repo.update_run_status("run-2", "FAILED", started_at=now)
    
    service = CiRunsReportingService(db)
    result = service.summary(CiRunsFilters(days=30, status="PASSED"))
    
    assert result["total"] == 1
    assert result["passed"] == 1
    assert result["failed"] == 0


def test_ci_runs_reporting_service_respects_target_type_filter():
    from api.services.ci_runs_reporting import CiRunsReportingService, CiRunsFilters
    from etl_framework.repository.repository import JobSelectionRepository
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine)
    
    now = datetime.now(timezone.utc)
    
    sel_repo = JobSelectionRepository(db)
    sel = sel_repo.create("my-sel", "", [], [], {})
    
    run_repo = RunRepository(db)
    run_repo.create_run("run-sel", "dev", "prod", selection_id=sel.id, ci_context={"commit_sha": "a"})
    run_repo.update_run_status("run-sel", "PASSED", started_at=now)
    
    run_repo.create_run("run-seq", "dev", "prod", config_snapshot={"sequence": {"name": "nightly"}}, ci_context={"commit_sha": "b"})
    run_repo.update_run_status("run-seq", "PASSED", started_at=now)
    
    service = CiRunsReportingService(db)
    result_sel = service.summary(CiRunsFilters(days=30, target_type="selection"))
    result_seq = service.summary(CiRunsFilters(days=30, target_type="sequence"))
    
    assert result_sel["total"] == 1
    assert result_seq["total"] == 1


def test_ci_runs_reporting_service_unresolved_selection_is_not_a_target():
    from api.services.ci_runs_reporting import CiRunsReportingService, CiRunsFilters

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine)
    repo = RunRepository(db)
    now = datetime.now(timezone.utc)
    repo.create_run(
        "run-missing-selection", "dev", "prod", selection_id=99999,
        ci_context={"commit_sha": "a"},
    )
    repo.update_run_status("run-missing-selection", "PASSED", started_at=now)

    service = CiRunsReportingService(db)

    unfiltered = service.summary(CiRunsFilters(days=30))
    selections = service.summary(CiRunsFilters(days=30, target_type="selection"))
    assert unfiltered["total"] == 1
    assert unfiltered["by_target_type"] == {"selection": 0, "sequence": 0}
    assert selections["total"] == 0


def test_ci_runs_reporting_service_uses_database_aggregate_queries():
    from api.services.ci_runs_reporting import CiRunsReportingService, CiRunsFilters
    from etl_framework.repository.repository import JobSelectionRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine)
    selections = JobSelectionRepository(db)
    first = selections.create("first", "", [], [], {})
    second = selections.create("second", "", [], [], {})
    repo = RunRepository(db)
    now = datetime.now(timezone.utc)
    repo.create_run("run-first", "dev", "prod", selection_id=first.id, ci_context={})
    repo.update_run_status("run-first", "PASSED", started_at=now)
    repo.create_run("run-second", "dev", "prod", selection_id=second.id, ci_context={})
    repo.update_run_status("run-second", "FAILED", started_at=now)

    from sqlalchemy import event
    queries = []
    event.listen(db.bind, "before_cursor_execute", lambda *args: queries.append(args[2]))
    result = CiRunsReportingService(db).summary(CiRunsFilters(days=30))

    selects = [query.lower() for query in queries if query.lstrip().lower().startswith("select")]
    assert result["by_target_type"]["selection"] == 2
    assert 1 <= len(selects) <= 3
    assert all("count(" in query for query in selects)
    assert all("select test_runs.config_snapshot" not in query for query in selects)
    assert all(" in (" not in query for query in selects)
    assert all("test_runs.source_env" not in query for query in selects)
    assert all("test_runs.completed_at" not in query for query in selects)


def test_ci_runs_reporting_service_empty_sequence_name_falls_back_to_existing_selection():
    from api.services.ci_runs_reporting import CiRunsReportingService, CiRunsFilters
    from etl_framework.repository.repository import JobSelectionRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine)
    selection = JobSelectionRepository(db).create("fallback", "", [], [], {})
    repo = RunRepository(db)
    repo.create_run(
        "run-empty-sequence", "dev", "prod", selection_id=selection.id,
        config_snapshot={"sequence": {"name": "  "}}, ci_context={},
    )
    repo.update_run_status("run-empty-sequence", "PASSED", started_at=datetime.now(timezone.utc))

    result = CiRunsReportingService(db).summary(CiRunsFilters(target_type="selection"))

    assert result["total"] == 1
    assert result["by_target_type"] == {"selection": 1, "sequence": 0}


def test_ci_runs_reporting_service_ignores_malformed_target_snapshots():
    from api.services.ci_runs_reporting import CiRunsReportingService, CiRunsFilters

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine)
    repo = RunRepository(db)
    now = datetime.now(timezone.utc)
    repo.create_run("run-list", "dev", "prod", config_snapshot=[], ci_context={})
    repo.update_run_status("run-list", "PASSED", started_at=now)
    repo.create_run("run-missing-sequence", "dev", "prod", config_snapshot={}, ci_context={})
    repo.update_run_status("run-missing-sequence", "PASSED", started_at=now)
    repo.create_run("run-missing-name", "dev", "prod", config_snapshot={"sequence": {}}, ci_context={})
    repo.update_run_status("run-missing-name", "PASSED", started_at=now)
    repo.create_run("run-blank-name", "dev", "prod", config_snapshot={"sequence": {"name": "  "}}, ci_context={})
    repo.update_run_status("run-blank-name", "PASSED", started_at=now)
    repo.create_run(
        "run-numeric-name", "dev", "prod",
        config_snapshot={"sequence": {"name": 42}}, ci_context={},
    )
    repo.update_run_status("run-numeric-name", "PASSED", started_at=now)

    result = CiRunsReportingService(db).summary(CiRunsFilters(days=30))

    assert result["total"] == 5
    assert result["by_target_type"] == {"selection": 0, "sequence": 0}


def test_api_ci_summary_endpoint_success(client):
    with _db_module.SessionLocal() as db:
        repo = RunRepository(db)
        now = datetime.now(timezone.utc)
        
        repo.create_run("run-1", "dev", "prod", ci_context={"commit_sha": "a"})
        repo.update_run_status("run-1", "PASSED", started_at=now)
        repo.create_run("run-2", "dev", "prod", ci_context={"commit_sha": "b"})
        repo.update_run_status("run-2", "FAILED", started_at=now)
    
    resp = client.get("/api/runs/ci-summary?days=30")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert data["passed"] == 1
    assert data["failed"] == 1
    assert data["pass_rate"] == 0.5


def test_api_ci_summary_endpoint_days_validation(client):
    resp = client.get("/api/runs/ci-summary?days=0")
    assert resp.status_code == 422

    resp = client.get("/api/runs/ci-summary?days=366")
    assert resp.status_code == 422


def test_api_ci_summary_endpoint_target_type_validation(client):
    resp = client.get("/api/runs/ci-summary?target_type=job")
    assert resp.status_code == 422


def test_api_ci_summary_endpoint_unknown_status_returns_empty_summary(client):
    resp = client.get("/api/runs/ci-summary?status=UNKNOWN")

    assert resp.status_code == 200
    assert resp.json() == {
        "total": 0,
        "passed": 0,
        "failed": 0,
        "error": 0,
        "cancelled": 0,
        "pass_rate": 0,
        "by_target_type": {"selection": 0, "sequence": 0},
    }


def test_api_ci_summary_endpoint_includes_by_target_type(client):
    from etl_framework.repository.repository import JobSelectionRepository
    
    with _db_module.SessionLocal() as db:
        now = datetime.now(timezone.utc)
        
        sel_repo = JobSelectionRepository(db)
        sel = sel_repo.create("sel-1", "", [], [], {})
        
        run_repo = RunRepository(db)
        run_repo.create_run("run-sel", "dev", "prod", selection_id=sel.id, ci_context={"commit_sha": "a"})
        run_repo.update_run_status("run-sel", "PASSED", started_at=now)
        
        run_repo.create_run("run-seq", "dev", "prod", config_snapshot={"sequence": {"name": "nightly"}}, ci_context={"commit_sha": "b"})
        run_repo.update_run_status("run-seq", "PASSED", started_at=now)
    
    resp = client.get("/api/runs/ci-summary?days=30")
    assert resp.status_code == 200
    data = resp.json()
    assert "by_target_type" in data
    assert data["by_target_type"]["selection"] == 1
    assert data["by_target_type"]["sequence"] == 1


def test_api_ci_summary_filters_by_status(client):
    with _db_module.SessionLocal() as db:
        repo = RunRepository(db)
        now = datetime.now(timezone.utc)
        
        repo.create_run("run-1", "dev", "prod", ci_context={"commit_sha": "a"})
        repo.update_run_status("run-1", "PASSED", started_at=now)
        repo.create_run("run-2", "dev", "prod", ci_context={"commit_sha": "b"})
        repo.update_run_status("run-2", "FAILED", started_at=now)
    
    resp = client.get("/api/runs/ci-summary?days=30&status=PASSED")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["passed"] == 1
    assert data["failed"] == 0


def test_api_ci_summary_filters_by_target_type(client):
    from etl_framework.repository.repository import JobSelectionRepository
    
    with _db_module.SessionLocal() as db:
        now = datetime.now(timezone.utc)
        
        sel_repo = JobSelectionRepository(db)
        sel = sel_repo.create("sel-1", "", [], [], {})
        
        run_repo = RunRepository(db)
        run_repo.create_run("run-sel", "dev", "prod", selection_id=sel.id, ci_context={"commit_sha": "a"})
        run_repo.update_run_status("run-sel", "PASSED", started_at=now)
        
        run_repo.create_run("run-seq", "dev", "prod", config_snapshot={"sequence": {"name": "nightly"}}, ci_context={"commit_sha": "b"})
        run_repo.update_run_status("run-seq", "PASSED", started_at=now)
    
    resp = client.get("/api/runs/ci-summary?days=30&target_type=selection")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["by_target_type"]["selection"] == 1
    assert data["by_target_type"]["sequence"] == 0
