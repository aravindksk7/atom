"""Tests for P2 features: badge SVG, baseline pinning, mismatch distribution, dry-run."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from api.main import app
from etl_framework.repository.database import Base, get_db
from etl_framework.repository import database as _db_module
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import TokenRepository, RunRepository
from etl_framework.repository.models import TestRun, TestResult, MismatchDetail


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


def _seed_run(engine, status="PASSED", run_id=None):
    import uuid
    rid = run_id or str(uuid.uuid4())
    with Session(engine) as db:
        run = TestRun(
            run_id=rid, status=status,
            source_env="dev", target_env="prod",
            completed_at=datetime.now(timezone.utc),
            total_tests=1, passed=1, failed=0, slow=0, error=0,
        )
        db.add(run)
        db.flush()
        result = TestResult(
            run_id=rid, query_name="orders",
            status=status, duration_seconds=1.0,
            source_row_count=10, target_row_count=10,
            value_mismatch_count=2,
            missing_in_target_count=0, missing_in_source_count=0,
            executed_at=datetime.now(timezone.utc),
        )
        db.add(result)
        db.flush()
        for i in range(3):
            db.add(MismatchDetail(
                test_result_id=result.id,
                column_name="status",
                source_value="ACTIVE" if i < 2 else "PENDING",
                target_value="INACTIVE",
                mismatch_type="value",
            ))
        db.commit()
        return rid, result.id


# ---------------------------------------------------------------------------
# Badge SVG
# ---------------------------------------------------------------------------

def test_run_badge_returns_svg(monkeypatch):
    client, engine = _make_client(monkeypatch)
    rid, _ = _seed_run(engine, "PASSED")
    resp = client.get(f"/api/runs/{rid}/badge")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/svg+xml")
    assert "<svg" in resp.text
    assert "PASSED" in resp.text


def test_run_badge_unknown_run_shows_unknown(monkeypatch):
    client, _ = _make_client(monkeypatch)
    resp = client.get("/api/runs/nonexistent-run-id/badge")
    assert resp.status_code == 200
    assert "UNKNOWN" in resp.text


def test_badge_failed_has_different_color(monkeypatch):
    client, engine = _make_client(monkeypatch)
    rid, _ = _seed_run(engine, "FAILED")
    resp = client.get(f"/api/runs/{rid}/badge")
    assert "FAILED" in resp.text
    assert "#fb7185" in resp.text  # FAILED color


def test_latest_badge_with_job_name(monkeypatch):
    client, engine = _make_client(monkeypatch)
    _seed_run(engine, "PASSED")
    resp = client.get("/api/runs/latest/badge?job_name=orders")
    assert resp.status_code == 200
    assert "<svg" in resp.text


# ---------------------------------------------------------------------------
# Baseline pinning
# ---------------------------------------------------------------------------

def test_set_baseline_returns_204(monkeypatch):
    client, engine = _make_client(monkeypatch)
    rid, _ = _seed_run(engine)
    resp = client.post(f"/api/runs/{rid}/set-baseline")
    assert resp.status_code == 204


def test_set_baseline_marks_run(monkeypatch):
    client, engine = _make_client(monkeypatch)
    rid, _ = _seed_run(engine)
    client.post(f"/api/runs/{rid}/set-baseline")
    with Session(engine) as db:
        run = db.query(TestRun).filter_by(run_id=rid).first()
        assert run.is_baseline is True


def test_set_baseline_only_one_per_env_pair(monkeypatch):
    import uuid
    client, engine = _make_client(monkeypatch)
    rid1 = str(uuid.uuid4())
    rid2 = str(uuid.uuid4())
    _seed_run(engine, run_id=rid1)
    _seed_run(engine, run_id=rid2)
    client.post(f"/api/runs/{rid1}/set-baseline")
    client.post(f"/api/runs/{rid2}/set-baseline")
    with Session(engine) as db:
        baselines = db.query(TestRun).filter_by(is_baseline=True).all()
        assert len(baselines) == 1
        assert baselines[0].run_id == rid2


def test_set_baseline_not_found_returns_404(monkeypatch):
    client, _ = _make_client(monkeypatch)
    resp = client.post("/api/runs/nonexistent/set-baseline")
    assert resp.status_code == 404


def test_vs_baseline_no_baseline_returns_404(monkeypatch):
    client, engine = _make_client(monkeypatch)
    rid, _ = _seed_run(engine)
    resp = client.get(f"/api/runs/{rid}/vs-baseline")
    assert resp.status_code == 404


def test_vs_baseline_returns_compare(monkeypatch):
    import uuid
    client, engine = _make_client(monkeypatch)
    rid1 = str(uuid.uuid4())
    rid2 = str(uuid.uuid4())
    _seed_run(engine, run_id=rid1)
    _seed_run(engine, run_id=rid2)
    client.post(f"/api/runs/{rid1}/set-baseline")
    resp = client.get(f"/api/runs/{rid2}/vs-baseline")
    assert resp.status_code == 200
    data = resp.json()
    assert "run_a" in data and "run_b" in data


# ---------------------------------------------------------------------------
# Mismatch distribution
# ---------------------------------------------------------------------------

def test_mismatch_distribution_returns_aggregated(monkeypatch):
    client, engine = _make_client(monkeypatch)
    rid, result_id = _seed_run(engine)
    resp = client.get(f"/api/runs/{rid}/results/{result_id}/mismatch-distribution")
    assert resp.status_code == 200
    dist = resp.json()["distribution"]
    assert len(dist) > 0
    top = dist[0]
    assert top["count"] >= 1
    assert "column" in top


def test_mismatch_distribution_most_frequent_first(monkeypatch):
    client, engine = _make_client(monkeypatch)
    rid, result_id = _seed_run(engine)
    dist = client.get(f"/api/runs/{rid}/results/{result_id}/mismatch-distribution").json()["distribution"]
    counts = [d["count"] for d in dist]
    assert counts == sorted(counts, reverse=True)


def test_mismatch_distribution_nonexistent_run_returns_404(monkeypatch):
    client, _ = _make_client(monkeypatch)
    resp = client.get("/api/runs/nope/results/1/mismatch-distribution")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Baseline repository unit tests
# ---------------------------------------------------------------------------

def test_set_baseline_repo():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        import uuid
        rid = str(uuid.uuid4())
        run = TestRun(run_id=rid, status="PASSED", source_env="dev", target_env="prod",
                      completed_at=datetime.now(timezone.utc), total_tests=0,
                      passed=0, failed=0, slow=0, error=0)
        db.add(run)
        db.commit()
        repo = RunRepository(db)
        result = repo.set_baseline(rid)
        assert result is not None
        assert result.is_baseline is True


def test_get_baseline_repo():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        import uuid
        rid = str(uuid.uuid4())
        run = TestRun(run_id=rid, status="PASSED", source_env="dev", target_env="prod",
                      completed_at=datetime.now(timezone.utc), total_tests=0,
                      passed=0, failed=0, slow=0, error=0, is_baseline=True)
        db.add(run)
        db.commit()
        repo = RunRepository(db)
        baseline = repo.get_baseline("dev", "prod")
        assert baseline is not None
        assert baseline.run_id == rid


def test_get_baseline_none_when_not_set():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        assert RunRepository(db).get_baseline("dev", "prod") is None
