"""Exports of runs made of saved jobs: SQL compare jobs, errored jobs, and jobs that
cannot be rebuilt must never come back as a silently blank file."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from api.schemas import RunSettings
from api.services import difference_export as de
from api.services.export_progress import ExportProgress
from etl_framework.reconciliation.models import MismatchRecord, ReconciliationResult
from etl_framework.repository import database as _db_module
from etl_framework.repository.database import Base
from etl_framework.repository.models import DifferenceExportJob, TestResult, TestRun
from etl_framework.repository.repository import ConfigRepository, JobRepository, RunRepository
from etl_framework.runner.state import TestStatus


class _FrameDBEngine:
    frames: dict[str, pd.DataFrame] = {}
    queries: list[str] = []

    def __init__(self, env) -> None:
        self._env = env

    def execute_query(self, query, params=None):
        type(self).queries.append(query)
        return type(self).frames[query].copy()

    def dispose(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _environment(monkeypatch, tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'repo.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    # Creating the tables costs over a second per test on Windows with disk sync on.
    event.listen(engine, "connect", lambda connection, _: connection.execute("PRAGMA synchronous=OFF"))
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(de, "SessionLocal", _db_module.SessionLocal)
    monkeypatch.setattr(de, "EXPORT_ROOT", tmp_path / "exports")
    monkeypatch.setattr("etl_framework.db.engine.DBEngine", _FrameDBEngine)
    _FrameDBEngine.frames = {}
    _FrameDBEngine.queries = []
    yield
    engine.dispose()


def _config_id(db) -> int:
    existing = ConfigRepository(db).list()
    if existing:
        return existing[0].id
    return ConfigRepository(db).create(
        name="warehouse",
        env_name="dev",
        config_data={"db_host": "h", "db_name": "d", "db_user": "u", "db_password": "p"},
    ).id


def _save_sql_job(db, name: str, source: pd.DataFrame, target: pd.DataFrame, **request) -> None:
    query_a, query_b = f"SELECT * FROM {name}_a", f"SELECT * FROM {name}_b"
    _FrameDBEngine.frames[query_a] = source
    _FrameDBEngine.frames[query_b] = target
    config_id = _config_id(db)
    JobRepository(db).create({
        "name": name, "description": "", "tags": [], "job_type": "compare", "query": "",
        "key_columns": ["id"], "exclude_columns": [], "source_env": None, "target_env": None,
        "enabled": True,
        "params": {"compare_type": "sql", "request": {
            "config_id_a": config_id, "config_id_b": config_id,
            "query_a": query_a, "query_b": query_b,
            "key_columns": ["id"], "label_a": "A", "label_b": "B",
            **request,
        }},
    })


def _save_job(db, name: str, job_type: str, params: dict | None = None) -> None:
    JobRepository(db).create({
        "name": name, "description": "", "tags": [], "job_type": job_type, "query": "",
        "key_columns": ["id"], "exclude_columns": [], "source_env": None, "target_env": None,
        "enabled": True, "params": params or {},
    })


def _run(db, jobs: list[str], *, live: bool = True, run_id: str = "run-1") -> str:
    RunRepository(db).create_run(
        run_id=run_id, source_env="dev", target_env="uat",
        config_snapshot={
            "job_sequence": [{"job_name": name} for name in jobs],
            "run_settings": RunSettings(use_live_connections=live).model_dump(),
        },
    )
    return run_id


def _add_result(db, run_id: str, name: str, issues: int = 0, stored: int = 0) -> None:
    repo = RunRepository(db)
    result = repo.add_test_result(run_id, ReconciliationResult(
        query_name=name, source_env="dev", target_env="uat",
        source_row_count=issues, target_row_count=issues, matched_count=issues,
        missing_in_target_count=0, missing_in_source_count=0, value_mismatch_count=issues,
        mismatches=[],
        status=TestStatus.FAILED if issues else TestStatus.PASSED,
        executed_at=datetime.now(timezone.utc), duration_seconds=0.1,
    ))
    repo.add_mismatch_details(result.id, [
        MismatchRecord(
            key_values={"id": index}, column_name="amount", source_value=index,
            target_value=index + 1, mismatch_type="value_diff",
        )
        for index in range(stored)
    ])


def _add_error(db, run_id: str, name: str, message: str) -> None:
    db.add(TestResult(
        run_id=run_id, query_name=name, status="ERROR", duration_seconds=0.0,
        source_row_count=0, target_row_count=0, value_mismatch_count=0,
        missing_in_target_count=0, missing_in_source_count=0, error_message=message,
        executed_at=datetime.now(timezone.utc),
    ))
    db.commit()


def _get_run(db, run_id: str = "run-1") -> TestRun:
    return db.query(TestRun).filter(TestRun.run_id == run_id).one()


def _recompute(db, tmp_path, run_id: str = "run-1") -> list[dict]:
    path = tmp_path / f"{run_id}.jsonl"
    count = de.write_recomputed_differences(db, _get_run(db, run_id), "json", path)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    assert count == len(rows)
    return rows


def _differing_frames(rows: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    keys = list(range(1, rows + 1))
    return (
        pd.DataFrame({"id": keys, "amount": [1.0] * rows}),
        pd.DataFrame({"id": keys, "amount": [2.0] * rows}),
    )


def test_recompute_writes_every_difference_of_each_sql_compare_job(tmp_path):
    with _db_module.SessionLocal() as db:
        _save_sql_job(db, "curves", *_differing_frames(3))
        _save_sql_job(db, "rates", *_differing_frames(2))
        _run(db, ["curves", "rates"])

        rows = _recompute(db, tmp_path)

    assert [row["test_name"] for row in rows] == ["curves"] * 3 + ["rates"] * 2
    assert {row["column_name"] for row in rows} == {"amount"}
    assert {row["mismatch_type"] for row in rows} == {"value_diff"}


def test_sql_job_recompute_aligns_column_case_like_the_live_run(tmp_path):
    source = pd.DataFrame({"id": [1], "amount": [1.0]})
    target = pd.DataFrame({"ID": [1], "AMOUNT": [2.0]})
    with _db_module.SessionLocal() as db:
        _save_sql_job(db, "curves", source, target)
        _run(db, ["curves"])

        rows = _recompute(db, tmp_path)

    assert [row["column_name"] for row in rows] == ["AMOUNT"]


def test_sql_job_with_live_connections_off_becomes_a_note(tmp_path):
    with _db_module.SessionLocal() as db:
        _save_sql_job(db, "curves", *_differing_frames(1))
        _run(db, ["curves"], live=False)

        rows = _recompute(db, tmp_path)

    assert [row["mismatch_type"] for row in rows] == [de.EXPORT_INCOMPLETE_TYPE]
    assert "live connections" in rows[0]["source_value"]
    assert _FrameDBEngine.queries == []


def test_sql_job_with_a_query_that_is_not_read_only_becomes_a_note(tmp_path):
    with _db_module.SessionLocal() as db:
        _save_sql_job(db, "curves", *_differing_frames(1))
        job = JobRepository(db).get("curves")
        job.params = {
            **job.params,
            "request": {**job.params["request"], "query_b": "DELETE FROM curves"},
        }
        db.commit()
        _run(db, ["curves"])

        rows = _recompute(db, tmp_path)

    assert [row["mismatch_type"] for row in rows] == [de.EXPORT_INCOMPLETE_TYPE]
    assert "read-only" in rows[0]["source_value"]
    assert _FrameDBEngine.queries == []


def test_recompute_reports_an_errored_job_without_running_it_again(tmp_path):
    with _db_module.SessionLocal() as db:
        _save_sql_job(db, "curves", *_differing_frames(2))
        _save_sql_job(db, "rates", *_differing_frames(1))
        run_id = _run(db, ["curves", "rates"])
        _add_error(db, run_id, "curves", "join keys don't match")
        _add_result(db, run_id, "rates", issues=1)

        rows = _recompute(db, tmp_path)

    by_job = {row["test_name"]: row for row in rows}
    assert by_job["curves"]["mismatch_type"] == de.JOB_ERROR_TYPE
    assert by_job["curves"]["source_value"] == "join keys don't match"
    assert by_job["rates"]["mismatch_type"] == "value_diff"
    assert "SELECT * FROM curves_a" not in _FrameDBEngine.queries


def test_stored_csv_lists_an_errored_job_beside_the_other_jobs_rows():
    with _db_module.SessionLocal() as db:
        run_id = _run(db, ["curves", "rates"])
        _add_error(db, run_id, "curves", "join keys don't match")
        _add_result(db, run_id, "rates", issues=1, stored=1)

        run = _get_run(db)
        assert de.stored_rows_are_complete(db, run)
        path, count = de.write_stored_differences(db, run, "csv")

    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert count == len(rows) == 2
    assert {row["test_name"]: row["mismatch_type"] for row in rows} == {
        "curves": de.JOB_ERROR_TYPE,
        "rates": "value_diff",
    }


def test_job_that_cannot_be_rebuilt_falls_back_to_its_stored_rows_and_says_it_is_partial(tmp_path):
    with _db_module.SessionLocal() as db:
        _save_job(db, "api_orders", "api_reconciliation")
        run_id = _run(db, ["api_orders"])
        _add_result(db, run_id, "api_orders", issues=5, stored=2)

        rows = _recompute(db, tmp_path)

    assert [row["mismatch_type"] for row in rows] == [
        "value_diff", "value_diff", de.EXPORT_INCOMPLETE_TYPE,
    ]
    assert "2 of 5" in rows[-1]["source_value"]


def test_job_that_cannot_be_rebuilt_but_has_all_its_rows_stored_adds_no_note(tmp_path):
    with _db_module.SessionLocal() as db:
        _save_job(db, "api_orders", "api_reconciliation")
        run_id = _run(db, ["api_orders"])
        _add_result(db, run_id, "api_orders", issues=2, stored=2)

        rows = _recompute(db, tmp_path)

    assert [row["mismatch_type"] for row in rows] == ["value_diff", "value_diff"]


def test_job_with_no_differences_adds_nothing_to_the_export(tmp_path):
    with _db_module.SessionLocal() as db:
        _save_job(db, "ds_load", "ds_job")
        run_id = _run(db, ["ds_load"])
        _add_result(db, run_id, "ds_load")

        assert _recompute(db, tmp_path) == []


def test_deleted_job_falls_back_to_its_stored_rows(tmp_path):
    with _db_module.SessionLocal() as db:
        run_id = _run(db, ["removed_job"])
        _add_result(db, run_id, "removed_job", issues=1, stored=1)

        rows = _recompute(db, tmp_path)

    assert [row["test_name"] for row in rows] == ["removed_job"]


def test_report_shaped_recon_file_job_falls_back_to_its_stored_rows(tmp_path):
    (tmp_path / "a.html").write_text("<html></html>", encoding="utf-8")
    with _db_module.SessionLocal() as db:
        _save_job(db, "stats", "compare", {"compare_type": "recon_file", "request": {
            "file_a_path": "a.html", "file_b_path": "b.html", "key_columns": ["id"],
        }})
        run_id = _run(db, ["stats"])
        _add_result(db, run_id, "stats", issues=1, stored=1)

        original = de.CompareService._load_recon_source
        de.CompareService._load_recon_source = lambda self, req, side: {"t": {"status": side}}
        try:
            rows = _recompute(db, tmp_path)
        finally:
            de.CompareService._load_recon_source = original

    assert [row["test_name"] for row in rows] == ["stats"]


def test_sql_job_recompute_uses_the_variable_values_of_its_run(tmp_path):
    with _db_module.SessionLocal() as db:
        _save_sql_job(db, "curves", *_differing_frames(2))
        job = JobRepository(db).get("curves")
        request = job.params["request"]
        job.params = {**job.params, "request": {
            **request,
            "query_a": "SELECT * FROM {{TABLE}}_a",
            "query_b": "SELECT * FROM {{TABLE}}_b",
        }}
        db.commit()
        run_id = _run(db, ["curves"])
        run = _get_run(db, run_id)
        run.config_snapshot = {**run.config_snapshot, "variables": {"TABLE": "curves"}}
        db.commit()

        rows = _recompute(db, tmp_path)

    assert len(rows) == 2
    assert _FrameDBEngine.queries == ["SELECT * FROM curves_a", "SELECT * FROM curves_b"]


def test_recompute_does_not_query_a_job_that_passed(tmp_path):
    with _db_module.SessionLocal() as db:
        _save_sql_job(db, "passed_job", *_differing_frames(1))
        _save_sql_job(db, "failed_job", *_differing_frames(1))
        run_id = _run(db, ["passed_job", "failed_job"])
        _add_result(db, run_id, "passed_job")
        _add_result(db, run_id, "failed_job", issues=1)

        rows = _recompute(db, tmp_path)

    assert [row["test_name"] for row in rows] == ["failed_job"]
    assert not [query for query in _FrameDBEngine.queries if "passed_job" in query]


def test_oracle_to_postgres_sequence_exports_every_difference_of_its_failed_jobs(tmp_path):
    """35 SQL compare jobs run serially by a saved sequence, 25 passing and 10 failing.
    One failed job passing its row cap sends the whole run down the recompute path.
    Oracle returns NUMBER keys as Decimal under upper-case names; Postgres does not."""
    from api.routes.sequences import _do_launch_sequence
    from api.schemas import SequenceLaunchRequest
    from etl_framework.repository.sequence_repository import ExecutionSequenceRepository

    def oracle_frame(rows: int) -> pd.DataFrame:
        return pd.DataFrame({
            "ID": [Decimal(index) for index in range(1, rows + 1)],
            "AMOUNT": [Decimal("1.5")] * rows,
        })

    def postgres_frame(rows: int, amount: float) -> pd.DataFrame:
        return pd.DataFrame({"id": list(range(1, rows + 1)), "amount": [amount] * rows})

    # The first failed job holds 8 differences against a cap of 5, the others 2 each.
    failed_rows = [8] + [2] * 9
    names = [f"passed_{index}" for index in range(25)] + [f"failed_{index}" for index in range(10)]
    with _db_module.SessionLocal() as db:
        limit = {"advanced": {"mismatch_row_limit": 5}}
        for index in range(25):
            _save_sql_job(db, f"passed_{index}", oracle_frame(3), postgres_frame(3, 1.5), **limit)
        for index, rows in enumerate(failed_rows):
            _save_sql_job(db, f"failed_{index}", oracle_frame(rows), postgres_frame(rows, 9.0), **limit)
        config_id = _config_id(db)
        sequence = ExecutionSequenceRepository(db).create(
            name="oracle_to_postgres", description="", tags=[],
            steps=[
                {
                    "step_id": name, "job_name": name, "trigger_rule": "all_done",
                    "depends_on": [names[index - 1]] if index else [],
                }
                for index, name in enumerate(names)
            ],
            defaults={
                "source_env": "oracle", "target_env": "postgres", "config_id": config_id,
                "run_settings": RunSettings(
                    use_live_connections=True, execution_mode="sequential",
                ).model_dump(),
            },
        )
        run_id = _do_launch_sequence(sequence.id, SequenceLaunchRequest(), None, None, db)

    expected = sum(failed_rows)
    with _db_module.SessionLocal() as db:
        run = _get_run(db, run_id)
        assert sorted(result.status for result in run.results) == ["FAILED"] * 10 + ["PASSED"] * 25
        assert sum(result.total_issues for result in run.results) == expected
        assert not de.stored_rows_are_complete(db, run)
        csv_job, _ = de.create_or_reuse_export_job(db, run_id, "csv")
        html_job, _ = de.create_or_reuse_export_job(db, run_id, "html")
        csv_id, html_id = csv_job.export_id, html_job.export_id

    _FrameDBEngine.queries = []
    de.run_difference_export_job(csv_id)
    de.run_difference_export_job(html_id)

    with _db_module.SessionLocal() as db:
        finished = {
            job.format: job
            for job in db.query(DifferenceExportJob).filter(DifferenceExportJob.run_id == run_id)
        }
        assert finished["csv"].error_message is None
        assert finished["html"].error_message is None
        assert finished["csv"].row_count == finished["html"].row_count == expected
        csv_path, html_path = finished["csv"].artifact_path, finished["html"].artifact_path

    with open(csv_path, encoding="utf-8", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    assert len(csv_rows) == expected
    assert {row["test_name"] for row in csv_rows} == {f"failed_{index}" for index in range(10)}
    assert open(html_path, encoding="utf-8").read().count('data-type="value_diff"') == expected
    assert not [query for query in _FrameDBEngine.queries if "passed_" in query]


def _completed_export(db, run_id: str, path, rows: int, export_id: str = "old") -> None:
    path.write_text("test_name\n", encoding="utf-8")
    db.add(DifferenceExportJob(
        export_id=export_id, run_id=run_id, format="csv", status="COMPLETED", row_count=rows,
        artifact_path=str(path),
        created_at=datetime.now(timezone.utc), completed_at=datetime.now(timezone.utc),
    ))
    db.commit()


def test_blank_export_of_a_run_with_differences_is_not_reused(tmp_path):
    with _db_module.SessionLocal() as db:
        run_id = _run(db, ["curves"])
        _add_result(db, run_id, "curves", issues=3)
        _completed_export(db, run_id, tmp_path / "old.csv", rows=0)

        job, created = de.create_or_reuse_export_job(db, run_id, "csv")

    assert created is True
    assert job.export_id != "old"


def test_partly_blank_export_is_not_reused(tmp_path):
    with _db_module.SessionLocal() as db:
        run_id = _run(db, ["curves", "rates"])
        _add_result(db, run_id, "curves", issues=3)
        _add_result(db, run_id, "rates", issues=2)
        _completed_export(db, run_id, tmp_path / "old.csv", rows=2)

        _, created = de.create_or_reuse_export_job(db, run_id, "csv")

    assert created is True


def test_blank_export_of_a_run_with_an_errored_job_is_not_reused(tmp_path):
    with _db_module.SessionLocal() as db:
        run_id = _run(db, ["curves"])
        _add_error(db, run_id, "curves", "boom")
        _completed_export(db, run_id, tmp_path / "old.csv", rows=0)

        _, created = de.create_or_reuse_export_job(db, run_id, "csv")

    assert created is True


@pytest.mark.parametrize("issues,rows", [(3, 3), (0, 0)])
def test_export_holding_every_difference_is_reused(tmp_path, issues, rows):
    with _db_module.SessionLocal() as db:
        run_id = _run(db, ["curves"])
        _add_result(db, run_id, "curves", issues=issues)
        _completed_export(db, run_id, tmp_path / "old.csv", rows=rows)

        job, created = de.create_or_reuse_export_job(db, run_id, "csv")

    assert created is False
    assert job.export_id == "old"


def test_full_html_report_holds_sql_job_rows_and_the_errored_jobs_message(tmp_path):
    source, target = _differing_frames(2)
    with _db_module.SessionLocal() as db:
        _save_sql_job(db, "curves", source, target)
        _save_sql_job(db, "broken", *_differing_frames(1))
        run_id = _run(db, ["curves", "broken"])
        _add_result(db, run_id, "curves", issues=2)
        _add_error(db, run_id, "broken", "join keys do not match")

        path = tmp_path / "report.html"
        count = de.write_full_html_report(db, _get_run(db), path)

    html = path.read_text(encoding="utf-8")
    assert count == 3
    assert "join keys do not match" in html
    assert html.count('data-type="value_diff"') == 2


def test_full_html_report_does_not_count_a_note_as_a_difference(tmp_path, monkeypatch):
    from etl_framework.reporting.generator import ReportGenerator

    captured = {}

    def _capture(self, snapshot, filename=None):
        captured.update({result.query_name: result for result in snapshot.results})

    monkeypatch.setattr(ReportGenerator, "generate", _capture)
    with _db_module.SessionLocal() as db:
        run_id = _run(db, ["broken"])
        _add_error(db, run_id, "broken", "boom")

        de.write_full_html_report(db, _get_run(db), tmp_path / "report.html")

    assert captured["broken"].total_issues_override == 0
    assert [row["mismatch_type"] for row in captured["broken"].mismatches] == [de.JOB_ERROR_TYPE]


def _open_export(db, run_id: str, status: str, *, age_seconds: float, heartbeat_age: float | None = None):
    now = datetime.now(timezone.utc)
    job = DifferenceExportJob(
        export_id=f"open-{status}", run_id=run_id, format="csv", status=status,
        created_at=now - timedelta(seconds=age_seconds),
        heartbeat_at=None if heartbeat_age is None else now - timedelta(seconds=heartbeat_age),
    )
    db.add(job)
    db.commit()
    return job


def test_running_export_with_a_dead_heartbeat_expires():
    with _db_module.SessionLocal() as db:
        run_id = _run(db, ["curves"])
        job = _open_export(db, run_id, "RUNNING", age_seconds=900, heartbeat_age=301)

        assert de.expire_if_stale(db, job) is True

        assert job.status == "FAILED"
        assert "stopped unexpectedly" in job.error_message


def test_running_export_with_a_fresh_heartbeat_does_not_expire():
    with _db_module.SessionLocal() as db:
        run_id = _run(db, ["curves"])
        job = _open_export(db, run_id, "RUNNING", age_seconds=9000, heartbeat_age=10)

        assert de.expire_if_stale(db, job) is False
        assert job.status == "RUNNING"


def test_pending_export_that_never_started_expires_from_its_creation_time():
    with _db_module.SessionLocal() as db:
        run_id = _run(db, ["curves"])
        job = _open_export(db, run_id, "PENDING", age_seconds=301)

        assert de.expire_if_stale(db, job) is True


def test_finished_export_never_expires():
    with _db_module.SessionLocal() as db:
        run_id = _run(db, ["curves"])
        job = _open_export(db, run_id, "COMPLETED", age_seconds=9000)

        assert de.expire_if_stale(db, job) is False
        assert job.status == "COMPLETED"


def test_new_request_replaces_a_stuck_export():
    with _db_module.SessionLocal() as db:
        run_id = _run(db, ["curves"])
        stuck = _open_export(db, run_id, "RUNNING", age_seconds=900, heartbeat_age=600)

        job, created = de.create_or_reuse_export_job(db, run_id, "csv")

        assert created is True
        assert job.export_id != stuck.export_id
        assert stuck.status == "FAILED"


def test_new_request_joins_a_live_export():
    with _db_module.SessionLocal() as db:
        run_id = _run(db, ["curves"])
        live = _open_export(db, run_id, "RUNNING", age_seconds=900, heartbeat_age=5)

        job, created = de.create_or_reuse_export_job(db, run_id, "csv")

        assert created is False
        assert job.export_id == live.export_id


def test_export_with_a_failed_job_is_rebuilt_on_the_next_request(tmp_path):
    with _db_module.SessionLocal() as db:
        run_id = _run(db, ["curves"])
        _add_result(db, run_id, "curves", issues=1)
        _completed_export(db, run_id, tmp_path / "old.csv", rows=5)
        old = db.query(DifferenceExportJob).filter_by(export_id="old").one()
        old.metadata_json = {"failed_jobs": ["curves"]}
        db.commit()

        _, created = de.create_or_reuse_export_job(db, run_id, "csv")

    assert created is True


def test_export_noting_only_unrebuildable_jobs_is_reused(tmp_path):
    with _db_module.SessionLocal() as db:
        run_id = _run(db, ["curves"])
        _add_result(db, run_id, "curves", issues=1)
        _completed_export(db, run_id, tmp_path / "old.csv", rows=5)
        old = db.query(DifferenceExportJob).filter_by(export_id="old").one()
        old.metadata_json = {"failed_jobs": []}
        db.commit()

        _, created = de.create_or_reuse_export_job(db, run_id, "csv")

    assert created is False


def test_failing_job_uses_its_stored_rows_and_the_next_job_still_exports(tmp_path):
    with _db_module.SessionLocal() as db:
        _save_sql_job(db, "broken", *_differing_frames(4))
        _save_sql_job(db, "healthy", *_differing_frames(2))
        del _FrameDBEngine.frames["SELECT * FROM broken_a"]
        run_id = _run(db, ["broken", "healthy"])
        _add_result(db, run_id, "broken", issues=4, stored=1)
        _add_result(db, run_id, "healthy", issues=2)
        progress = ExportProgress()

        path = tmp_path / "out.jsonl"
        count = de.write_recomputed_differences(db, _get_run(db), "json", path, progress=progress)

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert count == len(rows)
    broken = [row for row in rows if row["test_name"] == "broken"]
    assert [row["mismatch_type"] for row in broken] == ["value_diff", de.EXPORT_INCOMPLETE_TYPE]
    assert "1 of 4" in broken[-1]["source_value"]
    assert len([row for row in rows if row["test_name"] == "healthy"]) == 2
    assert progress.failed_jobs == ["broken"]
    assert [job["state"] for job in progress.snapshot()["jobs"]] == ["failed", "done"]


def test_job_failing_partway_keeps_its_rows_and_adds_no_stored_duplicates(tmp_path, monkeypatch):
    def write_one_row_then_fail(df_source, df_target, key_columns, exclude_columns, options, test_name, writer):
        writer.write({
            "test_name": test_name, "key_values": {"id": 1}, "column_name": "amount",
            "source_value": "1", "target_value": "2", "mismatch_type": "value_diff",
        })
        raise RuntimeError("pair 2 could not be read")

    monkeypatch.setattr(de, "_write_tabular_differences", write_one_row_then_fail)
    with _db_module.SessionLocal() as db:
        _save_sql_job(db, "broken", *_differing_frames(1))
        run_id = _run(db, ["broken"])
        _add_result(db, run_id, "broken", issues=3, stored=3)

        rows = _recompute(db, tmp_path)

    assert [row["mismatch_type"] for row in rows] == ["value_diff", de.EXPORT_INCOMPLETE_TYPE]
    assert "pair 2 could not be read" in rows[-1]["source_value"]


def test_long_failure_reason_is_cut_to_500_characters(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("x" * 2000)

    monkeypatch.setattr(de, "_write_tabular_differences", fail)
    progress = ExportProgress()
    with _db_module.SessionLocal() as db:
        _save_sql_job(db, "broken", *_differing_frames(1))
        run_id = _run(db, ["broken"])
        _add_result(db, run_id, "broken", issues=1)

        de.write_recomputed_differences(db, _get_run(db), "json", tmp_path / "o.jsonl", progress=progress)

    assert len(progress.snapshot()["jobs"][0]["error"]) == 500


def test_progress_lists_only_jobs_needing_a_rebuild(tmp_path):
    progress = ExportProgress()
    with _db_module.SessionLocal() as db:
        _save_sql_job(db, "passed_job", *_differing_frames(1))
        _save_sql_job(db, "errored_job", *_differing_frames(1))
        _save_sql_job(db, "failed_job", *_differing_frames(3))
        run_id = _run(db, ["passed_job", "errored_job", "failed_job"])
        _add_result(db, run_id, "passed_job")
        _add_error(db, run_id, "errored_job", "boom")
        _add_result(db, run_id, "failed_job", issues=3)

        de.write_recomputed_differences(db, _get_run(db), "json", tmp_path / "o.jsonl", progress=progress)

    jobs = progress.snapshot()["jobs"]
    assert [job["name"] for job in jobs] == ["failed_job"]
    assert jobs[0]["state"] == "done"
    assert jobs[0]["rows"] == 3


def test_job_that_can_never_be_rebuilt_is_not_a_failed_job(tmp_path):
    progress = ExportProgress()
    with _db_module.SessionLocal() as db:
        _save_job(db, "api_orders", "api_reconciliation")
        run_id = _run(db, ["api_orders"])
        _add_result(db, run_id, "api_orders", issues=5, stored=2)

        de.write_recomputed_differences(db, _get_run(db), "json", tmp_path / "o.jsonl", progress=progress)

    assert progress.failed_jobs == []


def _export_run(db, run_id: str) -> str:
    job, _ = de.create_or_reuse_export_job(db, run_id, "csv")
    return job.export_id


def _finished_export(export_id: str) -> DifferenceExportJob:
    with _db_module.SessionLocal() as db:
        return db.query(DifferenceExportJob).filter_by(export_id=export_id).one()


def test_finished_export_saves_its_progress_and_failed_jobs():
    with _db_module.SessionLocal() as db:
        _save_sql_job(db, "broken", *_differing_frames(1))
        _save_sql_job(db, "healthy", *_differing_frames(2))
        del _FrameDBEngine.frames["SELECT * FROM broken_a"]
        run_id = _run(db, ["broken", "healthy"])
        _add_result(db, run_id, "broken", issues=1)
        _add_result(db, run_id, "healthy", issues=2)
        export_id = _export_run(db, run_id)

    de.run_difference_export_job(export_id)

    job = _finished_export(export_id)
    assert job.status == "COMPLETED"
    assert job.metadata_json["failed_jobs"] == ["broken"]
    assert [entry["state"] for entry in job.metadata_json["progress"]["jobs"]] == ["failed", "done"]
    assert job.metadata_json["recomputed_at"]
    assert job.heartbeat_at is not None


def test_export_that_starts_gets_a_heartbeat_at_once(monkeypatch):
    seen = {}
    original = de.write_recomputed_differences

    def spy(db, run, fmt, path, progress=None):
        with _db_module.SessionLocal() as other:
            seen["heartbeat"] = other.query(DifferenceExportJob).one().heartbeat_at
        return original(db, run, fmt, path, progress=progress)

    monkeypatch.setattr(de, "write_recomputed_differences", spy)
    with _db_module.SessionLocal() as db:
        run_id = _run(db, [])
        export_id = _export_run(db, run_id)

    de.run_difference_export_job(export_id)

    assert seen["heartbeat"] is not None


def test_export_that_raises_is_failed_and_keeps_its_progress(monkeypatch):
    def fail(db, run, fmt, path, progress=None):
        progress.set_jobs(["a"])
        raise RuntimeError("disk full")

    monkeypatch.setattr(de, "write_recomputed_differences", fail)
    with _db_module.SessionLocal() as db:
        run_id = _run(db, [])
        export_id = _export_run(db, run_id)

    de.run_difference_export_job(export_id)

    job = _finished_export(export_id)
    assert job.status == "FAILED"
    assert job.error_message == "disk full"
    assert job.metadata_json["progress"]["jobs"][0]["name"] == "a"


def test_full_html_report_says_its_rows_were_rebuilt(tmp_path):
    with _db_module.SessionLocal() as db:
        _save_sql_job(db, "curves", *_differing_frames(1))
        run_id = _run(db, ["curves"])
        _add_result(db, run_id, "curves", issues=1)

        path = tmp_path / "report.html"
        de.write_full_html_report(db, _get_run(db), path)

    html = path.read_text(encoding="utf-8")
    assert 'id="rebuilt-notice"' in html
    assert "may differ from the original run" in html


def test_report_of_stored_rows_has_no_rebuilt_notice(tmp_path):
    from api.services.run_report import build_run_report_snapshot
    from etl_framework.reporting.generator import ReportGenerator

    with _db_module.SessionLocal() as db:
        run_id = _run(db, ["curves"])
        _add_result(db, run_id, "curves", issues=1, stored=1)
        snapshot = build_run_report_snapshot(_get_run(db), include_mismatches=True)
        path = ReportGenerator(output_dir=str(tmp_path)).generate(snapshot, filename="stored.html")

    assert 'id="rebuilt-notice"' not in open(path, encoding="utf-8").read()


def _adhoc_sql_run(db, source: pd.DataFrame, target: pd.DataFrame, query_b: str = "SELECT * FROM b") -> str:
    _FrameDBEngine.frames["SELECT * FROM a"] = source
    _FrameDBEngine.frames[query_b] = target
    config_id = _config_id(db)
    RunRepository(db).create_run(
        run_id="run-1", source_env="dev", target_env="uat",
        config_snapshot={"compare_request_type": "sql", "request": {
            "config_id_a": config_id, "config_id_b": config_id,
            "query_a": "SELECT * FROM a", "query_b": query_b,
            "key_columns": ["id"],
        }},
    )
    return "run-1"


def test_adhoc_sql_compare_export_aligns_oracle_column_case(tmp_path):
    source = pd.DataFrame({"ID": [1], "AMOUNT": [1.0]})
    target = pd.DataFrame({"id": [1], "amount": [2.0]})
    with _db_module.SessionLocal() as db:
        _adhoc_sql_run(db, source, target)

        rows = _recompute(db, tmp_path)

    assert [row["mismatch_type"] for row in rows] == ["value_diff"]


def test_adhoc_sql_compare_export_refuses_a_query_that_is_not_read_only(tmp_path):
    with _db_module.SessionLocal() as db:
        _adhoc_sql_run(db, *_differing_frames(1), query_b="DELETE FROM b")

        with pytest.raises(ValueError, match="read-only"):
            _recompute(db, tmp_path)

    assert _FrameDBEngine.queries == []
