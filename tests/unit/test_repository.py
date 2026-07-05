import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from etl_framework.repository.database import Base
from etl_framework.repository.models import SavedConfig, TestRun, TestResult
from etl_framework.repository.repository import RunRepository, ConfigRepository


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


# --- ConfigRepository tests ---

def test_config_create(db):
    repo = ConfigRepository(db)
    cfg = repo.create(name="dev", env_name="dev", config_data={"db_host": "localhost"})
    assert cfg.id is not None
    assert cfg.name == "dev"


def test_config_get(db):
    repo = ConfigRepository(db)
    cfg = repo.create(name="qa", env_name="qa", config_data={"db_host": "qa-host"})
    fetched = repo.get(cfg.id)
    assert fetched is not None
    assert fetched.name == "qa"


def test_config_get_missing_returns_none(db):
    repo = ConfigRepository(db)
    assert repo.get(9999) is None


def test_config_list(db):
    repo = ConfigRepository(db)
    repo.create(name="env1", env_name="dev", config_data={})
    repo.create(name="env2", env_name="qa", config_data={})
    configs = repo.list()
    assert len(configs) == 2


def test_config_update(db):
    repo = ConfigRepository(db)
    cfg = repo.create(name="stage", env_name="stage", config_data={"timeout": 30})
    updated = repo.update(cfg.id, config_data={"timeout": 60})
    assert updated is not None
    assert updated.config_json["timeout"] == 60


def test_config_delete(db):
    repo = ConfigRepository(db)
    cfg = repo.create(name="tmp", env_name="dev", config_data={})
    assert repo.delete(cfg.id) is True
    assert repo.get(cfg.id) is None


def test_config_delete_missing_returns_false(db):
    repo = ConfigRepository(db)
    assert repo.delete(9999) is False


# --- RunRepository tests ---

def test_run_create(db):
    repo = RunRepository(db)
    run = repo.create_run(run_id="run-001", source_env="dev", target_env="prod")
    assert run.id is not None
    assert run.status == "PENDING"
    assert run.run_id == "run-001"


def test_run_create_with_ci_context(db):
    repo = RunRepository(db)
    ctx = {"commit_sha": "abc123", "pipeline_url": "https://gitlab.example.com/p/1", "ref": "main"}
    run = repo.create_run(run_id="run-ci-1", source_env="dev", target_env="prod", ci_context=ctx)
    assert run.ci_context == ctx


def test_run_create_without_ci_context_defaults_to_none(db):
    repo = RunRepository(db)
    run = repo.create_run(run_id="run-ci-2", source_env="dev", target_env="prod")
    assert run.ci_context is None


def test_run_get(db):
    repo = RunRepository(db)
    repo.create_run(run_id="run-002", source_env="dev", target_env="prod")
    run = repo.get_run("run-002")
    assert run is not None
    assert run.source_env == "dev"


def test_run_get_missing_returns_none(db):
    repo = RunRepository(db)
    assert repo.get_run("nonexistent") is None


def test_run_list(db):
    repo = RunRepository(db)
    repo.create_run(run_id="r1", source_env="dev", target_env="prod")
    repo.create_run(run_id="r2", source_env="qa", target_env="prod")
    runs = repo.list_runs()
    assert len(runs) == 2


def test_run_update_status(db):
    repo = RunRepository(db)
    repo.create_run(run_id="run-003", source_env="dev", target_env="prod")
    updated = repo.update_run_status("run-003", "RUNNING",
                                     started_at=datetime.now(timezone.utc))
    assert updated is not None
    assert updated.status == "RUNNING"


def test_run_add_test_result(db):
    from etl_framework.reconciliation.models import ReconciliationResult, MismatchRecord
    from etl_framework.runner.state import TestStatus
    repo = RunRepository(db)
    repo.create_run(run_id="run-004", source_env="dev", target_env="prod")
    recon_result = ReconciliationResult(
        query_name="orders", source_env="dev", target_env="prod",
        source_row_count=100, target_row_count=100,
        matched_count=99, missing_in_target_count=0, missing_in_source_count=0,
        value_mismatch_count=1,
        mismatches=[MismatchRecord(key_values={"id": 1}, column_name="amount",
                                   source_value="10.0", target_value="9.0",
                                   mismatch_type="value_diff")],
        status=TestStatus.FAILED,
        executed_at=datetime.now(timezone.utc),
        duration_seconds=1.5,
    )
    test_result = repo.add_test_result("run-004", recon_result)
    assert test_result.id is not None
    assert test_result.value_mismatch_count == 1
    # mismatches are stored via add_mismatch_details
    repo.add_mismatch_details(test_result.id, recon_result.mismatches)
    db.refresh(test_result)
    assert len(test_result.mismatches) == 1


def test_run_complete_updates_counters(db):
    repo = RunRepository(db)
    repo.create_run(run_id="run-005", source_env="dev", target_env="prod")
    repo.update_run_status("run-005", "COMPLETED",
                           completed_at=datetime.now(timezone.utc),
                           passed=3, failed=1, slow=0, error=0, total_tests=4)
    run = repo.get_run("run-005")
    assert run.total_tests == 4
    assert run.passed == 3
    assert run.failed == 1
