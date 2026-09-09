from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from api.dependencies import get_session
from etl_framework.repository.repository import NotificationDeliveryRepository, NotificationRepository
from api.services.notifier import EVENTS
from api.services.audit_service import AuditService

router = APIRouter(tags=["notifications"])

_ALL_EVENTS = sorted(EVENTS)
_CHANNELS = ("generic", "email")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class HookCreate(BaseModel):
    name: str
    channel: str = "generic"
    url: str
    events: list[str] = list(_ALL_EVENTS)
    secret: str | None = None

    @field_validator("channel")
    @classmethod
    def _validate_channel(cls, v: str) -> str:
        if v not in _CHANNELS:
            raise ValueError(f"channel must be one of {_CHANNELS}")
        return v

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str, info) -> str:
        channel = info.data.get("channel", "generic")
        if channel == "email":
            addrs = [a.strip() for a in v.removeprefix("mailto:").split(",") if a.strip()]
            if not addrs or not all(_EMAIL_RE.match(a) for a in addrs):
                raise ValueError("url must be mailto:addr1,addr2 with valid email addresses")
            return "mailto:" + ",".join(addrs)
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("url must be an http(s) URL")
        return v


class HookUpdate(BaseModel):
    enabled: bool | None = None
    events: list[str] | None = None


class HookOut(BaseModel):
    id: int
    name: str
    url: str
    events: list[str]
    enabled: bool
    channel: str
    created_at: datetime
    model_config = {"from_attributes": True}


class DeliveryOut(BaseModel):
    id: int
    hook_id: int
    run_id: str
    event: str
    status: str
    attempt_count: int
    last_attempt_at: datetime | None = None
    delivered_at: datetime | None = None
    error_message: str | None = None
    response_status_code: int | None = None
    created_at: datetime
    model_config = {"from_attributes": True}


@router.get("", response_model=list[HookOut])
def list_hooks(db: Session = Depends(get_session)):
    return NotificationRepository(db).list()


@router.post("", response_model=HookOut, status_code=201)
def create_hook(body: HookCreate, request: Request, db: Session = Depends(get_session)):
    invalid = [e for e in body.events if e not in EVENTS]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Unknown events: {invalid}")
    hook = NotificationRepository(db).create(body.name, body.url, body.events, body.secret, body.channel)
    AuditService(db).log(
        request, "notification_hook.created", "notification_hook", hook.id,
        {"name": hook.name, "events": hook.events},
    )
    return hook


@router.patch("/{hook_id}", response_model=HookOut)
def update_hook(hook_id: int, body: HookUpdate, request: Request, db: Session = Depends(get_session)):
    if body.events is not None:
        invalid = [e for e in body.events if e not in EVENTS]
        if invalid:
            raise HTTPException(status_code=400, detail=f"Unknown events: {invalid}")
    hook = NotificationRepository(db).update(hook_id, enabled=body.enabled, events=body.events)
    if hook is None:
        raise HTTPException(status_code=404, detail="Hook not found")
    AuditService(db).log(
        request, "notification_hook.updated", "notification_hook", hook_id,
        {"enabled": body.enabled, "events": body.events},
    )
    return hook


@router.delete("/{hook_id}", status_code=204)
def delete_hook(hook_id: int, request: Request, db: Session = Depends(get_session)):
    if not NotificationRepository(db).delete(hook_id):
        raise HTTPException(status_code=404, detail="Hook not found")
    AuditService(db).log(request, "notification_hook.deleted", "notification_hook", hook_id)


@router.get("/{hook_id}/deliveries", response_model=list[DeliveryOut])
def list_hook_deliveries(
    hook_id: int,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_session),
):
    if NotificationRepository(db).get(hook_id) is None:
        raise HTTPException(status_code=404, detail="Hook not found")
    return NotificationDeliveryRepository(db).list_deliveries_for_hook(
        hook_id,
        limit=max(1, min(limit, 200)),
        offset=max(0, offset),
    )


@router.post("/{hook_id}/test", status_code=202)
def test_hook(hook_id: int, request: Request, db: Session = Depends(get_session)):
    hook = NotificationRepository(db).get(hook_id)
    if hook is None:
        raise HTTPException(status_code=404, detail="Hook not found")
    import threading
    if hook.channel == "email":
        from api.services.notifier import _resolve_smtp_config, _send_email, parse_mailto
        recipients = parse_mailto(hook.url)
        smtp_config = _resolve_smtp_config()
        threading.Thread(
            target=_send_email,
            args=(recipients, "ETL Framework webhook test", "This is a test notification from ETL Framework.", smtp_config),
            daemon=True,
        ).start()
    else:
        from api.services.notifier import _post
        from api.services.secret_store import decrypt_secret
        payload = {"event": "test.ping", "run_id": "test", "status": "TEST",
                   "message": "ETL Framework webhook test"}
        hook_secret = decrypt_secret(hook.secret)
        threading.Thread(target=_post, args=(hook.url, payload, hook_secret), daemon=True).start()
    AuditService(db).log(request, "notification_hook.tested", "notification_hook", hook_id)
    return {"detail": "Test ping dispatched"}
