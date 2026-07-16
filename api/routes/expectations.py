from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.services.audit_service import AuditService
from api.services.expectations_service import SyncReport, export_suites, sync_suites

router = APIRouter(prefix="/api/expectations", tags=["expectations"])


class SyncRequest(BaseModel):
    directory: str = "expectations"


@router.post("/sync", response_model=SyncReport)
def sync_expectations(body: SyncRequest, request: Request, db: Session = Depends(get_session)):
    directory = Path(body.directory)
    if not directory.is_dir():
        raise HTTPException(status_code=400, detail=f"Directory not found: {body.directory}")
    report = sync_suites(directory, db)
    AuditService(db).log(
        request, "expectations.synced", "expectations", body.directory,
        report.model_dump(),
    )
    return report


@router.post("/export")
def export_expectations(body: SyncRequest, db: Session = Depends(get_session)):
    written = export_suites(Path(body.directory), db)
    return {"written": written}
