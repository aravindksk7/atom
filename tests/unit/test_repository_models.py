import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session
from etl_framework.repository.database import Base, get_db, init_db
from etl_framework.repository.models import SavedConfig, TestRun, TestResult, MismatchDetail


@pytest.fixture
def engine_and_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield engine, session


def test_saved_config_table_exists(engine_and_db):
    engine, db = engine_and_db
    inspector = inspect(engine)
    assert "saved_configs" in inspector.get_table_names()


def test_test_run_table_exists(engine_and_db):
    engine, db = engine_and_db
    inspector = inspect(engine)
    assert "test_runs" in inspector.get_table_names()


def test_test_result_table_exists(engine_and_db):
    engine, db = engine_and_db
    inspector = inspect(engine)
    assert "test_results" in inspector.get_table_names()


def test_mismatch_detail_table_exists(engine_and_db):
    engine, db = engine_and_db
    inspector = inspect(engine)
    assert "mismatch_details" in inspector.get_table_names()


def test_can_insert_saved_config(engine_and_db):
    engine, db = engine_and_db
    config = SavedConfig(name="dev-config", env_name="dev",
                         config_json={"db_host": "localhost", "db_port": 1433})
    db.add(config)
    db.commit()
    assert config.id is not None
    assert config.created_at is not None


def test_can_insert_test_run(engine_and_db):
    engine, db = engine_and_db
    run = TestRun(run_id="run-001", status="PENDING",
                  source_env="dev", target_env="prod")
    db.add(run)
    db.commit()
    assert run.id is not None


def test_test_result_foreign_key_to_run(engine_and_db):
    engine, db = engine_and_db
    run = TestRun(run_id="run-002", status="RUNNING", source_env="dev", target_env="prod")
    db.add(run)
    db.commit()
    result = TestResult(run_id="run-002", query_name="orders_query",
                        status="PASSED", duration_seconds=1.5,
                        source_row_count=100, target_row_count=100)
    db.add(result)
    db.commit()
    assert result.id is not None
    assert result.run_id == "run-002"


def test_mismatch_detail_foreign_key_to_result(engine_and_db):
    engine, db = engine_and_db
    run = TestRun(run_id="run-003", status="FAILED", source_env="dev", target_env="prod")
    db.add(run)
    db.commit()
    result = TestResult(run_id="run-003", query_name="sales_query",
                        status="FAILED", duration_seconds=2.0,
                        source_row_count=50, target_row_count=50,
                        value_mismatch_count=1)
    db.add(result)
    db.commit()
    detail = MismatchDetail(test_result_id=result.id,
                            key_values={"id": 42},
                            column_name="amount",
                            source_value="100.00", target_value="99.99",
                            mismatch_type="value_diff")
    db.add(detail)
    db.commit()
    assert detail.id is not None


def test_cascade_delete_run_deletes_results(engine_and_db):
    engine, db = engine_and_db
    run = TestRun(run_id="run-004", status="PASSED", source_env="dev", target_env="prod")
    db.add(run)
    db.commit()
    result = TestResult(run_id="run-004", query_name="q1", status="PASSED",
                        duration_seconds=0.5, source_row_count=10, target_row_count=10)
    db.add(result)
    db.commit()
    result_id = result.id
    db.delete(run)
    db.commit()
    assert db.get(TestResult, result_id) is None


def test_test_run_ci_context_defaults_to_none_and_round_trips(engine_and_db):
    engine, db = engine_and_db

    run = TestRun(run_id="ci-run-1", source_env="dev", target_env="prod")
    db.add(run)
    db.commit()
    db.refresh(run)
    assert run.ci_context is None

    run.ci_context = {
        "commit_sha": "a1b2c3d",
        "pipeline_url": "https://gitlab.example.com/team/proj/-/pipelines/4821",
        "ref": "main",
        "triggered_by": "gitlab-ci",
    }
    db.commit()
    db.refresh(run)
    assert run.ci_context["commit_sha"] == "a1b2c3d"


def test_init_db_creates_tables():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    from etl_framework.repository.database import Base as db_base
    db_base.metadata.create_all(engine)
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    assert "test_runs" in tables
    assert "saved_configs" in tables
