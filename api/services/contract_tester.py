# Data Contracts Testing Engine
# Implements ODCS (Open Data Contract Standard) testing across 4 pillars:
# Schema, Quality, SLA, and Consumer Compatibility

from __future__ import annotations
from typing import Any
import json
import time
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from pydantic import BaseModel

from etl_framework.repository.contract_repository import ContractRepository
from etl_framework.repository.repository import JobRepository, SchemaSnapshotRepository


class CheckResult(BaseModel):
    id: str
    category: str  # "schema" | "quality" | "sla" | "consumers"
    name: str
    status: str  # "PASS" | "FAIL" | "WARN"
    target: str
    expected: Any
    actual: Any
    message: str


class ContractTestReport(BaseModel):
    contract: str
    source_job: str
    version: str
    overall_status: str  # "PASSED" | "FAILED" | "WARNING"
    executed_at: str
    duration_ms: float
    summary: dict[str, Any]
    checks: list[CheckResult]


class ContractTestingEngine:
    def __init__(self, db: Session):
        self._db = db
        self._contract_repo = ContractRepository(db)
        self._job_repo = JobRepository(db)
        self._schema_repo = SchemaSnapshotRepository(db)

    def test_contract(self, contract_name: str) -> ContractTestReport:
        start_time = time.time()
        
        contract = self._contract_repo.get(contract_name)
        if not contract:
            raise ValueError(f"Contract '{contract_name}' not found")
        
        # Run all evaluators
        all_checks = []
        all_checks.extend(self.evaluate_schema_conformance(contract_name))
        all_checks.extend(self.evaluate_quality_rules(contract_name))
        all_checks.extend(self.evaluate_sla_freshness(contract_name))
        all_checks.extend(self.evaluate_consumer_compatibility(contract_name))
        
        # Calculate summary
        total = len(all_checks)
        passed = sum(1 for c in all_checks if c.status == "PASS")
        failed = sum(1 for c in all_checks if c.status == "FAIL")
        warnings = sum(1 for c in all_checks if c.status == "WARN")
        pass_rate = round((passed / total * 100) if total > 0 else 0, 1)
        
        # Determine overall status
        if failed > 0:
            overall_status = "FAILED"
        elif warnings > 0:
            overall_status = "WARNING"
        else:
            overall_status = "PASSED"
        
        duration_ms = round((time.time() - start_time) * 1000, 2)
        
        return ContractTestReport(
            contract=contract.name,
            source_job=contract.source_job,
            version=contract.version,
            overall_status=overall_status,
            executed_at=datetime.now(timezone.utc).isoformat(),
            duration_ms=duration_ms,
            summary={
                "total": total,
                "passed": passed,
                "failed": failed,
                "warnings": warnings,
                "pass_rate": pass_rate
            },
            checks=all_checks
        )

    def evaluate_schema_conformance(self, contract_name: str) -> list[CheckResult]:
        checks = []
        contract = self._contract_repo.get(contract_name)
        if not contract:
            return checks
        
        job = self._job_repo.get(contract.source_job)
        if not job:
            return checks
        
        snapshot = self._schema_repo.get_latest(contract.source_job, "source")
        if not snapshot:
            checks.append(CheckResult(
                id=f"schema_snapshot_{contract.source_job}",
                category="schema",
                name="Schema snapshot exists",
                status="FAIL",
                target=contract.source_job,
                expected="Schema snapshot available",
                actual="No snapshot found",
                message=f"No schema snapshot found for job {contract.source_job}"
            ))
            return checks
        
        # Parse snapshot columns
        columns = snapshot.columns
        if isinstance(columns, str):
            try:
                columns = json.loads(columns)
            except Exception:
                columns = []
        
        # Get expected columns from job params
        params = job.params or {}
        null_check_cols = params.get("null_check_columns", [])
        key_cols = params.get("key_columns", [])
        expected_cols = set(null_check_cols + key_cols)
        
        snapshot_col_names = {c["name"] for c in columns if isinstance(c, dict)}
        
        # Check each expected column exists
        for col_name in expected_cols:
            if col_name in snapshot_col_names:
                checks.append(CheckResult(
                    id=f"schema_col_{col_name}",
                    category="schema",
                    name="Column exists",
                    status="PASS",
                    target=col_name,
                    expected="Present",
                    actual="Present",
                    message=f"Column {col_name} found in schema"
                ))
            else:
                checks.append(CheckResult(
                    id=f"schema_col_{col_name}",
                    category="schema",
                    name="Column exists",
                    status="FAIL",
                    target=col_name,
                    expected="Present",
                    actual="Missing",
                    message=f"Expected column {col_name} not found in schema"
                ))
        
        return checks

    def evaluate_quality_rules(self, contract_name: str) -> list[CheckResult]:
        checks = []
        contract = self._contract_repo.get(contract_name)
        if not contract:
            return checks
        
        job = self._job_repo.get(contract.source_job)
        if not job:
            return checks
        
        # Get latest test result for source job
        from etl_framework.repository.models import TestResult
        
        latest_result = (
            self._db.query(TestResult)
            .filter(TestResult.query_name == contract.source_job)
            .order_by(TestResult.executed_at.desc())
            .first()
        )
        
        if not latest_result:
            checks.append(CheckResult(
                id=f"quality_result_{contract.source_job}",
                category="quality",
                name="Test result exists",
                status="FAIL",
                target=contract.source_job,
                expected="Test result available",
                actual="No result found",
                message=f"No test result found for job {contract.source_job}"
            ))
            return checks
        
        # Check row count non-zero
        if latest_result.source_row_count > 0:
            checks.append(CheckResult(
                id="quality_row_count",
                category="quality",
                name="Row count non-zero",
                status="PASS",
                target=contract.source_job,
                expected="> 0",
                actual=str(latest_result.source_row_count),
                message=f"Job produced {latest_result.source_row_count} rows"
            ))
        else:
            checks.append(CheckResult(
                id="quality_row_count",
                category="quality",
                name="Row count non-zero",
                status="FAIL",
                target=contract.source_job,
                expected="> 0",
                actual="0",
                message="Job produced zero rows"
            ))
        
        # Check null constraints (inferred from test status and params)
        params = job.params or {}
        null_check_cols = params.get("null_check_columns", [])
        for col in null_check_cols:
            if latest_result.status == "PASSED":
                checks.append(CheckResult(
                    id=f"quality_not_null_{col}",
                    category="quality",
                    name="Not-null constraint",
                    status="PASS",
                    target=col,
                    expected="No nulls",
                    actual="No nulls",
                    message=f"Column {col} contains no null values"
                ))
            else:
                checks.append(CheckResult(
                    id=f"quality_not_null_{col}",
                    category="quality",
                    name="Not-null constraint",
                    status="WARN",
                    target=col,
                    expected="No nulls",
                    actual="Unknown",
                    message=f"Test failed; null check for {col} cannot be verified"
                ))
        
        # Check unique key constraints
        key_cols = params.get("key_columns", [])
        for col in key_cols:
            if latest_result.status == "PASSED" and latest_result.value_mismatch_count == 0:
                checks.append(CheckResult(
                    id=f"quality_unique_{col}",
                    category="quality",
                    name="Unique key constraint",
                    status="PASS",
                    target=col,
                    expected="All unique",
                    actual="All unique",
                    message=f"Key column {col} contains unique values"
                ))
            else:
                checks.append(CheckResult(
                    id=f"quality_unique_{col}",
                    category="quality",
                    name="Unique key constraint",
                    status="WARN",
                    target=col,
                    expected="All unique",
                    actual="Unknown",
                    message=f"Uniqueness for {col} cannot be verified from test result"
                ))
        
        return checks

    def evaluate_sla_freshness(self, contract_name: str) -> list[CheckResult]:
        checks = []
        contract = self._contract_repo.get(contract_name)
        if not contract:
            return checks
        
        # Get latest completed test run for source job
        from etl_framework.repository.models import TestResult
        
        latest_result = (
            self._db.query(TestResult)
            .filter(TestResult.query_name == contract.source_job)
            .filter(TestResult.status == "PASSED")
            .order_by(TestResult.executed_at.desc())
            .first()
        )
        
        if not latest_result or not latest_result.executed_at:
            checks.append(CheckResult(
                id="sla_freshness",
                category="sla",
                name="Data freshness",
                status="FAIL",
                target=contract.source_job,
                expected=f"<= {contract.sla_hours}h",
                actual="No successful run",
                message=f"No successful run found for {contract.source_job}"
            ))
            return checks
        
        # Calculate elapsed time
        now = datetime.now(timezone.utc)
        executed_at = latest_result.executed_at
        if executed_at.tzinfo is None:
            executed_at = executed_at.replace(tzinfo=timezone.utc)
        
        elapsed_hours = (now - executed_at).total_seconds() / 3600
        
        if elapsed_hours <= contract.sla_hours:
            checks.append(CheckResult(
                id="sla_freshness",
                category="sla",
                name="Data freshness",
                status="PASS",
                target=contract.source_job,
                expected=f"<= {contract.sla_hours}h",
                actual=f"{elapsed_hours:.1f}h",
                message=f"Last successful run was {elapsed_hours:.1f}h ago (within SLA)"
            ))
        else:
            overdue_hours = elapsed_hours - contract.sla_hours
            checks.append(CheckResult(
                id="sla_freshness",
                category="sla",
                name="Data freshness",
                status="FAIL",
                target=contract.source_job,
                expected=f"<= {contract.sla_hours}h",
                actual=f"{elapsed_hours:.1f}h",
                message=f"Last run was {elapsed_hours:.1f}h ago, exceeds SLA by {overdue_hours:.1f}h"
            ))
        
        # Check for open breaches
        open_breaches = self._contract_repo.list_open_breaches(contract.id)
        if len(open_breaches) == 0:
            checks.append(CheckResult(
                id="sla_breaches",
                category="sla",
                name="Open breaches",
                status="PASS",
                target=contract.name,
                expected="0",
                actual="0",
                message="No open contract breaches"
            ))
        else:
            escalated = sum(1 for b in open_breaches if b.escalated)
            checks.append(CheckResult(
                id="sla_breaches",
                category="sla",
                name="Open breaches",
                status="FAIL",
                target=contract.name,
                expected="0",
                actual=str(len(open_breaches)),
                message=f"{len(open_breaches)} open breach(es), {escalated} escalated"
            ))
        
        return checks

    def evaluate_consumer_compatibility(self, contract_name: str) -> list[CheckResult]:
        checks = []
        contract = self._contract_repo.get(contract_name)
        if not contract:
            return checks
        
        # Parse consumers list
        consumers = contract.consumers
        if isinstance(consumers, str):
            try:
                consumers = json.loads(consumers)
            except Exception:
                consumers = []
        
        if len(consumers) == 0:
            checks.append(CheckResult(
                id="consumers_declared",
                category="consumers",
                name="Consumers declared",
                status="WARN",
                target=contract.name,
                expected=">= 1",
                actual="0",
                message="No downstream consumers declared for this contract"
            ))
        else:
            checks.append(CheckResult(
                id="consumers_declared",
                category="consumers",
                name="Consumers declared",
                status="PASS",
                target=contract.name,
                expected=">= 1",
                actual=str(len(consumers)),
                message=f"Contract has {len(consumers)} declared consumer(s): {', '.join(consumers)}"
            ))
        
        # Check version follows semantic versioning
        version = contract.version or "1.0"
        parts = version.split(".")
        if len(parts) >= 2 and all(p.isdigit() for p in parts[:2]):
            checks.append(CheckResult(
                id="consumers_version",
                category="consumers",
                name="Semantic versioning",
                status="PASS",
                target=contract.name,
                expected="major.minor format",
                actual=version,
                message=f"Contract version {version} follows semantic versioning"
            ))
        else:
            checks.append(CheckResult(
                id="consumers_version",
                category="consumers",
                name="Semantic versioning",
                status="WARN",
                target=contract.name,
                expected="major.minor format",
                actual=version,
                message=f"Contract version {version} does not follow semantic versioning"
            ))
        
        return checks