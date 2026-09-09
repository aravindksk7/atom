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
    bo_download_dir: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_from: str = ""
    smtp_user: str = ""
    smtp_password_set: bool = False
    smtp_use_tls: bool = True


class SettingsUpdate(BaseModel):
    timezone: str | None = None
    upload_retention_days: int | None = None
    bo_download_dir: str | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_from: str | None = None
    smtp_user: str | None = None
    smtp_password: str | None = None  # omit to leave unchanged; "" clears it
    smtp_use_tls: bool | None = None


class SmtpTestRequest(BaseModel):
    to: str


def _settings_out(repo: SettingsRepository, row) -> SettingsOut:
    smtp = repo.get_smtp_config()
    return SettingsOut(
        timezone=row.timezone,
        upload_retention_days=int(row.upload_retention_days or 30),
        bo_download_dir=row.bo_download_dir or "",
        smtp_host=smtp["host"],
        smtp_port=smtp["port"],
        smtp_from=smtp["from_addr"],
        smtp_user=smtp["user"],
        smtp_password_set=bool(smtp["password"]),
        smtp_use_tls=smtp["use_tls"],
    )


@router.get("", response_model=SettingsOut)
def get_settings(db: Session = Depends(get_session)):
    repo = SettingsRepository(db)
    return _settings_out(repo, repo._get_or_create())


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
        if body.bo_download_dir is not None:
            row = repo.set_bo_download_dir(body.bo_download_dir)
        smtp_fields = (body.smtp_host, body.smtp_port, body.smtp_from, body.smtp_user,
                       body.smtp_password, body.smtp_use_tls)
        if any(f is not None for f in smtp_fields):
            row = repo.set_smtp_config(
                host=body.smtp_host, port=body.smtp_port, from_addr=body.smtp_from,
                user=body.smtp_user, password=body.smtp_password, use_tls=body.smtp_use_tls,
            )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    AuditService(db).log(
        request,
        "settings.updated",
        "settings",
        1,
        {
            "timezone": row.timezone,
            "upload_retention_days": row.upload_retention_days,
            "bo_download_dir": row.bo_download_dir,
            "smtp_host": row.smtp_host,
            "smtp_port": row.smtp_port,
            "smtp_from": row.smtp_from,
            "smtp_user": row.smtp_user,
            "smtp_use_tls": row.smtp_use_tls,
        },
    )
    return _settings_out(repo, row)


@router.post("/smtp/test", dependencies=[Depends(require_admin)])
def test_smtp(body: SmtpTestRequest, db: Session = Depends(get_session)):
    from api.services.notifier import _resolve_smtp_config, _send_email
    result = _send_email(
        [body.to], "ETL Framework SMTP test",
        "This is a test message from ETL Framework's SMTP settings.",
        config=_resolve_smtp_config(),
    )
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error or "SMTP send failed")
    return {"detail": f"Test email sent to {body.to}"}
