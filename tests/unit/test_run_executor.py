from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.schemas import RunSettings
from api.services.run_executor import RunExecutor
from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import JobRepository, RunRepository
from etl_framework.utils.logging import configure_logging


def _session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def test_run_executor_persists_real_reconciliation_result():
    db = _session()
    RunRepository(db).create_run("run-001", "dev", "prod", {})
    JobRepository(db).create(
        {
            "name": "orders",
            "description": "Orders",
            "tags": ["orders"],
            "job_type": "reconciliation",
            "query": "SELECT * FROM orders",
            "key_columns": ["id"],
            "exclude_columns": [],
            "source_env": None,
            "target_env": None,
            "params": {
                "source_rows": [{"id": 1, "amount": 10.0}],
                "target_rows": [{"id": 1, "amount": 9.0}],
            },
            "enabled": True,
        }
    )

    RunExecutor(
        db=db,
        run_id="run-001",
        source_env="dev",
        target_env="prod",
        job_sequence=["orders"],
        run_settings=RunSettings(metrics_enabled=False),
    ).execute()

    run = RunRepository(db).get_run("run-001")
    assert run.status == "FAILED"
    assert run.total_tests == 1
    assert run.failed == 1
    assert len(run.results) == 1
    assert run.results[0].value_mismatch_count == 1
    assert len(run.results[0].mismatches) == 1


def test_run_executor_persists_schema_policy_error():
    db = _session()
    RunRepository(db).create_run("run-002", "dev", "prod", {})
    JobRepository(db).create(
        {
            "name": "schema_check",
            "description": "Schema check",
            "tags": [],
            "job_type": "reconciliation",
            "query": "SELECT * FROM schema_check",
            "key_columns": ["id"],
            "exclude_columns": [],
            "source_env": None,
            "target_env": None,
            "params": {
                "source_rows": [{"id": 1, "amount": 10.0}],
                "target_rows": [{"id": 1}],
            },
            "enabled": True,
        }
    )

    RunExecutor(
        db=db,
        run_id="run-002",
        source_env="dev",
        target_env="prod",
        job_sequence=["schema_check"],
        run_settings=RunSettings(schema_mismatch_policy="error", metrics_enabled=False),
    ).execute()

    run = RunRepository(db).get_run("run-002")
    assert run.status == "ERROR"
    assert run.error == 1
    assert "Schema mismatch" in run.results[0].error_message


def test_run_executor_health_gate_runs_before_jobs():
    db = _session()
    RunRepository(db).create_run("run-003", "dev", "prod", {})
    JobRepository(db).create(
        {
            "name": "healthy_orders",
            "description": "Healthy orders",
            "tags": [],
            "job_type": "reconciliation",
            "query": "SELECT * FROM healthy_orders",
            "key_columns": ["id"],
            "exclude_columns": [],
            "source_env": None,
            "target_env": None,
            "params": {
                "source_rows": [{"id": 1, "amount": 10.0}],
                "target_rows": [{"id": 1, "amount": 10.0}],
            },
            "enabled": True,
        }
    )

    RunExecutor(
        db=db,
        run_id="run-003",
        source_env="dev",
        target_env="prod",
        job_sequence=["healthy_orders"],
        run_settings=RunSettings(health_check=True, metrics_enabled=False),
    ).execute()

    run = RunRepository(db).get_run("run-003")
    assert run.status == "PASSED"
    assert run.passed == 1


def test_run_executor_records_polars_backend_failure(monkeypatch):
    from etl_framework.reconciliation.backends.polars_backend import PolarsBackend

    def fail_compare(self, df_source, df_target):
        raise ImportError("polars is required")

    monkeypatch.setattr(PolarsBackend, "compare", fail_compare)
    db = _session()
    RunRepository(db).create_run("run-004", "dev", "prod", {})
    JobRepository(db).create(
        {
            "name": "polars_orders",
            "description": "Polars orders",
            "tags": [],
            "job_type": "reconciliation",
            "query": "SELECT * FROM polars_orders",
            "key_columns": ["id"],
            "exclude_columns": [],
            "source_env": None,
            "target_env": None,
            "params": {
                "source_rows": [{"id": 1, "amount": 10.0}],
                "target_rows": [{"id": 1, "amount": 10.0}],
            },
            "enabled": True,
        }
    )

    RunExecutor(
        db=db,
        run_id="run-004",
        source_env="dev",
        target_env="prod",
        job_sequence=["polars_orders"],
        run_settings=RunSettings(comparison_backend="polars", metrics_enabled=False),
    ).execute()

    run = RunRepository(db).get_run("run-004")
    assert run.status == "ERROR"
    assert "polars is required" in run.results[0].error_message


def test_run_executor_logs_include_run_id(tmp_path):
    log_file = tmp_path / "executor.log"
    configure_logging(level="INFO", log_file=str(log_file), log_format="text")
    db = _session()
    RunRepository(db).create_run("run-log-001", "dev", "prod", {})
    JobRepository(db).create(
        {
            "name": "logged_orders",
            "description": "Logged orders",
            "tags": [],
            "job_type": "reconciliation",
            "query": "SELECT * FROM logged_orders",
            "key_columns": ["id"],
            "exclude_columns": [],
            "source_env": None,
            "target_env": None,
            "params": {
                "source_rows": [{"id": 1, "amount": 10.0}],
                "target_rows": [{"id": 1, "amount": 10.0}],
            },
            "enabled": True,
        }
    )

    RunExecutor(
        db=db,
        run_id="run-log-001",
        source_env="dev",
        target_env="prod",
        job_sequence=["logged_orders"],
        run_settings=RunSettings(metrics_enabled=True),
    ).execute()

    assert "run-log-001" in log_file.read_text()


def test_run_executor_applies_chunked_reconciliation_settings():
    db = _session()
    RunRepository(db).create_run("run-005", "dev", "prod", {})
    JobRepository(db).create(
        {
            "name": "chunked_orders",
            "description": "Chunked orders",
            "tags": [],
            "job_type": "reconciliation",
            "query": "SELECT * FROM chunked_orders",
            "key_columns": ["id"],
            "exclude_columns": [],
            "source_env": None,
            "target_env": None,
            "params": {
                "source_rows": [{"id": 1, "amount": 10.0}, {"id": 2, "amount": 20.0}],
                "target_rows": [{"id": 1, "amount": 10.0}, {"id": 2, "amount": 20.0}],
            },
            "enabled": True,
        }
    )

    RunExecutor(
        db=db,
        run_id="run-005",
        source_env="dev",
        target_env="prod",
        job_sequence=["chunked_orders"],
        run_settings=RunSettings(
            chunk_size=1,
            use_hash_precheck=False,
            metrics_enabled=False,
        ),
    ).execute()

    run = RunRepository(db).get_run("run-005")
    assert run.status == "PASSED"
    assert run.results[0].source_row_count == 2
    assert run.results[0].target_row_count == 2


def test_run_executor_applies_max_duration_slo():
    db = _session()
    RunRepository(db).create_run("run-006", "dev", "prod", {})
    JobRepository(db).create(
        {
            "name": "slow_orders",
            "description": "Slow orders",
            "tags": [],
            "job_type": "reconciliation",
            "query": "SELECT * FROM slow_orders",
            "key_columns": ["id"],
            "exclude_columns": [],
            "source_env": None,
            "target_env": None,
            "params": {
                "source_rows": [{"id": 1, "amount": 10.0}],
                "target_rows": [{"id": 1, "amount": 10.0}],
            },
            "enabled": True,
        }
    )

    RunExecutor(
        db=db,
        run_id="run-006",
        source_env="dev",
        target_env="prod",
        job_sequence=["slow_orders"],
        run_settings=RunSettings(max_duration_seconds=0.000000001, metrics_enabled=False),
    ).execute()

    run = RunRepository(db).get_run("run-006")
    assert run.status == "SLOW"
    assert run.slow == 1


def _make_job(name: str, source_rows: list, target_rows: list) -> dict:
    return {
        "name": name,
        "description": name,
        "tags": [],
        "job_type": "reconciliation",
        "query": f"SELECT * FROM {name}",
        "key_columns": ["id"],
        "exclude_columns": [],
        "source_env": None,
        "target_env": None,
        "params": {"source_rows": source_rows, "target_rows": target_rows},
        "enabled": True,
    }


def test_run_executor_multiple_jobs_mixed_outcomes():
    """One passing + one failing job → overall run status FAILED with correct counts."""
    db = _session()
    RunRepository(db).create_run("run-007", "dev", "prod", {})
    JobRepository(db).create(_make_job("pass_job", [{"id": 1, "val": "a"}], [{"id": 1, "val": "a"}]))
    JobRepository(db).create(_make_job("fail_job", [{"id": 1, "val": "x"}], [{"id": 1, "val": "y"}]))

    RunExecutor(
        db=db,
        run_id="run-007",
        source_env="dev",
        target_env="prod",
        job_sequence=["pass_job", "fail_job"],
        run_settings=RunSettings(metrics_enabled=False),
    ).execute()

    run = RunRepository(db).get_run("run-007")
    assert run.status == "FAILED"
    assert run.passed == 1
    assert run.failed == 1
    assert run.total_tests == 2


def test_run_executor_empty_job_sequence_completes_as_passed():
    """A sequence that matches no known jobs finishes PASSED with 0 tests."""
    db = _session()
    RunRepository(db).create_run("run-008", "dev", "prod", {})

    RunExecutor(
        db=db,
        run_id="run-008",
        source_env="dev",
        target_env="prod",
        job_sequence=["nonexistent_job"],
        run_settings=RunSettings(metrics_enabled=False),
    ).execute()

    run = RunRepository(db).get_run("run-008")
    assert run.status == "PASSED"
    assert run.total_tests == 0


def test_run_executor_parallel_mode_completes_all_jobs():
    """Parallel execution mode must complete all jobs and produce the same results."""
    db = _session()
    RunRepository(db).create_run("run-009", "dev", "prod", {})
    JobRepository(db).create(_make_job("par_a", [{"id": 1, "val": "a"}], [{"id": 1, "val": "a"}]))
    JobRepository(db).create(_make_job("par_b", [{"id": 1, "val": "b"}], [{"id": 1, "val": "b"}]))

    RunExecutor(
        db=db,
        run_id="run-009",
        source_env="dev",
        target_env="prod",
        job_sequence=["par_a", "par_b"],
        run_settings=RunSettings(
            execution_mode="parallel",
            max_workers=2,
            metrics_enabled=False,
        ),
    ).execute()

    run = RunRepository(db).get_run("run-009")
    assert run.status == "PASSED"
    assert run.total_tests == 2
    assert run.passed == 2


def test_run_executor_error_status_takes_precedence_over_failed():
    """ERROR status on the overall run must win even when other jobs failed (not errored)."""
    db = _session()
    RunRepository(db).create_run("run-010", "dev", "prod", {})
    # A normal failing job
    JobRepository(db).create(_make_job("fail_job2", [{"id": 1, "val": "x"}], [{"id": 1, "val": "y"}]))
    # A job that will trigger schema error (→ ERROR status)
    JobRepository(db).create(
        {
            "name": "err_job",
            "description": "Error job",
            "tags": [],
            "job_type": "reconciliation",
            "query": "SELECT * FROM err_job",
            "key_columns": ["id"],
            "exclude_columns": [],
            "source_env": None,
            "target_env": None,
            "params": {
                "source_rows": [{"id": 1, "col_a": "x"}],
                "target_rows": [{"id": 1, "col_b": "x"}],
            },
            "enabled": True,
        }
    )

    RunExecutor(
        db=db,
        run_id="run-010",
        source_env="dev",
        target_env="prod",
        job_sequence=["fail_job2", "err_job"],
        run_settings=RunSettings(schema_mismatch_policy="error", metrics_enabled=False),
    ).execute()

    run = RunRepository(db).get_run("run-010")
    assert run.status == "ERROR"


# ---------------------------------------------------------------------------
# ContractBreachChecker unit tests
# ---------------------------------------------------------------------------

def _contract_session():
    import etl_framework.repository.contract_models  # noqa: F401 — register contract tables
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _make_state(name: str, status_str: str):
    from unittest.mock import MagicMock
    from etl_framework.runner.state import TestStatus
    state = MagicMock()
    state.name = name
    state.status = TestStatus[status_str]
    return state


def test_breach_checker_opens_breach_on_failed_run():
    from etl_framework.repository.contract_repository import ContractRepository
    from api.services.contract_breach_checker import ContractBreachChecker

    db = _contract_session()
    repo = ContractRepository(db)
    contract = repo.create({
        "name": "orders_v1",
        "source_job": "orders_job",
        "owner": "team@co.com",
        "sla_hours": 4.0,
        "consumers": [],
    })

    states = [_make_state("orders_job", "FAILED")]
    ContractBreachChecker().check(states, "run-breach-001", db)

    status = repo.get_status("orders_v1")
    assert status["status"] == "BREACHED"
    assert status["open_breach"]["breach_type"] == "dq_violation"


def test_breach_checker_resolves_breach_on_passed_run():
    from etl_framework.repository.contract_repository import ContractRepository
    from api.services.contract_breach_checker import ContractBreachChecker

    db = _contract_session()
    repo = ContractRepository(db)
    contract = repo.create({
        "name": "orders_v2",
        "source_job": "orders_job2",
        "owner": "team@co.com",
        "sla_hours": 4.0,
        "consumers": [],
    })
    repo.open_breach(contract.id, "run-old", "dq_violation")

    states = [_make_state("orders_job2", "PASSED")]
    ContractBreachChecker().check(states, "run-resolve-001", db)

    status = repo.get_status("orders_v2")
    assert status["status"] == "OK"


def test_breach_checker_idempotent_on_double_failure():
    from etl_framework.repository.contract_repository import ContractRepository
    from api.services.contract_breach_checker import ContractBreachChecker

    db = _contract_session()
    repo = ContractRepository(db)
    contract = repo.create({
        "name": "orders_v3",
        "source_job": "orders_job3",
        "owner": "team@co.com",
        "sla_hours": 4.0,
        "consumers": [],
    })

    states = [_make_state("orders_job3", "FAILED")]
    checker = ContractBreachChecker()
    checker.check(states, "run-x1", db)
    checker.check(states, "run-x2", db)  # second failure — should not double-open

    open_breaches = repo.list_open_breaches(contract.id)
    assert len(open_breaches) == 1


# ---------------------------------------------------------------------------
# _resolve_segment_columns / segment_summary wiring
# ---------------------------------------------------------------------------

def _make_executor(db, run_id: str = "seg-run", job_sequence=None, run_settings=None) -> RunExecutor:
    return RunExecutor(
        db=db,
        run_id=run_id,
        source_env="dev",
        target_env="prod",
        job_sequence=job_sequence or [],
        run_settings=run_settings or RunSettings(metrics_enabled=False),
    )


def test_resolve_segment_columns_manual_wins():
    from api.schemas import JobDefinition

    db = _session()
    ex = _make_executor(db)
    job = JobDefinition(
        name="j", query="SELECT 1", key_columns=["id"],
        params={"segment_columns": ["region", "day"]},
    )
    assert ex._resolve_segment_columns(job) == ["region", "day"]


def test_resolve_segment_columns_auto_from_profiles():
    from api.schemas import JobDefinition
    from etl_framework.repository.repository import ColumnProfileRepository

    db = _session()
    repo = ColumnProfileRepository(db)
    repo.save("j", None, "region", 0.0, 4, None, None, None, None, None, None, None, None)
    repo.save("j", None, "customer_id", 0.0, 90000, None, None, None, None, None, None, None, None)
    db.commit()

    ex = _make_executor(db)
    job = JobDefinition(name="j", query="SELECT 1", key_columns=["id"])
    assert ex._resolve_segment_columns(job) == ["region"]


def test_resolve_segment_columns_no_profiles_returns_empty():
    from api.schemas import JobDefinition

    db = _session()
    ex = _make_executor(db)
    job = JobDefinition(name="j", query="SELECT 1", key_columns=["id"])
    assert ex._resolve_segment_columns(job) == []


def test_run_with_segment_columns_persists_summary():
    """End-to-end in simulation mode: mismatching frames + manual segment_columns
    -> TestResult.segment_summary populated."""
    db = _session()
    RunRepository(db).create_run("seg-run-001", "dev", "prod", {})
    JobRepository(db).create(
        {
            "name": "seg_job",
            "description": "seg_job",
            "tags": [],
            "job_type": "reconciliation",
            "query": "SELECT * FROM t",
            "key_columns": ["id"],
            "exclude_columns": [],
            "source_env": None,
            "target_env": None,
            "params": {
                "segment_columns": ["region"],
                "source_rows": [{"id": 1, "region": "EMEA", "amt": 10},
                                 {"id": 2, "region": "APAC", "amt": 20}],
                "target_rows": [{"id": 1, "region": "EMEA", "amt": 10},
                                 {"id": 2, "region": "APAC", "amt": 99}],
            },
            "enabled": True,
        }
    )

    RunExecutor(
        db=db,
        run_id="seg-run-001",
        source_env="dev",
        target_env="prod",
        job_sequence=["seg_job"],
        run_settings=RunSettings(metrics_enabled=False),
    ).execute()

    run = RunRepository(db).get_run("seg-run-001")
    tr = run.results[0]
    assert tr.segment_summary is not None
    assert tr.segment_summary["region"][0]["value"] == "APAC"
    assert tr.segment_summary["region"][0]["mismatch_count"] == 1
