"""Integration tests for Data Contracts Testing API."""
from __future__ import annotations
import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from api.main import app
from api.dependencies import get_session
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from etl_framework.repository.database import Base, get_db
import etl_framework.repository.models
import etl_framework.repository.contract_models
from etl_framework.repository.contract_models import Contract
from etl_framework.repository.models import SavedJob, TestRun, TestResult, SchemaSnapshot
from etl_framework.repository.repository import TokenRepository
import etl_framework.repository.database as _db_module


@pytest.fixture
def client(monkeypatch):
    previous_overrides = dict(app.dependency_overrides)
    app.dependency_overrides.clear()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    def override_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_session
    app.dependency_overrides[get_session] = override_session

    with Session(engine) as db:
        raw_token, _ = TokenRepository(db).create("test-token")

    with TestClient(app, headers={"Authorization": f"Bearer {raw_token}"}, raise_server_exceptions=True) as c:
        yield c, engine

    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous_overrides)


def test_post_contract_test_endpoint(client):
    """Test POST /api/contracts/{name}/test endpoint."""
    c, engine = client
    
    with Session(engine) as db:
        # Setup contract with complete test data
        contract = Contract(
            name="api_test_contract",
            source_job="api_test_job",
            owner="test@example.com",
            sla_hours=4.0,
            consumers='["finance"]',
            version="1.0"
        )
        db.add(contract)
        
        job = SavedJob(
            name="api_test_job",
            query="SELECT * FROM test",
            params={"null_check_columns": ["id"]}
        )
        db.add(job)
        
        recent_time = datetime.now(timezone.utc) - timedelta(hours=1)
        run = TestRun(
            run_id="api_run_1",
            status="PASSED",
            started_at=recent_time,
            completed_at=recent_time
        )
        db.add(run)
        
        result = TestResult(
            run_id="api_run_1",
            query_name="api_test_job",
            status="PASSED",
            source_row_count=100,
            target_row_count=100,
            value_mismatch_count=0,
            executed_at=recent_time
        )
        db.add(result)
        
        snapshot = SchemaSnapshot(
            job_name="api_test_job",
            environment="source",
            columns='[{"name": "id", "type": "INTEGER"}]',
            captured_at=recent_time
        )
        db.add(snapshot)
        db.commit()
    
    # Execute test
    response = c.post("/api/contracts/api_test_contract/test")
    
    assert response.status_code == 200
    data = response.json()
    assert data["contract"] == "api_test_contract"
    assert data["source_job"] == "api_test_job"
    assert "overall_status" in data
    assert "summary" in data
    assert "checks" in data
    assert len(data["checks"]) > 0


def test_post_contract_test_not_found(client):
    """Test POST /api/contracts/{name}/test with nonexistent contract."""
    c, _ = client
    response = c.post("/api/contracts/nonexistent_contract/test")
    
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()

