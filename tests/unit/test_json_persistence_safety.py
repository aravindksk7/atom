"""Row values and date-typed config must survive every path into a JSON column.

Comparison keys come straight from source data, so their Python type depends on
the database, file format and backend. json.dumps rejects most of those types,
which used to fail a run only once it had mismatches to store.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, time, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import JSON, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.services.difference_export import _json_text, _triage_key
from api.services.expectations_service import sync_suites
from etl_framework.reconciliation.backends.pandas_backend import PandasBackend
from etl_framework.reconciliation.mismatch_diff import diff_mismatches
from etl_framework.reconciliation.models import MismatchRecord, ReconciliationResult
from etl_framework.repository import database
from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.models import SavedJob
from etl_framework.repository.repository import JobRepository, RunRepository, TokenRepository
from etl_framework.runner.state import TestStatus
from etl_framework.utils.serialization import safe_json_dumps


def _strict_loads(text: str):
    def reject(constant):
        raise AssertionError(f"non-standard JSON constant: {constant}")

    return json.loads(text, parse_constant=reject)


def _engine(**kwargs):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        **kwargs,
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db():
    with Session(_engine()) as session:
        yield session


@pytest.fixture
def api(monkeypatch):
    from fastapi.testclient import TestClient

    from api.main import app

    engine = _engine()
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    with Session(engine) as setup_db:
        raw, _ = TokenRepository(setup_db).create("test-runner")
    return TestClient(app, headers={"Authorization": f"Bearer {raw}"}), engine


def _seed_result(db: Session, run_id: str | None = None, **overrides) -> tuple[RunRepository, str, int]:
    repo = RunRepository(db)
    run_id = run_id or str(uuid.uuid4())
    repo.create_run(run_id, "dev", "qa", {})
    fields = dict(
        query_name="curves", source_env="dev", target_env="qa",
        source_row_count=2, target_row_count=1, matched_count=1,
        missing_in_target_count=1, missing_in_source_count=0,
        value_mismatch_count=0, mismatches=[], status=TestStatus.FAILED,
        executed_at=datetime.now(timezone.utc), duration_seconds=1.0,
    )
    result = repo.add_test_result(run_id, ReconciliationResult(**{**fields, **overrides}))
    return repo, run_id, result.id


def _record(key_values: dict) -> MismatchRecord:
    return MismatchRecord(
        key_values=key_values, column_name="v", source_value="1", target_value="2",
        mismatch_type="value_diff",
    )


KEY_COLUMNS = {
    "datetime_tz": [pd.Timestamp("2026-10-06 10:15", tz="UTC"), pd.Timestamp("2026-10-07 10:15", tz="UTC")],
    "datetime_naive": [pd.Timestamp("2026-10-06"), pd.Timestamp("2026-10-07")],
    "date": [date(2026, 10, 6), date(2026, 10, 7)],
    "time": [time(1), time(2)],
    "decimal": [Decimal("1.5"), Decimal("2.5")],
    "uuid": [uuid.UUID(int=1), uuid.UUID(int=2)],
    "float_with_nan": [1.0, float("nan")],
    "str_with_none": ["a", None],
}


def _backend(name: str):
    if name == "pandas":
        return PandasBackend(key_columns=["k"])
    pytest.importorskip("polars")
    from etl_framework.reconciliation.backends.polars_backend import PolarsBackend

    return PolarsBackend(key_columns=["k"])


@pytest.mark.parametrize("backend", ["pandas", "polars"])
@pytest.mark.parametrize("dtype", list(KEY_COLUMNS))
def test_backend_mismatches_persist_as_strict_json_for_every_key_type(db, backend, dtype):
    values = KEY_COLUMNS[dtype]
    mismatches = _backend(backend).compare_with_counts(
        pd.DataFrame({"k": values, "v": [1, 2]}),
        pd.DataFrame({"k": values[:1], "v": [1]}),
    ).mismatches
    assert mismatches, "fixture must produce a mismatch to store"

    repo, _, result_id = _seed_result(db)
    repo.add_mismatch_details(result_id, mismatches)

    stored = repo.list_mismatches(result_id, limit=10)
    assert len(stored) == len(mismatches)
    for row in stored:
        _strict_loads(json.dumps(row.key_values))


def test_production_engine_serializes_every_json_column_safely():
    process = JSON().bind_processor(database.engine.dialect)
    payload = {"day": date(2026, 10, 6), "when": pd.Timestamp("2026-10-06", tz="UTC"), "gap": float("nan")}

    assert _strict_loads(process(payload)) == {
        "day": "2026-10-06",
        "when": "2026-10-06T00:00:00+00:00",
        "gap": None,
    }


def test_result_summary_columns_tolerate_row_values_on_production_serializer():
    with Session(_engine(json_serializer=safe_json_dumps)) as session:
        _, _, result_id = _seed_result(
            session,
            sample_rows=[{"when": pd.Timestamp("2026-10-06", tz="UTC"), "n": np.int64(3)}],
            segment_summary={"day": [{"value": date(2026, 10, 6)}]},
            mismatch_summary={"metrics": {"at": datetime(2026, 10, 6), "n": np.int64(3)}},
            schema_diff={"added": [Decimal("1.5")]},
        )
        session.expire_all()
        from etl_framework.repository.models import TestResult

        stored = session.get(TestResult, result_id)
        assert stored.sample_rows == [{"when": "2026-10-06T00:00:00+00:00", "n": 3}]
        assert stored.mismatch_summary["metrics"] == {"at": "2026-10-06T00:00:00", "n": 3}


def test_create_and_update_job_with_date_range_rule(api):
    client, engine = api
    rule = {"type": "date_range", "column": "d", "min_date": "2026-01-01", "max_date": "2026-12-31"}
    body = {
        "name": "dq-date", "job_type": "reconciliation", "query": "select 1",
        "key_columns": ["id"], "rules": [rule],
    }

    created = client.post("/api/jobs", json=body)
    assert created.status_code == 201, created.text
    assert created.json()["rules"][0]["min_date"] == "2026-01-01"

    updated = client.put("/api/jobs/dq-date", json=body)
    assert updated.status_code == 200, updated.text

    with Session(engine) as session:
        stored = session.query(SavedJob).filter_by(name="dq-date").one()
        assert stored.params["rules"][0]["max_date"] == "2026-12-31"


def test_suite_sync_stores_bare_yaml_dates_as_iso(tmp_path):
    (tmp_path / "suite.yml").write_text(
        "job: dq-suite\nrules:\n  - type: date_range\n    column: d\n    min_date: 2026-01-01\n",
        encoding="utf-8",
    )
    with Session(_engine(json_serializer=safe_json_dumps)) as session:
        JobRepository(session).create({
            "name": "dq-suite", "job_type": "reconciliation", "query": "select 1",
            "key_columns": ["id"], "params": {},
        })

        report = sync_suites(tmp_path, session)

        assert report.synced == ["dq-suite"]
        session.expire_all()
        assert JobRepository(session).get("dq-suite").params["rules"][0]["min_date"] == "2026-01-01"


def test_mismatch_diff_handles_nested_pair_key():
    record = _record({"id": 1, "__pair__": {"region": "east"}})

    assert diff_mismatches([record], [record]).summary == {"new": 0, "resolved": 0, "persistent": 1}


def test_mismatch_diff_matches_fresh_values_against_their_stored_form():
    fresh = _record({"when": pd.Timestamp("2026-10-06", tz="UTC")})
    stored = _record({"when": "2026-10-06T00:00:00+00:00"})

    assert diff_mismatches([stored], [fresh]).summary == {"new": 0, "resolved": 0, "persistent": 1}


@pytest.mark.parametrize("first,second", [(float("nan"), float("nan")), (float("nan"), None)], ids=["nan", "nan_none"])
def test_mismatch_diff_does_not_report_null_keys_as_regressions(first, second):
    diff = diff_mismatches([_record({"k": first})], [_record({"k": second})])

    assert diff.summary == {"new": 0, "resolved": 0, "persistent": 1}


def test_mismatch_diff_treats_integer_and_whole_float_keys_as_equal():
    diff = diff_mismatches([_record({"k": 9})], [_record({"k": 9.0})])

    assert diff.summary == {"new": 0, "resolved": 0, "persistent": 1}


def test_mismatch_diff_endpoint_accepts_file_mapping_keys(api):
    client, engine = api
    with Session(engine) as session:
        repo, run_id, result_id = _seed_result(session)
        repo.add_mismatch_details(result_id, [_record({"id": 1, "__pair__": {"region": "east"}})])

    resp = client.post("/api/compare/mismatch-diff", json={"run_id_a": run_id, "run_id_b": run_id})

    assert resp.status_code == 200, resp.text
    assert resp.json()["summary"] == {"new": 0, "resolved": 0, "persistent": 1}


def test_mismatch_read_endpoint_returns_strict_json_for_null_key(api):
    client, engine = api
    with Session(engine) as session:
        repo, run_id, result_id = _seed_result(session)
        repo.add_mismatch_details(result_id, [_record({"k": float("nan")})])

    resp = client.get(f"/api/runs/{run_id}/results/{result_id}/mismatches")

    assert resp.status_code == 200, resp.text
    assert _strict_loads(resp.text)[0]["key_values"] == {"k": None}


def test_triage_key_matches_between_stored_and_recomputed_datetime_keys():
    key = {"curve_id": 9501, "curve_date": pd.Timestamp("2026-10-06 10:15", tz="UTC")}
    stored = json.loads(safe_json_dumps(key))
    recomputed = json.loads(_json_text(key))

    assert _triage_key("t", "<row>", stored) == _triage_key("t", "<row>", recomputed)


def test_triage_key_keeps_decisions_on_rows_stored_before_nan_became_null():
    legacy_stored = {"k": float("nan")}
    recomputed = json.loads(_json_text({"k": float("nan")}))

    assert _triage_key("t", "<row>", legacy_stored) == _triage_key("t", "<row>", recomputed)
