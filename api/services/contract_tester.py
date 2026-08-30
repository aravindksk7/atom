from pydantic import BaseModel
from typing import Any

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
