from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.routes.selections import _validate_env_requirements
from etl_framework.repository.repository import JobRepository, JobSelectionRepository, ScheduleRepository
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
    selection_id: int
    selection_version: int | None = None
    source_env: str
    target_env: str = ""
    enabled: bool = True

    @field_validator("cron_expr")
    @classmethod
    def check_cron(cls, v: str) -> str:
        return _validate_cron(v)


class ScheduleOut(BaseModel):
    id: int
    name: str
    cron_expr: str
    selection_id: int
    selection_version: int
    source_env: str
    target_env: str
    enabled: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    created_at: datetime
    model_config = {"from_attributes": True}


def _resolve_selection_version(db: Session, selection_id: int, version: int | None) -> int:
    sel_repo = JobSelectionRepository(db)
    if sel_repo.get(selection_id) is None:
        raise HTTPException(status_code=404, detail="Job selection not found")
    if version is None:
        latest = sel_repo.latest_version(selection_id)
        if latest is None:
            raise HTTPException(status_code=422, detail="Job selection has no versions")
        return latest.version_number
    if sel_repo.get_version(selection_id, version) is None:
        raise HTTPException(status_code=404, detail="Selection version not found")
    return version


def _resolve_and_validate(db: Session, body: "ScheduleCreate") -> int:
    """Resolve the target selection_version and enforce the same single/dual-env
    job-type check used by ad-hoc launches, so a schedule can't be saved pointing
    at a selection that structurally needs a target_env it doesn't have."""
    version_number = _resolve_selection_version(db, body.selection_id, body.selection_version)
    version = JobSelectionRepository(db).get_version(body.selection_id, version_number)
    jobs_by_name = {j.name: j for j in JobRepository(db).list()}
    _validate_env_requirements(version.job_sequence or [], jobs_by_name, body.target_env)
    return version_number


@router.get("", response_model=list[ScheduleOut])
def list_schedules(db: Session = Depends(get_session)):
    return ScheduleRepository(db).list()


@router.post("", response_model=ScheduleOut, status_code=201)
def create_schedule(body: ScheduleCreate, request: Request, db: Session = Depends(get_session)):
    repo = ScheduleRepository(db)
    if repo.get_by_name(body.name):
        raise HTTPException(status_code=409, detail="Schedule name already exists")
    data = body.model_dump()
    data["selection_version"] = _resolve_and_validate(db, body)
    sched = repo.create(data)
    _sched_svc.add_job(sched)
    AuditService(db).log(
        request, "schedule.created", "schedule", sched.id,
        {"name": sched.name, "cron_expr": sched.cron_expr, "selection_id": sched.selection_id},
    )
    return sched


@router.put("/{schedule_id}", response_model=ScheduleOut)
def update_schedule(
    schedule_id: int, body: ScheduleCreate, request: Request, db: Session = Depends(get_session)
):
    data = body.model_dump()
    data["selection_version"] = _resolve_and_validate(db, body)
    repo = ScheduleRepository(db)
    sched = repo.update(schedule_id, data)
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
