from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.schemas import AuditEventOut
from api.services.audit_service import AuditService

router = APIRouter(tags=["audit"])


@router.get("", response_model=list[AuditEventOut])
def list_audit_events(
    resource_type: str | None = None,
    resource_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_session),
):
    return AuditService(db).list(
        resource_type=resource_type,
        resource_id=resource_id,
        limit=limit,
        offset=offset,
    )
