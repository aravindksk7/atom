from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.routes.selections import _validate_env_requirements
from api.schemas import SequenceRef
from api.services.sequence_resolver import SequenceResolutionError, resolve as resolve_sequence
from etl_framework.repository.repository import JobRepository, JobSelectionRepository, ScheduleRepository
from etl_framework.repository.sequence_repository import ExecutionSequenceRepository
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
    selection_id: int | None = None
    selection_version: int | None = None
    sequence_id: int | None = None
    sequence_version: int | None = None
    source_env: str
    target_env: str = ""
    enabled: bool = True

    @field_validator("cron_expr")
    @classmethod
    def check_cron(cls, v: str) -> str:
        return _validate_cron(v)

    @model_validator(mode="after")
    def check_one_target(self) -> "ScheduleCreate":
        if (self.selection_id is None) == (self.sequence_id is None):
            raise ValueError("Provide exactly one of selection_id or sequence_id")
        return self


class ScheduleOut(BaseModel):
    id: int
    name: str
    cron_expr: str
    selection_id: int | None
    selection_version: int | None
    sequence_id: int | None
    sequence_version: int | None
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


def _resolve_sequence_version(db: Session, sequence_id: int, version: int | None) -> int:
    """Resolve and pin. A schedule always stores a concrete version so a later
    edit to the sequence cannot silently change what the schedule runs."""
    repo = ExecutionSequenceRepository(db)
    if repo.get(sequence_id) is None:
        raise HTTPException(status_code=404, detail="Execution sequence not found")
    if version is None:
        latest = repo.latest_version(sequence_id)
        if latest is None:
            raise HTTPException(status_code=422, detail="Execution sequence has no versions")
        return latest.version_number
    if repo.get_version(sequence_id, version) is None:
        raise HTTPException(status_code=404, detail="Execution sequence version not found")
    return version


def _resolve_and_validate(db: Session, body: "ScheduleCreate") -> tuple[str, int]:
    """Resolve the target version and enforce the same single/dual-env job-type
    check used by ad-hoc launches, so a schedule can't be saved pointing at a
    target that structurally needs a target_env it doesn't have.

    Returns (target_kind, version_number) where target_kind is
    "selection" or "sequence".
    """
    jobs_by_name = {j.name: j for j in JobRepository(db).list()}

    if body.sequence_id is not None:
        version_number = _resolve_sequence_version(db, body.sequence_id, body.sequence_version)
        try:
            resolved = resolve_sequence(
                db, SequenceRef(sequence_id=body.sequence_id, sequence_version=version_number)
            )
        except SequenceResolutionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _validate_env_requirements(resolved.as_linear_steps(), jobs_by_name, body.target_env)
        return "sequence", version_number

    version_number = _resolve_selection_version(db, body.selection_id, body.selection_version)
    version = JobSelectionRepository(db).get_version(body.selection_id, version_number)
    _validate_env_requirements(version.job_sequence or [], jobs_by_name, body.target_env)
    return "selection", version_number


@router.get("/stats")
def scheduler_stats(
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    db: Session = Depends(get_session),
):
    from api.services.scheduler_stats import build_scheduler_stats

    return build_scheduler_stats(db, days=days)


@router.get("", response_model=list[ScheduleOut])
def list_schedules(db: Session = Depends(get_session)):
    return ScheduleRepository(db).list()


@router.post("", response_model=ScheduleOut, status_code=201)
def create_schedule(body: ScheduleCreate, request: Request, db: Session = Depends(get_session)):
    repo = ScheduleRepository(db)
    if repo.get_by_name(body.name):
        raise HTTPException(status_code=409, detail="Schedule name already exists")
    data = body.model_dump()
    kind, version_number = _resolve_and_validate(db, body)
    if kind == "sequence":
        data["sequence_version"] = version_number
        data["selection_id"] = None
        data["selection_version"] = None
    else:
        data["selection_version"] = version_number
        data["sequence_id"] = None
        data["sequence_version"] = None
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
    kind, version_number = _resolve_and_validate(db, body)
    if kind == "sequence":
        data["sequence_version"] = version_number
        data["selection_id"] = None
        data["selection_version"] = None
    else:
        data["selection_version"] = version_number
        data["sequence_id"] = None
        data["sequence_version"] = None
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
