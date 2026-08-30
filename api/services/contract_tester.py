from pydantic import BaseModel
from typing import Any
from sqlalchemy.orm import Session
from etl_framework.repository.contract_repository import ContractRepository
from etl_framework.repository.repository import JobRepository, SchemaSnapshotRepository
import json

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