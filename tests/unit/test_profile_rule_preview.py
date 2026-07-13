from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app
from etl_framework.repository import database as database_module
from etl_framework.repository.database import Base, get_db
import etl_framework.repository.models  # noqa: F401 - registers ORM models
from etl_framework.repository.repository import ColumnProfileRepository, TokenRepository


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(database_module, "SessionLocal", sessionmaker(bind=engine))

    def override_get_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("profile-test")
        repo = ColumnProfileRepository(db)
        for idx, mean in enumerate([10, 11, 12, 10, 500], start=1):
            repo.save(
                job_name="orders_profile",
                run_id=f"run-{idx}",
                column_name="amount",
                null_rate=0.0,
                distinct_count=idx,
                min_val=str(mean),
                max_val=str(mean),
                mean_val=float(mean),
                std_val=1.0,
                p25=float(mean),
                p50=float(mean),
                p75=float(mean),
                p95=float(mean),
            )
        db.commit()

    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_preview_profile_rule_returns_violations(client):
    resp = client.post(
        "/api/jobs/orders_profile/profile/preview-rule",
        json={
            "column": "amount",
            "metric": "mean_val",
            "rule": {
                "type": "outlier_zscore",
                "column": "amount",
                "threshold": 1.5,
                "severity": "warn",
            },
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["sample_size"] == 5
    assert data["violations"][0]["rule_type"] == "outlier_zscore"
    assert data["violations"][0]["actual_value"] == 1


def test_preview_profile_rule_rejects_unknown_metric(client):
    resp = client.post(
        "/api/jobs/orders_profile/profile/preview-rule",
        json={
            "column": "amount",
            "metric": "not_a_metric",
            "rule": {"type": "outlier_zscore", "column": "amount"},
        },
    )

    assert resp.status_code == 400
    assert "metric must be one of" in resp.json()["detail"]


def test_preview_profile_rule_requires_history(client):
    resp = client.post(
        "/api/jobs/orders_profile/profile/preview-rule",
        json={
            "column": "missing",
            "metric": "mean_val",
            "rule": {"type": "outlier_zscore", "column": "missing"},
        },
    )

    assert resp.status_code == 404
