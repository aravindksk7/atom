from __future__ import annotations
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base, get_db
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import RunRepository
from api.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def repo(db):
    return RunRepository(db)


def _make_run(repo, run_id="run-001", run_type="reconciliation", pair_id=None):
    run = repo.create_run(
        run_id=run_id,
        source_env="dev",
        target_env="prod",
        config_snapshot=None,
        run_type=run_type,
        pair_id=pair_id,
    )
    return run


def test_create_run_with_run_type(repo):
    run = _make_run(repo, run_type="bo_comparison")
    assert run.run_type == "bo_comparison"
    assert run.pair_id is None


def test_create_run_with_pair_id(repo):
    run = _make_run(repo, run_type="dual_env", pair_id="pair-abc")
    assert run.pair_id == "pair-abc"


def test_accept_mismatch_sets_fields(db, repo):
    from etl_framework.repository.models import TestResult, MismatchDetail
    _make_run(repo)
    tr = TestResult(
        run_id="run-001", query_name="q1", status="FAILED",
        duration_seconds=1.0, source_row_count=10, target_row_count=10,
        value_mismatch_count=1, missing_in_target_count=0, missing_in_source_count=0,
    )
    db.add(tr); db.commit(); db.refresh(tr)
    md = MismatchDetail(
        test_result_id=tr.id, column_name="amount",
        source_value="100", target_value="99", mismatch_type="value_diff",
    )
    db.add(md); db.commit(); db.refresh(md)

    updated, status_changed = repo.accept_mismatch(md.id, "rounding diff", "alice")
    assert updated.accepted is True
    assert updated.accepted_note == "rounding diff"
    assert updated.accepted_by == "alice"
    assert status_changed is True  # last mismatch accepted → result flips to PASSED


def test_accept_mismatch_not_last_no_status_change(db, repo):
    from etl_framework.repository.models import TestResult, MismatchDetail
    _make_run(repo)
    tr = TestResult(
        run_id="run-001", query_name="q2", status="FAILED",
        duration_seconds=1.0, source_row_count=5, target_row_count=5,
        value_mismatch_count=2, missing_in_target_count=0, missing_in_source_count=0,
    )
    db.add(tr); db.commit(); db.refresh(tr)
    m1 = MismatchDetail(test_result_id=tr.id, column_name="c1",
                        source_value="a", target_value="b", mismatch_type="value_diff")
    m2 = MismatchDetail(test_result_id=tr.id, column_name="c2",
                        source_value="x", target_value="y", mismatch_type="value_diff")
    db.add_all([m1, m2]); db.commit(); db.refresh(m1); db.refresh(m2)

    _, status_changed = repo.accept_mismatch(m1.id, "ok", None)
    assert status_changed is False  # m2 still unaccepted


def test_get_pair_runs(repo):
    _make_run(repo, run_id="r-a", run_type="dual_env", pair_id="p1")
    _make_run(repo, run_id="r-b", run_type="dual_env", pair_id="p1")
    runs = repo.get_pair_runs("p1")
    assert len(runs) == 2
    assert {r.run_id for r in runs} == {"r-a", "r-b"}


def test_list_pairs_returns_unique_pair_ids(repo):
    _make_run(repo, run_id="r1", pair_id="p1")
    _make_run(repo, run_id="r2", pair_id="p1")
    _make_run(repo, run_id="r3", pair_id="p2")
    _make_run(repo, run_id="r4", pair_id="p2")
    pairs = repo.list_pairs()
    assert set(pairs) == {"p1", "p2"}


@pytest.fixture
def api_client(monkeypatch):
    from api.routes import runs as runs_module
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


def test_accept_mismatch_endpoint_404_on_unknown(api_client):
    resp = api_client.patch(
        "/api/runs/no-run/results/999/mismatches/999/accept",
        json={"note": "x"},
    )
    assert resp.status_code == 404


def test_accept_mismatch_note_required(api_client):
    resp = api_client.patch(
        "/api/runs/no-run/results/1/mismatches/1/accept",
        json={"note": ""},
    )
    assert resp.status_code == 422
