from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.services.audit_service import AuditService
from api.services.gate_service import GateVerdict, evaluate_gate

router = APIRouter(tags=["gates"])


@router.post("/{job_name}/evaluate", response_model=GateVerdict)
def evaluate(job_name: str, request: Request, db: Session = Depends(get_session)):
    verdict = evaluate_gate(job_name, db)
    AuditService(db).log(
        request, "gate.evaluated", "gate", job_name,
        {"verdict": verdict.verdict, "reasons": verdict.reasons},
    )
    return verdict
