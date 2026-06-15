from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, HttpUrl
from sqlalchemy.orm import Session

from api.dependencies import get_session
from etl_framework.repository.repository import NotificationRepository
from api.services.notifier import EVENTS
from api.services.audit_service import AuditService

router = APIRouter(tags=["notifications"])

_ALL_EVENTS = sorted(EVENTS)


class HookCreate(BaseModel):
    name: str
    url: str
    events: list[str] = list(_ALL_EVENTS)
    secret: str | None = None


class HookOut(BaseModel):
    id: int
    name: str
    url: str
    events: list[str]
    enabled: bool
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
    hook = NotificationRepository(db).create(body.name, body.url, body.events, body.secret)
    AuditService(db).log(
        request, "notification_hook.created", "notification_hook", hook.id,
        {"name": hook.name, "events": hook.events},
    )
    return hook


@router.delete("/{hook_id}", status_code=204)
def delete_hook(hook_id: int, request: Request, db: Session = Depends(get_session)):
    if not NotificationRepository(db).delete(hook_id):
        raise HTTPException(status_code=404, detail="Hook not found")
    AuditService(db).log(request, "notification_hook.deleted", "notification_hook", hook_id)


@router.post("/{hook_id}/test", status_code=202)
def test_hook(hook_id: int, request: Request, db: Session = Depends(get_session)):
    hook = NotificationRepository(db).get(hook_id)
    if hook is None:
        raise HTTPException(status_code=404, detail="Hook not found")
    from api.services.notifier import _post
    import threading
    payload = {"event": "test.ping", "run_id": "test", "status": "TEST",
               "message": "ETL Framework webhook test"}
    threading.Thread(target=_post, args=(hook.url, payload, hook.secret), daemon=True).start()
    AuditService(db).log(request, "notification_hook.tested", "notification_hook", hook_id)
    return {"detail": "Test ping dispatched"}
