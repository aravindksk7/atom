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
    upload_retention_days: int = 30


class SettingsUpdate(BaseModel):
    timezone: str | None = None
    upload_retention_days: int | None = None


@router.get("", response_model=SettingsOut)
def get_settings(db: Session = Depends(get_session)):
    repo = SettingsRepository(db)
    return SettingsOut(
        timezone=repo.get_timezone(),
        upload_retention_days=repo.get_upload_retention_days(),
    )


@router.put("", response_model=SettingsOut, dependencies=[Depends(require_admin)])
def update_settings(body: SettingsUpdate, request: Request, db: Session = Depends(get_session)):
    repo = SettingsRepository(db)
    try:
        if body.timezone is not None:
            row = repo.set_timezone(body.timezone)
            from api.services import scheduler as _sched_svc
            _sched_svc.refresh_all_timezones()
        else:
            row = repo._get_or_create()
        if body.upload_retention_days is not None:
            row = repo.set_upload_retention_days(body.upload_retention_days)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    AuditService(db).log(
        request,
        "settings.updated",
        "settings",
        1,
        {"timezone": row.timezone, "upload_retention_days": row.upload_retention_days},
    )
    return SettingsOut(
        timezone=row.timezone,
        upload_retention_days=int(row.upload_retention_days or 30),
    )
