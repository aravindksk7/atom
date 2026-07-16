"""Write-Audit-Publish gate: PROMOTE/HOLD verdict for a job's staged data.

Orchestration contract: load staging -> run job -> call gate -> swap/publish
only on PROMOTE. The gate never publishes anything itself.
"""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field
from sqlalchemy import desc
from sqlalchemy.orm import Session

from etl_framework.repository.contract_models import Contract, ContractBreach
from etl_framework.repository.models import TestResult


class GateVerdict(BaseModel):
    job: str
    verdict: str  # "PROMOTE" | "HOLD"
    run_id: str | None = None
    result_status: str | None = None
    reasons: list[str] = Field(default_factory=list)
    evaluated_at: datetime


def evaluate_gate(job_name: str, db: Session) -> GateVerdict:
    reasons: list[str] = []

    result = (
        db.query(TestResult)
        .filter(TestResult.query_name == job_name)
        .order_by(desc(TestResult.executed_at), desc(TestResult.id))
        .first()
    )
    if result is None:
        reasons.append(f"No run result found for job '{job_name}'")
    elif result.status != "PASSED":
        reasons.append(
            f"Latest result for '{job_name}' is {result.status}"
            + (f": {result.error_message}" if result.error_message else "")
        )

    open_breaches = (
        db.query(ContractBreach)
        .join(Contract, Contract.id == ContractBreach.contract_id)
        .filter(
            Contract.source_job == job_name,
            Contract.active.is_(True),
            ContractBreach.resolved_at.is_(None),
        )
        .count()
    )
    if open_breaches:
        reasons.append(f"{open_breaches} open contract breach(es) on '{job_name}'")

    return GateVerdict(
        job=job_name,
        verdict="HOLD" if reasons else "PROMOTE",
        run_id=result.run_id if result is not None else None,
        result_status=result.status if result is not None else None,
        reasons=reasons,
        evaluated_at=datetime.now(timezone.utc),
    )
