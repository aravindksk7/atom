from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import RunRepository
from api.main import app
from api.dependencies import get_db
from etl_framework.repository import database as _db_module
from etl_framework.repository.models import TestResult, MismatchDetail, TestRun
from fastapi.testclient import TestClient


@pytest.fixture
def bulk_client(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    with Session(engine) as db:
        from etl_framework.repository.repository import TokenRepository
        raw_token, _ = TokenRepository(db).create("test-runner")

    def override_get_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, headers={"Authorization": f"Bearer {raw_token}"}) as client:
        yield client
    app.dependency_overrides.clear()


def _make_run(engine, run_id="bulk-run-001"):
    db = Session(engine)
    try:
        repo = RunRepository(db)
        run = repo.create_run(
            run_id=run_id,
            source_env="dev",
            target_env="prod",
            config_snapshot=None,
            run_type="reconciliation",
        )
        return run
    finally:
        db.close()


def test_bulk_accept_accepts_all_unaccepted_mismatches(bulk_client):
    engine = _db_module.SessionLocal().bind
    _make_run(engine)
    db = Session(engine)
    try:
        tr = TestResult(
            run_id="bulk-run-001", query_name="q1", status="FAILED",
            duration_seconds=1.0, source_row_count=10, target_row_count=10,
            value_mismatch_count=2, missing_in_target_count=0, missing_in_source_count=0,
        )
        db.add(tr); db.commit(); db.refresh(tr)
        m1 = MismatchDetail(test_result_id=tr.id, column_name="c1",
                            source_value="a", target_value="b", mismatch_type="value_diff")
        m2 = MismatchDetail(test_result_id=tr.id, column_name="c2",
                            source_value="x", target_value="y", mismatch_type="value_diff")
        db.add_all([m1, m2]); db.commit()
        tr_id = tr.id
        m1_id = m1.id
        m2_id = m2.id
    finally:
        db.close()

    resp = bulk_client.post(
        "/api/runs/bulk-run-001/results/bulk-accept",
        json={"result_ids": [tr_id], "note": "bulk accept test"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted_mismatch_count"] == 2
    assert data["result_status_updated"] == 1
    assert tr_id in data["result_ids"]

    db = Session(engine)
    try:
        for md_id in [m1_id, m2_id]:
            md = db.get(MismatchDetail, md_id)
            assert md.accepted is True
            assert md.accepted_note == "bulk accept test"

            refreshed_tr = db.get(TestResult, tr_id)
            assert refreshed_tr.status == "PASSED"
            run = db.query(TestRun).filter(TestRun.run_id == "bulk-run-001").first()
            assert run.passed == 1
            assert run.failed == 0
    finally:
        db.close()


def test_bulk_accept_does_not_reaccept_already_accepted(bulk_client):
    engine = _db_module.SessionLocal().bind
    _make_run(engine)
    db = Session(engine)
    try:
        tr = TestResult(
            run_id="bulk-run-001", query_name="q1", status="FAILED",
            duration_seconds=1.0, source_row_count=10, target_row_count=10,
            value_mismatch_count=1, missing_in_target_count=0, missing_in_source_count=0,
        )
        db.add(tr); db.commit(); db.refresh(tr)
        md = MismatchDetail(test_result_id=tr.id, column_name="c1",
                            source_value="a", target_value="b", mismatch_type="value_diff",
                            accepted=True)
        db.add(md); db.commit()
        tr_id = tr.id
    finally:
        db.close()

    resp = bulk_client.post(
        "/api/runs/bulk-run-001/results/bulk-accept",
        json={"result_ids": [tr_id], "note": "bulk accept test"},
    )
    assert resp.status_code == 200
    assert resp.json()["accepted_mismatch_count"] == 0
    assert resp.json()["result_status_updated"] == 0


def test_bulk_accept_rejects_result_from_other_run(bulk_client):
    engine = _db_module.SessionLocal().bind
    _make_run(engine, "run-a")
    _make_run(engine, "run-b")
    db = Session(engine)
    try:
        tr_b = TestResult(
            run_id="run-b", query_name="q-cross", status="FAILED",
            duration_seconds=1.0, source_row_count=1, target_row_count=1,
            value_mismatch_count=1, missing_in_target_count=0, missing_in_source_count=0,
        )
        db.add(tr_b); db.commit(); db.refresh(tr_b)
        tr_b_id = tr_b.id
    finally:
        db.close()

    resp = bulk_client.post(
        "/api/runs/run-a/results/bulk-accept",
        json={"result_ids": [tr_b_id], "note": "wrong run"},
    )
    assert resp.status_code == 404
    assert "Result" in resp.json()["detail"]


def test_bulk_accept_requires_note(bulk_client):
    resp = bulk_client.post(
        "/api/runs/bulk-run-001/results/bulk-accept",
        json={"result_ids": [1], "note": ""},
    )
    assert resp.status_code == 422


def test_bulk_override_marks_failed_tests_as_passed(bulk_client):
    engine = _db_module.SessionLocal().bind
    _make_run(engine)
    db = Session(engine)
    try:
        tr = TestResult(
            run_id="bulk-run-001", query_name="q1", status="FAILED",
            duration_seconds=1.0, source_row_count=10, target_row_count=10,
            value_mismatch_count=1, missing_in_target_count=0, missing_in_source_count=0,
        )
        db.add(tr); db.commit(); db.refresh(tr)
        tr_id = tr.id
    finally:
        db.close()

    resp = bulk_client.post(
        "/api/runs/bulk-run-001/results/bulk-override",
        json={"result_ids": [tr_id], "reason": "data drift expected"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["effective_status"] == "PASSED"
    assert data[0]["override_reason"] == "data drift expected"

    db = Session(engine)
    try:
        refreshed_tr = db.get(TestResult, tr_id)
        assert refreshed_tr.override_status == "PASSED"
        assert refreshed_tr.override_reason == "data drift expected"
    finally:
        db.close()


def test_bulk_override_rejects_non_failed_result(bulk_client):
    engine = _db_module.SessionLocal().bind
    _make_run(engine)
    db = Session(engine)
    try:
        tr = TestResult(
            run_id="bulk-run-001", query_name="q1", status="PASSED",
            duration_seconds=1.0, source_row_count=10, target_row_count=10,
            value_mismatch_count=0, missing_in_target_count=0, missing_in_source_count=0,
        )
        db.add(tr); db.commit(); db.refresh(tr)
        tr_id = tr.id
    finally:
        db.close()

    resp = bulk_client.post(
        "/api/runs/bulk-run-001/results/bulk-override",
        json={"result_ids": [tr_id], "reason": "should fail"},
    )
    assert resp.status_code == 409


def test_reject_mismatch_clears_prior_accept_and_vice_versa(bulk_client):
    engine = _db_module.SessionLocal().bind
    _make_run(engine)
    db = Session(engine)
    try:
        tr = TestResult(
            run_id="bulk-run-001", query_name="q1", status="FAILED",
            duration_seconds=1.0, source_row_count=10, target_row_count=10,
            value_mismatch_count=1, missing_in_target_count=0, missing_in_source_count=0,
        )
        db.add(tr); db.commit(); db.refresh(tr)
        md = MismatchDetail(test_result_id=tr.id, column_name="c1",
                            source_value="a", target_value="b", mismatch_type="value_diff",
                            accepted=True, accepted_note="was accepted")
        db.add(md); db.commit(); db.refresh(md)
        tr_id, md_id = tr.id, md.id
    finally:
        db.close()

    from etl_framework.repository.repository import RunRepository
    db = Session(engine)
    try:
        repo = RunRepository(db)
        updated, _ = repo.reject_mismatch(md_id, "actually wrong", "qa-lead")
        assert updated.rejected is True
        assert updated.rejected_note == "actually wrong"
        assert updated.accepted is False
        assert updated.accepted_note is None

        updated2, _ = repo.accept_mismatch(md_id, "re-accepted", "qa-lead")
        assert updated2.accepted is True
        assert updated2.rejected is False
        assert updated2.rejected_note is None
    finally:
        db.close()


def test_bulk_decide_mismatches_accept_respects_filter(bulk_client):
    engine = _db_module.SessionLocal().bind
    _make_run(engine)
    db = Session(engine)
    try:
        tr = TestResult(
            run_id="bulk-run-001", query_name="q1", status="FAILED",
            duration_seconds=1.0, source_row_count=10, target_row_count=10,
            value_mismatch_count=3, missing_in_target_count=0, missing_in_source_count=0,
        )
        db.add(tr); db.commit(); db.refresh(tr)
        m1 = MismatchDetail(test_result_id=tr.id, column_name="amount", source_value="1", target_value="2", mismatch_type="value_diff")
        m2 = MismatchDetail(test_result_id=tr.id, column_name="amount", source_value="3", target_value="4", mismatch_type="value_diff")
        m3 = MismatchDetail(test_result_id=tr.id, column_name="status", source_value="A", target_value="B", mismatch_type="value_diff")
        db.add_all([m1, m2, m3]); db.commit()
        tr_id, m3_id = tr.id, m3.id
    finally:
        db.close()

    from etl_framework.repository.repository import RunRepository
    db = Session(engine)
    try:
        repo = RunRepository(db)
        summary = repo.bulk_decide_mismatches(
            tr_id, decision="accept", note="rounding tolerance", decided_by="qa-lead",
            column="amount",
        )
        assert summary["matched_count"] == 2
        assert summary["decided_count"] == 2
        assert summary["result_status_updated"] is False

        m3 = db.get(MismatchDetail, m3_id)
        assert m3.accepted is False
    finally:
        db.close()


def test_bulk_decide_mismatches_accept_all_rows_flips_result_to_passed(bulk_client):
    engine = _db_module.SessionLocal().bind
    _make_run(engine)
    db = Session(engine)
    try:
        tr = TestResult(
            run_id="bulk-run-001", query_name="q1", status="FAILED",
            duration_seconds=1.0, source_row_count=10, target_row_count=10,
            value_mismatch_count=2, missing_in_target_count=0, missing_in_source_count=0,
        )
        db.add(tr); db.commit(); db.refresh(tr)
        db.add_all([
            MismatchDetail(test_result_id=tr.id, column_name="c1", source_value="a", target_value="b", mismatch_type="value_diff"),
            MismatchDetail(test_result_id=tr.id, column_name="c2", source_value="x", target_value="y", mismatch_type="value_diff"),
        ])
        db.commit()
        tr_id = tr.id
    finally:
        db.close()

    from etl_framework.repository.repository import RunRepository
    db = Session(engine)
    try:
        repo = RunRepository(db)
        summary = repo.bulk_decide_mismatches(tr_id, decision="accept", note="all good", decided_by=None)
        assert summary["decided_count"] == 2
        assert summary["result_status_updated"] is True
        assert db.get(TestResult, tr_id).status == "PASSED"
    finally:
        db.close()


def test_bulk_decide_mismatches_reject_never_flips_result_to_passed(bulk_client):
    engine = _db_module.SessionLocal().bind
    _make_run(engine)
    db = Session(engine)
    try:
        tr = TestResult(
            run_id="bulk-run-001", query_name="q1", status="FAILED",
            duration_seconds=1.0, source_row_count=10, target_row_count=10,
            value_mismatch_count=1, missing_in_target_count=0, missing_in_source_count=0,
        )
        db.add(tr); db.commit(); db.refresh(tr)
        db.add(MismatchDetail(test_result_id=tr.id, column_name="c1", source_value="a", target_value="b", mismatch_type="value_diff"))
        db.commit()
        tr_id = tr.id
    finally:
        db.close()

    from etl_framework.repository.repository import RunRepository
    db = Session(engine)
    try:
        repo = RunRepository(db)
        summary = repo.bulk_decide_mismatches(tr_id, decision="reject", note="confirmed real diff", decided_by=None)
        assert summary["decided_count"] == 1
        assert summary["result_status_updated"] is False
        assert db.get(TestResult, tr_id).status == "FAILED"
    finally:
        db.close()


def test_reject_mismatch_endpoint(bulk_client):
    engine = _db_module.SessionLocal().bind
    _make_run(engine)
    db = Session(engine)
    try:
        tr = TestResult(
            run_id="bulk-run-001", query_name="q1", status="FAILED",
            duration_seconds=1.0, source_row_count=10, target_row_count=10,
            value_mismatch_count=1, missing_in_target_count=0, missing_in_source_count=0,
        )
        db.add(tr); db.commit(); db.refresh(tr)
        md = MismatchDetail(test_result_id=tr.id, column_name="c1",
                            source_value="a", target_value="b", mismatch_type="value_diff")
        db.add(md); db.commit(); db.refresh(md)
        tr_id, md_id = tr.id, md.id
    finally:
        db.close()

    resp = bulk_client.patch(
        f"/api/runs/bulk-run-001/results/{tr_id}/mismatches/{md_id}/reject",
        json={"note": "confirmed real diff"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["rejected"] is True
    assert data["rejected_note"] == "confirmed real diff"
    assert data["accepted"] is False


def test_reject_mismatch_endpoint_requires_note(bulk_client):
    resp = bulk_client.patch(
        "/api/runs/bulk-run-001/results/1/mismatches/1/reject",
        json={"note": ""},
    )
    assert resp.status_code == 422


def test_bulk_decide_endpoint_filtered_and_all_rows(bulk_client):
    engine = _db_module.SessionLocal().bind
    _make_run(engine)
    db = Session(engine)
    try:
        tr = TestResult(
            run_id="bulk-run-001", query_name="q1", status="FAILED",
            duration_seconds=1.0, source_row_count=10, target_row_count=10,
            value_mismatch_count=3, missing_in_target_count=0, missing_in_source_count=0,
        )
        db.add(tr); db.commit(); db.refresh(tr)
        db.add_all([
            MismatchDetail(test_result_id=tr.id, column_name="amount", source_value="1", target_value="2", mismatch_type="value_diff"),
            MismatchDetail(test_result_id=tr.id, column_name="amount", source_value="3", target_value="4", mismatch_type="value_diff"),
            MismatchDetail(test_result_id=tr.id, column_name="status", source_value="A", target_value="B", mismatch_type="value_diff"),
        ])
        db.commit()
        tr_id = tr.id
    finally:
        db.close()

    filtered = bulk_client.post(
        f"/api/runs/bulk-run-001/results/{tr_id}/mismatches/bulk-decide",
        json={"decision": "accept", "note": "rounding", "column": "amount"},
    )
    assert filtered.status_code == 200
    assert filtered.json()["decided_count"] == 2
    assert filtered.json()["result_status_updated"] is False

    all_rows = bulk_client.post(
        f"/api/runs/bulk-run-001/results/{tr_id}/mismatches/bulk-decide",
        json={"decision": "accept", "note": "the rest"},
    )
    assert all_rows.status_code == 200
    assert all_rows.json()["decided_count"] == 1
    assert all_rows.json()["result_status_updated"] is True


def test_bulk_decide_endpoint_404_for_missing_run(bulk_client):
    resp = bulk_client.post(
        "/api/runs/no-such-run/results/1/mismatches/bulk-decide",
        json={"decision": "accept", "note": "x"},
    )
    assert resp.status_code == 404
