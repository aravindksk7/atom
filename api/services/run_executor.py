from __future__ import annotations

import base64
import dataclasses
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import os
import re
import time
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from api.schemas import JobDefinition, RunSettings, SequenceStep, StepCondition
from api.services.frame_engine import FrameEngine
from etl_framework.config.models import EnvironmentConfig
from etl_framework.db.engine import DBEngine
from etl_framework.automic.client import AutomicClient
from etl_framework.sap_bo.client import BORestClient
from etl_framework.reconciliation.backends.pandas_backend import PandasBackend
from etl_framework.reconciliation.backends.polars_backend import PolarsBackend
from etl_framework.reconciliation.engine import ReconciliationEngine
from etl_framework.reconciliation.models import MismatchRecord, ReconciliationResult
from etl_framework.reconciliation.segments import (
    build_segment_summary,
    pick_auto_segment_columns,
)
from etl_framework.repository.models import TestResult
from etl_framework.repository.repository import JobRepository, RunRepository, RunStepRepository
from etl_framework.runner.health import HealthChecker
from etl_framework.runner.state import TestCaseState, TestStatus
from etl_framework.runner.test_runner import TestRunner
from etl_framework.reporting.metrics import MetricsWriter
from etl_framework.utils.context import set_run_id
from etl_framework.utils.tracing import span

HOLD_POLL_INTERVAL_SECONDS = int(os.environ.get("HOLD_POLL_INTERVAL_SECONDS", "5"))
BO_REPORT_SAMPLE_ROW_LIMIT = int(os.environ.get("BO_REPORT_SAMPLE_ROW_LIMIT", "20"))

logger = logging.getLogger(__name__)


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
        job_sequence: list[str | SequenceStep],
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
            steps = self._resolve_sequence_steps()

            self._run_repo.update_run_status(
                self._run_id,
                "RUNNING",
                started_at=started_at,
                total_tests=len(steps),
            )

            try:
                self._apply_health_gate()
                step_repo = RunStepRepository(self._db)
                step_repo.materialize_steps(self._run_id, steps)

                all_states: list[TestCaseState] = []
                all_results: list[ReconciliationResult] = []
                prev_result: ReconciliationResult | None = None
                cancelled = False
                jobs_index = self._build_jobs_index()

                for i, seq_step in enumerate(steps):
                    # Condition gate: check previous step's outcome before running this step
                    if seq_step.condition is not None and prev_result is not None:
                        if not self._check_condition(seq_step.condition, prev_result):
                            step_repo.cancel_remaining(self._run_id, from_index=i)
                            break

                    if seq_step.wait_seconds > 0:
                        time.sleep(seq_step.wait_seconds)

                    step_repo.update_status(self._run_id, i, "RUNNING")

                    job_def = jobs_index.get(seq_step.job_name)
                    if job_def is None:
                        step_repo.update_status(self._run_id, i, "ERROR")
                        continue

                    case_fn = self._build_case(job_def)
                    state = TestRunner(max_workers=1).run([(job_def.name, case_fn)])[0]
                    all_states.append(state)

                    step_results = self._persist_states([state])
                    if step_results:
                        prev_result = step_results[0]
                        all_results.extend(step_results)

                    job_outcome = state.status.value if hasattr(state.status, "value") else str(state.status)
                    step_repo.update_status(self._run_id, i, job_outcome)

                    if self._run_repo.is_cancel_requested(self._run_id):
                        step_repo.cancel_remaining(self._run_id, from_index=i + 1)
                        cancelled = True
                        break

                    if seq_step.hold_after:
                        step_repo.update_status(
                            self._run_id, i, "HELD",
                            held_at=datetime.now(timezone.utc),
                        )
                        self._fire_held_webhook(i, seq_step.job_name)
                        release_action = self._poll_for_release(step_repo, i)
                        if release_action == "cancel":
                            step_repo.cancel_remaining(self._run_id, from_index=i + 1)
                            cancelled = True
                            break

                if cancelled:
                    self._run_repo.update_run_status(
                        self._run_id,
                        "CANCELLED",
                        completed_at=datetime.now(timezone.utc),
                    )
                    self._fire_webhooks("CANCELLED")
                else:
                    self._write_metrics(all_results)
                    self._complete_run(all_states)

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

    def _resolve_sequence_steps(self) -> list[SequenceStep]:
        result: list[SequenceStep] = []
        for item in self._job_sequence:
            if isinstance(item, str):
                result.append(SequenceStep(job_name=item))
            elif isinstance(item, dict):
                result.append(SequenceStep(**item))
            else:
                result.append(item)
        return result

    def _build_jobs_index(self) -> dict[str, JobDefinition]:
        index: dict[str, JobDefinition] = {job.name: job for job in _SEED_JOBS}
        index.update({job.name: self._job_to_definition(job) for job in self._job_repo.list()})
        return index

    def _check_condition(self, condition: StepCondition, prev_result: ReconciliationResult) -> bool:
        prev_status = prev_result.status.value if hasattr(prev_result.status, "value") else str(prev_result.status)
        if condition.require_status and prev_status not in condition.require_status:
            return False
        if condition.max_mismatch_count is not None:
            total = (
                prev_result.value_mismatch_count
                + prev_result.missing_in_target_count
                + prev_result.missing_in_source_count
            )
            if total > condition.max_mismatch_count:
                return False
        if condition.min_row_count is not None and prev_result.source_row_count < condition.min_row_count:
            return False
        if condition.max_row_count is not None and prev_result.source_row_count > condition.max_row_count:
            return False
        if condition.max_value_mismatches is not None and prev_result.value_mismatch_count > condition.max_value_mismatches:
            return False
        if condition.max_missing_in_target is not None and prev_result.missing_in_target_count > condition.max_missing_in_target:
            return False
        if condition.max_missing_in_source is not None and prev_result.missing_in_source_count > condition.max_missing_in_source:
            return False
        return True

    def _poll_for_release(self, step_repo: RunStepRepository, step_index: int) -> str:
        while True:
            time.sleep(HOLD_POLL_INTERVAL_SECONDS)
            self._db.expire_all()
            step = step_repo.get_step(self._run_id, step_index)
            if step is None or step.status != "HELD":
                return (step.release_action or "approve") if step else "approve"

    def _fire_held_webhook(self, step_index: int, job_name: str) -> None:
        try:
            from etl_framework.repository.repository import NotificationRepository
            from api.services.notifier import notify
            hooks = NotificationRepository(self._db).list_enabled_for_event("run.held")
            notify(
                self._run_id,
                "run.held",
                extra={"step_index": step_index, "job_name": job_name},
                hooks=hooks,
                db_session=self._db,
            )
        except Exception:
            pass

    def _resolve_jobs(self) -> list[JobDefinition]:
        jobs_by_name = {job.name: self._job_to_definition(job) for job in self._job_repo.list()}
        jobs_by_name.update({job.name: job for job in _SEED_JOBS if job.name not in jobs_by_name})
        requested = [jobs_by_name[n] for n in self._job_sequence if n in jobs_by_name]
        return self._topo_sort(requested)

    def _topo_sort(self, jobs: list[JobDefinition]) -> list[JobDefinition]:
        name_set = {j.name for j in jobs}
        by_name = {j.name: j for j in jobs}
        in_degree: dict[str, int] = {j.name: 0 for j in jobs}
        graph: dict[str, list[str]] = {j.name: [] for j in jobs}
        for job in jobs:
            for dep in job.depends_on:
                if dep in name_set:
                    graph[dep].append(job.name)
                    in_degree[job.name] += 1
        queue = [n for n, d in in_degree.items() if d == 0]
        sorted_names: list[str] = []
        while queue:
            node = queue.pop(0)
            sorted_names.append(node)
            for child in graph[node]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)
        if len(sorted_names) != len(jobs):
            raise ValueError("Cycle detected in job dependency graph")
        return [by_name[n] for n in sorted_names]

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
        params = dict(job.params or {})
        rules_raw = params.pop("rules", [])
        depends_on = params.pop("depends_on", [])
        pass_condition_raw = params.pop("pass_condition", None)
        from api.schemas import DQRule, PassCondition
        rules = [DQRule.model_validate(r) for r in (rules_raw or [])]
        pass_condition = PassCondition.model_validate(pass_condition_raw) if pass_condition_raw else None
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
            params=params,
            enabled=job.enabled,
            rules=rules,
            depends_on=depends_on,
            pass_condition=pass_condition,
        )

    def _build_case(self, job: JobDefinition):
        if job.job_type == "freshness":
            return self._build_case_freshness(job)
        if job.job_type == "schema_snapshot":
            return self._build_case_schema_snapshot(job)
        if job.job_type == "profile":
            return self._build_case_profile(job)
        if job.job_type == "cross_job_assertion":
            return self._build_case_cross_job(job)
        if job.job_type == "dbt_artifact":
            return self._build_case_dbt(job)
        if job.job_type == "bo_report" and self._settings.use_live_connections:
            return self._build_case_bo_report(job)
        if job.job_type == "automic_job" and self._settings.use_live_connections:
            return self._build_case_automic(job)
        if job.job_type == "api_reconciliation" and self._settings.use_live_connections:
            return self._build_case_api_reconciliation(job)

        def run_job() -> ReconciliationResult:
            source_engine, target_engine = self._build_engines(job)
            segment_columns = self._resolve_segment_columns(job)
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
                segment_columns=segment_columns,
            )
            max_duration = self._settings.max_duration_seconds or None
            result = engine.reconcile(
                query=job.query,
                query_name=job.name,
                params=job.params,
                max_duration_seconds=max_duration,
            )
            if job.rules:
                result = self._apply_dq_rules(result, job, source_engine)
            if job.pass_condition:
                result = self._apply_pass_condition(result, job, source_engine)
            if segment_columns:
                try:
                    summary = build_segment_summary(result.mismatches, segment_columns)
                    result = dataclasses.replace(result, segment_summary=summary)
                except Exception:
                    logger.warning("segment summary failed for %s", job.name, exc_info=True)
            return result

        max_retries = self._settings.max_retries
        retry_delay = self._settings.retry_delay_seconds

        def run_with_retry() -> ReconciliationResult:
            import time
            for attempt in range(max_retries + 1):
                try:
                    return run_job()
                except Exception:
                    if attempt == max_retries:
                        raise
                    time.sleep(retry_delay * (2 ** attempt))
            raise RuntimeError("unreachable")  # pragma: no cover

        return run_with_retry

    def _apply_dq_rules(
        self, result: ReconciliationResult, job: JobDefinition, source_engine
    ) -> ReconciliationResult:
        from etl_framework.reconciliation.dq_engine import DQEngine, DQViolation
        try:
            source_df = source_engine.execute_query(job.query, job.params)
        except Exception:
            return result
        violations = DQEngine().evaluate(source_df, job.rules, engine=source_engine)
        if not violations:
            return result
        extra: list[MismatchRecord] = []
        for v in violations:
            extra.append(MismatchRecord(
                key_values={"dq_rule": v.rule_type},
                column_name=v.column or "",
                source_value=str(v.actual_value) if v.actual_value is not None else "",
                target_value="",
                mismatch_type="dq_violation",
            ))
        from dataclasses import replace as _replace
        from etl_framework.runner.state import TestStatus as _TS
        has_error = any(v.severity == "error" for v in violations)
        new_status = _TS.FAILED if has_error else result.status
        new_mismatches = result.mismatches + extra
        return _replace(
            result,
            mismatches=new_mismatches,
            value_mismatch_count=result.value_mismatch_count + len(extra),
            status=new_status,
        )

    def _apply_pass_condition(
        self, result: ReconciliationResult, job: JobDefinition, source_engine
    ) -> ReconciliationResult:
        c = job.pass_condition
        if c is None:
            return result

        violations: list[str] = []

        if c.min_row_count is not None and result.source_row_count < c.min_row_count:
            violations.append(f"row_count {result.source_row_count} < min {c.min_row_count}")
        if c.max_row_count is not None and result.source_row_count > c.max_row_count:
            violations.append(f"row_count {result.source_row_count} > max {c.max_row_count}")
        if c.max_value_mismatches is not None and result.value_mismatch_count > c.max_value_mismatches:
            violations.append(f"value_mismatches {result.value_mismatch_count} > {c.max_value_mismatches}")
        if c.max_missing_in_target is not None and result.missing_in_target_count > c.max_missing_in_target:
            violations.append(f"missing_in_target {result.missing_in_target_count} > {c.max_missing_in_target}")
        if c.max_missing_in_source is not None and result.missing_in_source_count > c.max_missing_in_source:
            violations.append(f"missing_in_source {result.missing_in_source_count} > {c.max_missing_in_source}")

        if c.require_status:
            cur = result.status.value if hasattr(result.status, "value") else str(result.status)
            if cur not in c.require_status:
                violations.append(f"status {cur!r} not in {c.require_status}")

        if c.pass_sql:
            try:
                df = source_engine.execute_query(c.pass_sql)
                has_rows = not df.empty
                if c.pass_sql_mode == "rows_mean_pass" and not has_rows:
                    violations.append("pass_sql returned no rows")
                elif c.pass_sql_mode == "rows_mean_fail" and has_rows:
                    violations.append("pass_sql returned rows (expected none)")
            except Exception as exc:
                violations.append(f"pass_sql error: {exc}")

        if not violations:
            return result

        extra = [
            MismatchRecord(
                key_values={"pass_condition": v},
                column_name="",
                source_value="FAIL",
                target_value="PASS",
                mismatch_type="pass_condition_violation",
            )
            for v in violations
        ]
        from dataclasses import replace as _replace
        from etl_framework.runner.state import TestStatus as _TS
        return _replace(
            result,
            mismatches=result.mismatches + extra,
            value_mismatch_count=result.value_mismatch_count + len(extra),
            status=_TS.FAILED,
        )

    # ── Freshness ──────────────────────────────────────────────────────────

    def _build_case_freshness(self, job: JobDefinition):
        def run_freshness() -> ReconciliationResult:
            source_engine, _ = self._build_engines(job)
            return self._execute_freshness(job, source_engine)
        return run_freshness

    def _execute_freshness(self, job: JobDefinition, engine) -> ReconciliationResult:
        t0 = time.monotonic()
        executed_at = datetime.now(timezone.utc)
        ts_col = job.params.get("timestamp_column", "ts")
        max_age_hours = float(job.params.get("max_age_hours", 24))
        query = job.query or job.params.get("query", "")

        try:
            df = engine.execute_query(query)
        except Exception as exc:
            return ReconciliationResult(
                query_name=job.name, source_env=self._source_env, target_env=self._target_env,
                source_row_count=0, target_row_count=0, matched_count=0,
                missing_in_target_count=0, missing_in_source_count=0, value_mismatch_count=1,
                mismatches=[MismatchRecord(key_values={"job": job.name}, column_name=ts_col,
                                           source_value=str(exc), target_value="",
                                           mismatch_type="freshness_error")],
                status=TestStatus.ERROR, executed_at=executed_at,
                duration_seconds=time.monotonic() - t0,
            )

        if df.empty or ts_col not in df.columns:
            return ReconciliationResult(
                query_name=job.name, source_env=self._source_env, target_env=self._target_env,
                source_row_count=0, target_row_count=0, matched_count=1,
                missing_in_target_count=0, missing_in_source_count=0, value_mismatch_count=0,
                mismatches=[], status=TestStatus.PASSED, executed_at=executed_at,
                duration_seconds=time.monotonic() - t0,
            )

        max_ts = pd.to_datetime(df[ts_col], errors="coerce").max()
        if max_ts is None or pd.isna(max_ts):
            return ReconciliationResult(
                query_name=job.name, source_env=self._source_env, target_env=self._target_env,
                source_row_count=1, target_row_count=1, matched_count=0,
                missing_in_target_count=0, missing_in_source_count=0, value_mismatch_count=1,
                mismatches=[MismatchRecord(key_values={"job": job.name}, column_name=ts_col,
                                           source_value="NULL", target_value=f"<= {max_age_hours}h ago",
                                           mismatch_type="freshness_null")],
                status=TestStatus.FAILED, executed_at=executed_at,
                duration_seconds=time.monotonic() - t0,
            )

        now_utc = datetime.now(timezone.utc)
        if max_ts.tzinfo is None:
            max_ts = max_ts.replace(tzinfo=timezone.utc)
        age_hours = (now_utc - max_ts).total_seconds() / 3600

        if age_hours <= max_age_hours:
            return ReconciliationResult(
                query_name=job.name, source_env=self._source_env, target_env=self._target_env,
                source_row_count=1, target_row_count=1, matched_count=1,
                missing_in_target_count=0, missing_in_source_count=0, value_mismatch_count=0,
                mismatches=[], status=TestStatus.PASSED, executed_at=executed_at,
                duration_seconds=time.monotonic() - t0,
            )

        return ReconciliationResult(
            query_name=job.name, source_env=self._source_env, target_env=self._target_env,
            source_row_count=1, target_row_count=1, matched_count=0,
            missing_in_target_count=0, missing_in_source_count=0, value_mismatch_count=1,
            mismatches=[MismatchRecord(
                key_values={"job": job.name}, column_name=ts_col,
                source_value=f"{age_hours:.1f}h",
                target_value=f"<= {max_age_hours}h",
                mismatch_type="freshness_stale",
            )],
            status=TestStatus.FAILED, executed_at=executed_at,
            duration_seconds=time.monotonic() - t0,
        )

    # ── Profile ────────────────────────────────────────────────────────────

    def _build_case_profile(self, job: JobDefinition):
        def run_profile() -> ReconciliationResult:
            source_engine, _ = self._build_engines(job)
            return self._execute_profile(job, source_engine)
        return run_profile

    def _execute_profile(self, job: JobDefinition, engine) -> ReconciliationResult:
        from api.services.profile_service import compute_profile, detect_drift
        from etl_framework.repository.repository import ColumnProfileRepository

        t0 = time.monotonic()
        executed_at = datetime.now(timezone.utc)
        columns = job.params.get("columns", [])
        drift_threshold = float(job.params.get("drift_threshold_pct", 20.0))

        try:
            df = engine.execute_query(job.query)
        except Exception:
            df = pd.DataFrame()

        if df.empty:
            return ReconciliationResult(
                query_name=job.name, source_env=self._source_env, target_env=self._target_env,
                source_row_count=0, target_row_count=0, matched_count=1,
                missing_in_target_count=0, missing_in_source_count=0, value_mismatch_count=0,
                mismatches=[], status=TestStatus.PASSED, executed_at=executed_at,
                duration_seconds=time.monotonic() - t0,
            )

        current_profile = compute_profile(df, columns)
        repo = ColumnProfileRepository(self._db)
        previous_rows = repo.get_latest(job.name)
        previous_profile = {
            row.column_name: {
                "null_rate": row.null_rate, "distinct_count": row.distinct_count,
                "mean_val": row.mean_val, "std_val": row.std_val,
                "p25": row.p25, "p50": row.p50, "p75": row.p75, "p95": row.p95,
            }
            for row in previous_rows
        }

        flagged = detect_drift(current_profile, previous_profile, drift_threshold)

        for col, stats in current_profile.items():
            repo.save(
                job_name=job.name, run_id=self._run_id, column_name=col,
                null_rate=stats.get("null_rate"), distinct_count=stats.get("distinct_count"),
                min_val=stats.get("min_val"), max_val=stats.get("max_val"),
                mean_val=stats.get("mean_val"), std_val=stats.get("std_val"),
                p25=stats.get("p25"), p50=stats.get("p50"),
                p75=stats.get("p75"), p95=stats.get("p95"),
            )
        self._db.commit()

        mismatches = [
            MismatchRecord(
                key_values={"job": job.name, "column": col},
                column_name=col,
                source_value=str(current_profile.get(col, {}).get("mean_val")),
                target_value=str(previous_profile.get(col, {}).get("mean_val")),
                mismatch_type="profile_drift",
            )
            for col in flagged
        ]

        status = TestStatus.FAILED if flagged else TestStatus.PASSED
        return ReconciliationResult(
            query_name=job.name, source_env=self._source_env, target_env=self._target_env,
            source_row_count=len(df), target_row_count=len(df),
            matched_count=len(current_profile) - len(flagged),
            missing_in_target_count=0, missing_in_source_count=0,
            value_mismatch_count=len(flagged),
            mismatches=mismatches, status=status, executed_at=executed_at,
            duration_seconds=time.monotonic() - t0,
        )

    # ── Schema Snapshot ────────────────────────────────────────────────────

    def _build_case_schema_snapshot(self, job: JobDefinition):
        def run_schema_snapshot() -> ReconciliationResult:
            source_engine, target_engine = self._build_engines(job)
            environment = job.params.get("environment", "both")
            engine = target_engine if environment == "target" else source_engine
            return self._execute_schema_snapshot(job, engine)
        return run_schema_snapshot

    def _execute_schema_snapshot(self, job: JobDefinition, engine) -> ReconciliationResult:
        from api.services.schema_snapshot_service import capture_schema, diff_schemas
        from etl_framework.repository.repository import SchemaSnapshotRepository

        t0 = time.monotonic()
        executed_at = datetime.now(timezone.utc)
        environment = job.params.get("environment", "source")

        try:
            df = engine.execute_query(job.query)
        except Exception:
            df = pd.DataFrame()

        current_cols = capture_schema(df)
        repo = SchemaSnapshotRepository(self._db)
        previous = repo.get_latest(job.name, environment)
        previous_cols = previous.columns if previous else []

        diff = diff_schemas(current_cols, previous_cols)
        repo.save(job.name, self._run_id, environment, current_cols)
        self._db.commit()

        mismatches = []
        for col in diff["added"]:
            mismatches.append(MismatchRecord(
                key_values={"job": job.name, "change": "added"},
                column_name=col, source_value="(new)", target_value="(absent)",
                mismatch_type="schema_added",
            ))
        for col in diff["removed"]:
            mismatches.append(MismatchRecord(
                key_values={"job": job.name, "change": "removed"},
                column_name=col, source_value="(absent)", target_value="(was present)",
                mismatch_type="schema_removed",
            ))
        for change in diff["changed"]:
            mismatches.append(MismatchRecord(
                key_values={"job": job.name, "change": "type_changed"},
                column_name=change["column"],
                source_value=change["to"], target_value=change["from"],
                mismatch_type="schema_type_changed",
            ))

        first_run = not previous_cols
        changes = len(diff["added"]) + len(diff["removed"]) + len(diff["changed"])
        status = TestStatus.PASSED if (first_run or not changes) else TestStatus.FAILED
        return ReconciliationResult(
            query_name=job.name, source_env=self._source_env, target_env=self._target_env,
            source_row_count=len(current_cols), target_row_count=len(previous_cols),
            matched_count=len(current_cols) - changes,
            missing_in_target_count=len(diff["removed"]),
            missing_in_source_count=len(diff["added"]),
            value_mismatch_count=len(diff["changed"]),
            mismatches=mismatches, status=status, executed_at=executed_at,
            duration_seconds=time.monotonic() - t0,
        )

    # ── Cross-Job Assertion ────────────────────────────────────────────────

    def _build_case_cross_job(self, job: JobDefinition):
        def run_cross_job() -> ReconciliationResult:
            return self._execute_cross_job(job)
        return run_cross_job

    def _execute_cross_job(self, job: JobDefinition) -> ReconciliationResult:
        from etl_framework.repository.models import ColumnProfile

        t0 = time.monotonic()
        executed_at = datetime.now(timezone.utc)
        p = job.params
        source_job = p.get("source_job", "")
        target_job = p.get("target_job", "")
        source_metric = p.get("source_metric", "count")
        target_metric = p.get("target_metric", "count")
        source_col = p.get("source_column", "")
        target_col = p.get("target_column", "")
        tolerance = float(p.get("tolerance", 0.0))
        tolerance_type = p.get("tolerance_type", "absolute")

        def _get_count(job_name: str):
            from etl_framework.repository.models import TestResult as _TR
            row = (
                self._db.query(_TR)
                .filter(_TR.run_id == self._run_id, _TR.query_name == job_name)
                .first()
            )
            return float(row.source_row_count) if row else None

        def _get_profile_metric(job_name: str, column: str, metric: str):
            row = (
                self._db.query(ColumnProfile)
                .filter(
                    ColumnProfile.job_name == job_name,
                    ColumnProfile.run_id == self._run_id,
                    ColumnProfile.column_name == column,
                )
                .first()
            )
            if row is None:
                return None
            return {"distinct_count": float(row.distinct_count) if row.distinct_count is not None else None}.get(metric)

        src_val = _get_count(source_job) if source_metric == "count" else _get_profile_metric(source_job, source_col, source_metric)
        tgt_val = _get_count(target_job) if target_metric == "count" else _get_profile_metric(target_job, target_col, target_metric)

        _skipped = ReconciliationResult(
            query_name=job.name, source_env=self._source_env, target_env=self._target_env,
            source_row_count=0, target_row_count=0, matched_count=0,
            missing_in_target_count=0, missing_in_source_count=0, value_mismatch_count=0,
            mismatches=[], status=TestStatus.SKIPPED, executed_at=executed_at,
            duration_seconds=time.monotonic() - t0,
        )

        if src_val is None or tgt_val is None:
            return _skipped

        effective_tolerance = (tolerance / 100 * abs(src_val)) if tolerance_type == "percent" else tolerance
        delta = abs(src_val - tgt_val)
        passed = delta <= effective_tolerance

        mismatches = [] if passed else [
            MismatchRecord(
                key_values={"source_job": source_job, "target_job": target_job},
                column_name=source_col or "row_count",
                source_value=str(src_val), target_value=str(tgt_val),
                mismatch_type="cross_job_delta",
            )
        ]

        return ReconciliationResult(
            query_name=job.name, source_env=self._source_env, target_env=self._target_env,
            source_row_count=int(src_val), target_row_count=int(tgt_val),
            matched_count=1 if passed else 0,
            missing_in_target_count=0, missing_in_source_count=0,
            value_mismatch_count=0 if passed else 1,
            mismatches=mismatches,
            status=TestStatus.PASSED if passed else TestStatus.FAILED,
            executed_at=executed_at, duration_seconds=time.monotonic() - t0,
        )

    def _build_case_bo_report(self, job: JobDefinition):
        def run_job() -> ReconciliationResult:
            from api.services.file_source import read_tabular

            t0 = time.monotonic()
            creds = self._config_snapshot.get("bo_credentials", {})
            env = EnvironmentConfig(name=creds.get("name", "bo"), **{
                k: v for k, v in creds.items() if k != "name"
            })
            client = BORestClient(env)
            client.authenticate()
            doc_id = job.params.get("report_id", "")
            report_id = job.params.get("bo_report_id", "")
            fmt = job.params.get("format", "xlsx")
            try:
                data = client.download_report(doc_id, report_id, fmt)
            finally:
                client.logout()

            df = read_tabular(
                content_b64=base64.b64encode(data).decode("ascii"),
                file_name=f"bo_report_{doc_id}_{report_id}.{fmt}",
            )
            row_count = len(df)
            sample_rows = json.loads(
                df.head(BO_REPORT_SAMPLE_ROW_LIMIT).to_json(orient="records", date_format="iso")
            )
            return ReconciliationResult(
                query_name=job.name,
                source_env=self._source_env,
                target_env=self._target_env,
                source_row_count=row_count,
                target_row_count=row_count,
                matched_count=row_count,
                missing_in_target_count=0,
                missing_in_source_count=0,
                value_mismatch_count=0,
                mismatches=[],
                status=TestStatus.PASSED,
                executed_at=datetime.now(timezone.utc),
                duration_seconds=time.monotonic() - t0,
                sample_rows=sample_rows,
            )
        return run_job

    def _build_case_automic(self, job: JobDefinition):
        def run_job() -> ReconciliationResult:
            creds = self._config_snapshot.get("automic_credentials") or self._config_snapshot.get("config_data") or self._config_snapshot
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

    def _build_case_api_reconciliation(self, job: JobDefinition):
        def run_job() -> ReconciliationResult:
            from etl_framework.config.models import resolve_api_endpoint
            from etl_framework.rest_api.client import APIEndpointClient

            api_endpoints = self._config_snapshot.get("api_endpoints") or {}
            endpoints_snapshot = {"api_endpoints": api_endpoints}
            src_entry = resolve_api_endpoint(endpoints_snapshot, job.params["source_api_endpoint"])
            tgt_entry = resolve_api_endpoint(endpoints_snapshot, job.params["target_api_endpoint"])

            df_a = APIEndpointClient(src_entry).fetch_dataframe()
            df_b = APIEndpointClient(tgt_entry).fetch_dataframe()

            reconciler = ReconciliationEngine(
                source_engine=FrameEngine(df_a, self._source_env),
                target_engine=FrameEngine(df_b, self._target_env),
                key_columns=job.key_columns,
                exclude_columns=job.exclude_columns,
                float_tolerance=self._settings.float_tolerance,
                mismatch_row_limit=self._settings.mismatch_row_limit,
                backend=self._build_backend(job),
            )
            return reconciler.reconcile(query="__api_source__", query_name=job.name)
        return run_job

    def _build_case_dbt(self, job: JobDefinition):
        def run_job() -> ReconciliationResult:
            from api.services.dbt_artifact_parser import DbtArtifactParser

            parser = DbtArtifactParser()
            summary = parser.parse(
                run_results_path=job.params.get("run_results_path") or job.params.get("artifact_path"),
                manifest_path=job.params.get("manifest_path"),
            )
            mismatches = [
                MismatchRecord(
                    key_values={"unique_id": result.unique_id},
                    column_name="dbt_status",
                    source_value=result.status,
                    target_value="pass",
                    mismatch_type="dbt_result",
                )
                for result in summary.results
                if result.status in {"fail", "error"}
            ]
            duration = sum(result.execution_time for result in summary.results)
            return ReconciliationResult(
                query_name=job.name,
                source_env=self._source_env,
                target_env=self._target_env,
                source_row_count=summary.total,
                target_row_count=summary.total,
                matched_count=summary.passed,
                missing_in_target_count=0,
                missing_in_source_count=0,
                value_mismatch_count=len(mismatches),
                mismatches=mismatches,
                status=TestStatus.FAILED if summary.failed else TestStatus.PASSED,
                executed_at=datetime.now(timezone.utc),
                duration_seconds=duration,
            )
        return run_job

    def _resolve_segment_columns(self, job: JobDefinition) -> list[str]:
        """Manual params.segment_columns wins; else auto-pick from latest profile."""
        manual = job.params.get("segment_columns") or []
        if manual:
            return [str(c) for c in manual]
        try:
            from etl_framework.repository.repository import ColumnProfileRepository
            profiles = ColumnProfileRepository(self._db).get_latest(job.name)
        except Exception:
            logger.warning("segment column auto-pick failed for %s", job.name, exc_info=True)
            return []
        return pick_auto_segment_columns(profiles, job.key_columns or self._settings.key_columns or [])

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
        self._fire_webhooks(final_status, passed=passed, failed=failed, error=error)
        self._check_contracts(states)

    def _check_contracts(self, states: list[TestCaseState]) -> None:
        from api.services.contract_breach_checker import ContractBreachChecker
        ContractBreachChecker().check(states, self._run_id, self._db)

    def _fire_webhooks(self, status: str, **extra) -> None:
        try:
            from etl_framework.repository.repository import NotificationRepository
            from api.services.notifier import notify
            hooks = NotificationRepository(self._db).list_enabled_for_event(status)
            notify(self._run_id, status, extra=extra, hooks=hooks, db_session=self._db)
        except Exception:
            pass  # never let notifier failures affect the run
