from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from api.dependencies import get_session
from etl_framework.repository.repository import ScheduleRepository
import api.services.scheduler as _sched_svc
from api.services.audit_service import AuditService

router = APIRouter(tags=["schedules"])


def _validate_cron(expr: str) -> str:
    try:
        from croniter import croniter
        if not croniter.is_valid(expr):
            raise ValueError("invalid")
    except ImportError:
        pass  # croniter not installed — skip validation
    return expr


class ScheduleCreate(BaseModel):
    name: str
    cron_expr: str
    job_sequence: list[str]
    source_env: str
    target_env: str
    run_settings_json: dict = {}
    enabled: bool = True

    @field_validator("cron_expr")
    @classmethod
    def check_cron(cls, v: str) -> str:
        return _validate_cron(v)


class ScheduleOut(BaseModel):
    id: int
    name: str
    cron_expr: str
    job_sequence: list[str]
    source_env: str
    target_env: str
    run_settings_json: dict
    enabled: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    created_at: datetime
    model_config = {"from_attributes": True}


@router.get("", response_model=list[ScheduleOut])
def list_schedules(db: Session = Depends(get_session)):
    return ScheduleRepository(db).list()


@router.post("", response_model=ScheduleOut, status_code=201)
def create_schedule(body: ScheduleCreate, request: Request, db: Session = Depends(get_session)):
    repo = ScheduleRepository(db)
    if repo.get_by_name(body.name):
        raise HTTPException(status_code=409, detail="Schedule name already exists")
    sched = repo.create(body.model_dump())
    _sched_svc.add_job(sched)
    AuditService(db).log(
        request, "schedule.created", "schedule", sched.id,
        {"name": sched.name, "cron_expr": sched.cron_expr},
    )
    return sched


@router.put("/{schedule_id}", response_model=ScheduleOut)
def update_schedule(
    schedule_id: int, body: ScheduleCreate, request: Request, db: Session = Depends(get_session)
):
    repo = ScheduleRepository(db)
    sched = repo.update(schedule_id, body.model_dump())
    if sched is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    _sched_svc.reload_job(sched)
    AuditService(db).log(
        request, "schedule.updated", "schedule", sched.id,
        {"name": sched.name, "cron_expr": sched.cron_expr, "enabled": sched.enabled},
    )
    return sched


@router.delete("/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: int, request: Request, db: Session = Depends(get_session)):
    if not ScheduleRepository(db).delete(schedule_id):
        raise HTTPException(status_code=404, detail="Schedule not found")
    _sched_svc.remove_job(schedule_id)
    AuditService(db).log(request, "schedule.deleted", "schedule", schedule_id)


@router.post("/{schedule_id}/run-now", status_code=202)
def run_now(schedule_id: int, request: Request, db: Session = Depends(get_session)):
    sched = ScheduleRepository(db).get(schedule_id)
    if sched is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    import threading
    from api.services.scheduler import _run_schedule
    threading.Thread(
        target=_run_schedule, args=(sched.id, sched.name), daemon=True
    ).start()
    AuditService(db).log(request, "schedule.run_now", "schedule", schedule_id)
    return {"detail": f"Schedule '{sched.name}' triggered manually"}
