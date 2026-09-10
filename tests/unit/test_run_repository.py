from __future__ import annotations

from sqlalchemy import create_engine
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import JobSelectionRepository, RunRepository


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def test_list_runs_ci_only_false_returns_all_runs():
    db = _session()
    repo = RunRepository(db)
    repo.create_run("run-manual", "dev", "prod")
    repo.create_run("run-ci", "dev", "prod", ci_context={"commit_sha": "abc123"})

    runs = repo.list_runs(ci_only=False)

    assert len(runs) == 2
    assert {r.run_id for r in runs} == {"run-manual", "run-ci"}


def test_list_runs_ci_only_true_returns_only_ci_runs():
    db = _session()
    repo = RunRepository(db)
    repo.create_run("run-manual", "dev", "prod")
    repo.create_run("run-ci", "dev", "prod", ci_context={"commit_sha": "abc123"})

    runs = repo.list_runs(ci_only=True)

    assert len(runs) == 1
    assert runs[0].run_id == "run-ci"


def test_list_runs_ci_only_true_with_status_filter():
    db = _session()
    repo = RunRepository(db)
    repo.create_run("run-ci-pending", "dev", "prod", ci_context={"commit_sha": "abc"})
    repo.create_run("run-ci-passed", "dev", "prod", ci_context={"commit_sha": "def"})
    repo.update_run_status("run-ci-passed", "PASSED")
    repo.create_run("run-manual", "dev", "prod")

    runs = repo.list_runs(ci_only=True, status="PASSED")

    assert len(runs) == 1
    assert runs[0].run_id == "run-ci-passed"


def test_list_runs_ci_only_true_with_run_type_filter():
    db = _session()
    repo = RunRepository(db)
    repo.create_run("run-ci-recon", "dev", "prod", ci_context={"commit_sha": "abc"}, run_type="reconciliation")
    repo.create_run("run-ci-test", "dev", "prod", ci_context={"commit_sha": "def"}, run_type="test_suite")

    runs = repo.list_runs(ci_only=True, run_type="reconciliation")

    assert len(runs) == 1
    assert runs[0].run_id == "run-ci-recon"


def test_list_runs_combines_ci_status_run_type_and_pagination_filters():
    db = _session()
    repo = RunRepository(db)
    repo.create_run("matching-old", "dev", "prod", ci_context={"commit_sha": "a"})
    repo.update_run_status("matching-old", "PASSED")
    repo.create_run("wrong-status", "dev", "prod", ci_context={"commit_sha": "b"})
    repo.create_run(
        "wrong-type", "dev", "prod", ci_context={"commit_sha": "c"}, run_type="test_suite"
    )
    repo.update_run_status("wrong-type", "PASSED")
    repo.create_run("matching-new", "dev", "prod", ci_context={"commit_sha": "d"})
    repo.update_run_status("matching-new", "PASSED")
    repo.create_run("manual", "dev", "prod")
    repo.update_run_status("manual", "PASSED")

    runs = repo.list_runs(
        ci_only=True,
        status="PASSED",
        run_type="reconciliation",
        limit=1,
        offset=1,
    )

    assert [run.run_id for run in runs] == ["matching-old"]


def test_list_runs_applies_days_before_pagination():
    db = _session()
    repo = RunRepository(db)
    matching = repo.create_run("matching", "dev", "prod")
    matching.started_at = datetime.now(timezone.utc)
    for index in range(3):
        run = repo.create_run(f"old-{index}", "dev", "prod")
        run.started_at = datetime.now(timezone.utc) - timedelta(days=2)
    db.commit()

    runs = repo.list_runs(days=1, limit=1)

    assert [run.run_id for run in runs] == ["matching"]


def test_list_runs_applies_exact_target_type_before_pagination_and_requires_existing_selection():
    db = _session()
    repo = RunRepository(db)
    selection = JobSelectionRepository(db).create("selection", "", [], [], {})
    repo.create_run("matching-selection", "dev", "prod", selection_id=selection.id)
    repo.create_run("missing-selection", "dev", "prod", selection_id=999999)
    repo.create_run("blank-sequence", "dev", "prod", config_snapshot={"sequence": {"name": "   "}})
    repo.create_run("empty-sequence", "dev", "prod", config_snapshot={"sequence": {"name": ""}})
    repo.create_run("missing-name", "dev", "prod", config_snapshot={"sequence": {}})
    repo.create_run("missing-sequence", "dev", "prod", config_snapshot={})
    repo.create_run("numeric-sequence", "dev", "prod", config_snapshot={"sequence": {"name": 42}})
    repo.create_run("sequence-2", "dev", "prod", config_snapshot={"sequence": {"name": "newer"}})
    repo.create_run("sequence-1", "dev", "prod", config_snapshot={"sequence": {"name": "newest"}})

    selection_runs = repo.list_runs(target_type="selection")
    sequence_runs = repo.list_runs(target_type="sequence")
    paged_sequence_runs = repo.list_runs(target_type="sequence", limit=1, offset=1)

    assert [run.run_id for run in selection_runs] == ["matching-selection"]
    assert [run.run_id for run in sequence_runs] == ["sequence-1", "sequence-2"]
    assert [run.run_id for run in paged_sequence_runs] == ["sequence-2"]
