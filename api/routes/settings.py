from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.dependencies import get_session, require_admin
from api.services.audit_service import AuditService
from etl_framework.repository.repository import SettingsRepository

router = APIRouter(tags=["settings"])


class SettingsOut(BaseModel):
    timezone: str


class SettingsUpdate(BaseModel):
    timezone: str


@router.get("", response_model=SettingsOut)
def get_settings(db: Session = Depends(get_session)):
    return SettingsOut(timezone=SettingsRepository(db).get_timezone())


@router.put("", response_model=SettingsOut, dependencies=[Depends(require_admin)])
def update_settings(body: SettingsUpdate, request: Request, db: Session = Depends(get_session)):
    try:
        row = SettingsRepository(db).set_timezone(body.timezone)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    from api.services import scheduler as _sched_svc
    _sched_svc.refresh_all_timezones()

    AuditService(db).log(request, "settings.timezone_changed", "settings", 1, {"timezone": row.timezone})
    return SettingsOut(timezone=row.timezone)
