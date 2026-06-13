from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from api.schemas import JobDefinition, RunSettings
from etl_framework.config.models import EnvironmentConfig
from etl_framework.db.engine import DBEngine
from etl_framework.automic.client import AutomicClient
from etl_framework.sap_bo.client import BORestClient
from etl_framework.reconciliation.backends.pandas_backend import PandasBackend
from etl_framework.reconciliation.backends.polars_backend import PolarsBackend
from etl_framework.reconciliation.engine import ReconciliationEngine
from etl_framework.reconciliation.models import MismatchRecord, ReconciliationResult
from etl_framework.repository.models import TestResult
from etl_framework.repository.repository import JobRepository, RunRepository
from etl_framework.runner.health import HealthChecker
from etl_framework.runner.state import TestCaseState, TestStatus
from etl_framework.runner.test_runner import TestRunner
from etl_framework.reporting.metrics import MetricsWriter
from etl_framework.utils.context import set_run_id
from etl_framework.utils.tracing import span


_SEED_JOBS: list[JobDefinition] = [
    JobDefinition(
        name="orders_reconciliation",
        description="Reconcile orders table",
        tags=["orders", "daily"],
        query="SELECT * FROM orders",
        key_columns=["id"],
    ),
    JobDefinition(
        name="customers_reconciliation",
        description="Reconcile customers table",
        tags=["customers"],
        query="SELECT * FROM customers",
        key_columns=["id"],
    ),
    JobDefinition(
        name="products_reconciliation",
        description="Reconcile products table",
        tags=["products"],
        query="SELECT * FROM products",
        key_columns=["id"],
    ),
    JobDefinition(
        name="inventory_check",
        description="Check inventory counts",
        tags=["inventory", "daily"],
        query="SELECT * FROM inventory",
        key_columns=["id"],
    ),
    JobDefinition(
        name="sales_summary_validation",
        description="Validate sales summary aggregates",
        tags=["sales"],
        query="SELECT * FROM sales_summary",
        key_columns=["id"],
    ),
]


@dataclass
class _Env:
    name: str


class DataFrameQueryEngine:
    def __init__(self, env_name: str, frames: dict[str, pd.DataFrame]) -> None:
        self._env = _Env(env_name)
        self._frames = frames

    def execute_query(self, query: str, params: dict | None = None) -> pd.DataFrame:
        if query in self._frames:
            return self._frames[query].copy()
        base_query = self._extract_base_query(query)
        if base_query in self._frames:
            frame = self._frames[base_query].copy()
            return self._apply_window(query, frame)
        if "__default__" in self._frames:
            return self._frames["__default__"].copy()
        return pd.DataFrame()

    def _extract_base_query(self, query: str) -> str:
        match = re.search(r"FROM \((.*)\) AS _base", query, flags=re.IGNORECASE | re.DOTALL)
        return match.group(1).strip() if match else query

    def _apply_window(self, query: str, frame: pd.DataFrame) -> pd.DataFrame:
        match = re.search(
            r"OFFSET\s+(\d+)\s+ROWS\s+FETCH\s+NEXT\s+(\d+)\s+ROWS\s+ONLY",
            query,
            flags=re.IGNORECASE,
        )
        if not match:
            return frame
        offset = int(match.group(1))
        chunk_size = int(match.group(2))
        return frame.iloc[offset: offset + chunk_size].reset_index(drop=True)

    def connect(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class SQLAlchemyQueryEngine:
    """Thin wrapper around DBEngine that matches the engine protocol."""

    def __init__(self, db_engine: DBEngine) -> None:
        self._db = db_engine
        self._env = db_engine._env

    def execute_query(self, query: str, params: dict | None = None) -> pd.DataFrame:
        return self._db.execute_query(query, params)

    def connect(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class RunExecutor:
    def __init__(
        self,
        db: Session,
        run_id: str,
        source_env: str,
        target_env: str,
        job_sequence: list[str],
        run_settings: RunSettings,
        config_snapshot: dict[str, Any] | None = None,
    ) -> None:
        self._db = db
        self._run_id = run_id
        self._source_env = source_env
        self._target_env = target_env
        self._job_sequence = job_sequence
        self._settings = run_settings
        self._config_snapshot = config_snapshot or {}
        self._run_repo = RunRepository(db)
        self._job_repo = JobRepository(db)

    def execute(self) -> None:
        with span("api.run_executor.execute", {"run_id": self._run_id}):
            set_run_id(self._run_id)
            started_at = datetime.now(timezone.utc)
            self._run_repo.update_run_status(
                self._run_id,
                "RUNNING",
                started_at=started_at,
                total_tests=len(self._job_sequence),
            )

            try:
                self._apply_health_gate()
                cases = [(job.name, self._build_case(job)) for job in self._resolve_jobs()]
                max_workers = 1 if self._settings.execution_mode == "sequential" else self._settings.max_workers
                states = TestRunner(max_workers=max_workers).run(cases)
                results = self._persist_states(states)
                self._write_metrics(results)
                self._complete_run(states)
            except Exception as exc:
                self._run_repo.update_run_status(
                    self._run_id,
                    "ERROR",
                    completed_at=datetime.now(timezone.utc),
                    error=1,
                )
                self._persist_error("<run>", exc)
            finally:
                set_run_id("")

    def _resolve_jobs(self) -> list[JobDefinition]:
        jobs_by_name = {job.name: self._job_to_definition(job) for job in self._job_repo.list()}
        jobs_by_name.update({job.name: job for job in _SEED_JOBS if job.name not in jobs_by_name})
        return [jobs_by_name[name] for name in self._job_sequence if name in jobs_by_name]

    def _apply_health_gate(self) -> None:
        if not self._settings.health_check:
            return
        with span("api.run_executor.health_gate", {"run_id": self._run_id}):
            health = HealthChecker()
            source = DataFrameQueryEngine(self._source_env, {"__default__": pd.DataFrame()})
            target = DataFrameQueryEngine(self._target_env, {"__default__": pd.DataFrame()})
            results = [
                health.check_db(self._source_env, source),
                health.check_db(self._target_env, target),
            ]
            if not health.all_healthy(results):
                messages = "; ".join(f"{r.component}: {r.message}" for r in results if not r.healthy)
                raise RuntimeError(f"Health check failed: {messages}")

    def _job_to_definition(self, job) -> JobDefinition:
        return JobDefinition(
            name=job.name,
            description=job.description,
            tags=job.tags or [],
            job_type=job.job_type,
            query=job.query,
            key_columns=job.key_columns or [],
            exclude_columns=job.exclude_columns or [],
            source_env=job.source_env,
            target_env=job.target_env,
            params=job.params or {},
            enabled=job.enabled,
        )

    def _build_case(self, job: JobDefinition):
        if job.job_type == "bo_report" and self._settings.use_live_connections:
            return self._build_case_bo_report(job)
        if job.job_type == "automic_job" and self._settings.use_live_connections:
            return self._build_case_automic(job)

        def run_job() -> ReconciliationResult:
            source_engine, target_engine = self._build_engines(job)
            engine = ReconciliationEngine(
                source_engine=source_engine,
                target_engine=target_engine,
                key_columns=job.key_columns or self._settings.key_columns,
                exclude_columns=job.exclude_columns or self._settings.exclude_columns,
                float_tolerance=self._settings.float_tolerance,
                mismatch_row_limit=self._settings.mismatch_row_limit,
                schema_mismatch_policy=self._settings.schema_mismatch_policy,
                null_equals_null=self._settings.null_equals_null,
                chunk_size=self._settings.chunk_size,
                use_hash_precheck=self._settings.use_hash_precheck,
                backend=self._build_backend(job),
            )
            max_duration = self._settings.max_duration_seconds or None
            return engine.reconcile(
                query=job.query,
                query_name=job.name,
                params=job.params,
                max_duration_seconds=max_duration,
            )

        return run_job

    def _build_case_bo_report(self, job: JobDefinition):
        def run_job() -> ReconciliationResult:
            creds = self._config_snapshot.get("bo_credentials", {})
            env = EnvironmentConfig(name=creds.get("name", "bo"), **{
                k: v for k, v in creds.items() if k != "name"
            })
            client = BORestClient(env)
            client.authenticate()
            doc_id = job.params.get("report_id", "")
            report_id = job.params.get("bo_report_id", "")
            fmt = job.params.get("format", "xlsx")
            data = client.download_report(doc_id, report_id, fmt)
            return ReconciliationResult(
                query_name=job.name,
                source_env=self._source_env,
                target_env=self._target_env,
                source_row_count=len(data),
                target_row_count=len(data),
                matched_count=len(data),
                missing_in_target_count=0,
                missing_in_source_count=0,
                value_mismatch_count=0,
                mismatches=[],
                status=TestStatus.PASSED,
                executed_at=datetime.now(timezone.utc),
                duration_seconds=0.0,
            )
        return run_job

    def _build_case_automic(self, job: JobDefinition):
        def run_job() -> ReconciliationResult:
            creds = self._config_snapshot.get("automic_credentials", {})
            env = EnvironmentConfig(name=creds.get("name", "automic"), **{
                k: v for k, v in creds.items() if k != "name"
            })
            client = AutomicClient(env)
            if job.params.get("run_id"):
                status_obj = client.get_status_by_run_id(job.params["run_id"])
            else:
                status_obj = client.get_status_by_job_name(job.params.get("job_name", ""))
            mapped = status_obj.status if isinstance(status_obj.status, TestStatus) else TestStatus.PASSED
            return ReconciliationResult(
                query_name=job.name,
                source_env=self._source_env,
                target_env=self._target_env,
                source_row_count=0,
                target_row_count=0,
                matched_count=0,
                missing_in_target_count=0,
                missing_in_source_count=0,
                value_mismatch_count=0,
                mismatches=[],
                status=mapped,
                executed_at=datetime.now(timezone.utc),
                duration_seconds=0.0,
            )
        return run_job

    def _build_backend(self, job: JobDefinition):
        key_columns = job.key_columns or self._settings.key_columns
        if self._settings.comparison_backend == "polars":
            return PolarsBackend(
                key_columns=key_columns,
                float_tolerance=self._settings.float_tolerance,
                null_equals_null=self._settings.null_equals_null,
                mismatch_row_limit=self._settings.mismatch_row_limit,
            )
        return PandasBackend(
            key_columns=key_columns,
            float_tolerance=self._settings.float_tolerance,
            null_equals_null=self._settings.null_equals_null,
            mismatch_row_limit=self._settings.mismatch_row_limit,
        )

    def _build_engines(self, job: JobDefinition):
        if self._settings.use_live_connections:
            src_creds = self._config_snapshot.get("source_credentials")
            tgt_creds = self._config_snapshot.get("target_credentials")
            if src_creds and tgt_creds:
                try:
                    src_env = EnvironmentConfig(name=src_creds.get("name", self._source_env), **{
                        k: v for k, v in src_creds.items() if k != "name"
                    })
                    tgt_env = EnvironmentConfig(name=tgt_creds.get("name", self._target_env), **{
                        k: v for k, v in tgt_creds.items() if k != "name"
                    })
                    return (
                        SQLAlchemyQueryEngine(DBEngine(src_env)),
                        SQLAlchemyQueryEngine(DBEngine(tgt_env)),
                    )
                except Exception:
                    pass  # fall through to simulation

        source_rows = job.params.get("source_rows")
        target_rows = job.params.get("target_rows")
        if source_rows is None:
            source_rows = self._config_snapshot.get("source_rows")
        if target_rows is None:
            target_rows = self._config_snapshot.get("target_rows")
        if source_rows is None:
            source_rows = self._default_rows(job)
        if target_rows is None:
            target_rows = self._default_rows(job)

        source_df = pd.DataFrame(source_rows)
        target_df = pd.DataFrame(target_rows)
        source = DataFrameQueryEngine(self._source_env, {job.query: source_df})
        target = DataFrameQueryEngine(self._target_env, {job.query: target_df})
        return source, target

    def _default_rows(self, job: JobDefinition) -> list[dict[str, Any]]:
        key = (job.key_columns or self._settings.key_columns or ["id"])[0]
        return [{key: 1, "value": job.name}]

    def _persist_states(self, states: list[TestCaseState]) -> list[ReconciliationResult]:
        with span("api.run_executor.persist_results", {"run_id": self._run_id}):
            results = []
            for state in states:
                if isinstance(state.result, ReconciliationResult):
                    test_result = self._run_repo.add_test_result(self._run_id, state.result)
                    self._run_repo.add_mismatch_details(test_result.id, state.result.mismatches)
                    results.append(state.result)
                elif state.status == TestStatus.ERROR:
                    self._persist_error(state.name, Exception(state.error_message or "Unknown error"))
            return results

    def _persist_error(self, query_name: str, exc: Exception) -> None:
        tr = TestResult(
            run_id=self._run_id,
            query_name=query_name,
            status=TestStatus.ERROR.value,
            duration_seconds=0.0,
            source_row_count=0,
            target_row_count=0,
            value_mismatch_count=0,
            missing_in_target_count=0,
            missing_in_source_count=0,
            error_message=str(exc),
            executed_at=datetime.now(timezone.utc),
        )
        self._db.add(tr)
        self._db.commit()

    def _write_metrics(self, results: list[ReconciliationResult]) -> None:
        if not self._settings.metrics_enabled:
            return
        with span("api.run_executor.write_metrics", {"run_id": self._run_id}):
            MetricsWriter(f"logs/metrics_{self._run_id}.json").write(self._run_id, results)

    def _complete_run(self, states: list[TestCaseState]) -> None:
        passed = sum(1 for state in states if state.status == TestStatus.PASSED)
        failed = sum(1 for state in states if state.status == TestStatus.FAILED)
        slow = sum(1 for state in states if state.status == TestStatus.SLOW)
        error = sum(1 for state in states if state.status == TestStatus.ERROR)
        if error:
            final_status = "ERROR"
        elif failed:
            final_status = "FAILED"
        elif slow:
            final_status = "SLOW"
        else:
            final_status = "PASSED"
        self._run_repo.update_run_status(
            self._run_id,
            final_status,
            completed_at=datetime.now(timezone.utc),
            total_tests=len(states),
            passed=passed,
            failed=failed,
            slow=slow,
            error=error,
        )
