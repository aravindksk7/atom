from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.schemas import (
    JobSelectionCreate,
    JobSelectionDetailOut,
    JobSelectionLaunchRequest,
    JobSelectionOut,
    JobSelectionUpdate,
    JobSelectionVersionOut,
    RunStatusOut,
    RunTrigger,
)
from api.routes.runs import _execute_run, _snapshot_from_trigger
from api.services.audit_service import AuditService
from etl_framework.repository.repository import JobRepository, JobSelectionRepository, RunRepository

router = APIRouter(tags=["selections"])

# Job types whose execution only touches one environment (per the approved
# design spec); everything else needs a target_env to compare against.
_SINGLE_ENV_JOB_TYPES = {"bo_report", "freshness", "profile", "automic_job", "dbt_artifact", "schema_snapshot"}


def _selection_out(selection) -> JobSelectionOut:
    latest = selection.versions[-1] if selection.versions else None
    return JobSelectionOut(
        id=selection.id,
        name=selection.name,
        description=selection.description,
        tags=selection.tags or [],
        archived=selection.archived,
        latest_version=latest.version_number if latest else 0,
        job_count=len(latest.job_sequence) if latest else 0,
        created_at=selection.created_at,
        updated_at=selection.updated_at,
    )


def _version_out(version) -> JobSelectionVersionOut:
    return JobSelectionVersionOut(
        version_number=version.version_number,
        job_sequence=version.job_sequence or [],
        run_settings=version.run_settings_json or {},
        created_at=version.created_at,
    )


def _detail_out(selection) -> JobSelectionDetailOut:
    base = _selection_out(selection)
    return JobSelectionDetailOut(
        **base.model_dump(),
        versions=[_version_out(v) for v in selection.versions],
    )


def _dump_job_sequence(job_sequence) -> list:
    return [s.model_dump() if hasattr(s, "model_dump") else s for s in job_sequence]


@router.get("", response_model=list[JobSelectionOut])
def list_selections(db: Session = Depends(get_session)):
    return [_selection_out(s) for s in JobSelectionRepository(db).list()]


@router.post("", response_model=JobSelectionOut, status_code=201)
def create_selection(body: JobSelectionCreate, request: Request, db: Session = Depends(get_session)):
    repo = JobSelectionRepository(db)
    if repo.get_by_name(body.name) is not None:
        raise HTTPException(status_code=409, detail="A job selection with this name already exists")
    job_sequence = _dump_job_sequence(body.job_sequence)
    selection = repo.create(
        name=body.name, description=body.description, tags=body.tags,
        job_sequence=job_sequence, run_settings=body.run_settings.model_dump(),
    )
    AuditService(db).log(
        request, "selection.created", "job_selection", selection.id,
        {"name": selection.name, "job_count": len(job_sequence)},
    )
    return _selection_out(selection)


@router.get("/{selection_id}", response_model=JobSelectionDetailOut)
def get_selection(selection_id: int, db: Session = Depends(get_session)):
    selection = JobSelectionRepository(db).get(selection_id)
    if selection is None:
        raise HTTPException(status_code=404, detail="Job selection not found")
    return _detail_out(selection)


@router.get("/{selection_id}/versions/{version_number}", response_model=JobSelectionVersionOut)
def get_selection_version(selection_id: int, version_number: int, db: Session = Depends(get_session)):
    repo = JobSelectionRepository(db)
    if repo.get(selection_id) is None:
        raise HTTPException(status_code=404, detail="Job selection not found")
    version = repo.get_version(selection_id, version_number)
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")
    return _version_out(version)


@router.put("/{selection_id}", response_model=JobSelectionDetailOut)
def update_selection(
    selection_id: int, body: JobSelectionUpdate, request: Request, db: Session = Depends(get_session)
):
    repo = JobSelectionRepository(db)
    selection = repo.get(selection_id)
    if selection is None:
        raise HTTPException(status_code=404, detail="Job selection not found")

    repo.update_metadata(selection_id, name=body.name, description=body.description, tags=body.tags)

    if body.job_sequence is not None or body.run_settings is not None:
        job_sequence = _dump_job_sequence(body.job_sequence) if body.job_sequence is not None else None
        run_settings = body.run_settings.model_dump() if body.run_settings is not None else None
        repo.create_new_version(selection_id, job_sequence, run_settings)

    db.refresh(selection)
    AuditService(db).log(request, "selection.updated", "job_selection", selection_id, {"name": selection.name})
    return _detail_out(selection)


@router.delete("/{selection_id}", status_code=204)
def archive_selection(selection_id: int, request: Request, db: Session = Depends(get_session)):
    repo = JobSelectionRepository(db)
    if repo.get(selection_id) is None:
        raise HTTPException(status_code=404, detail="Job selection not found")
    try:
        repo.archive_or_raise(selection_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    AuditService(db).log(request, "selection.archived", "job_selection", selection_id)


@router.get("/{selection_id}/runs", response_model=list[RunStatusOut])
def list_selection_runs(selection_id: int, db: Session = Depends(get_session)):
    repo = JobSelectionRepository(db)
    if repo.get(selection_id) is None:
        raise HTTPException(status_code=404, detail="Job selection not found")
    return [
        RunStatusOut(
            run_id=r.run_id, status=r.status, started_at=r.started_at,
            completed_at=r.completed_at, total_tests=r.total_tests,
            passed=r.passed, failed=r.failed, slow=r.slow, error=r.error,
            run_type=r.run_type, pair_id=r.pair_id,
        )
        for r in repo.runs_for_selection(selection_id)
    ]


def _job_name_of(step) -> str:
    if isinstance(step, dict):
        return step.get("job_name", "")
    if hasattr(step, "job_name"):
        return step.job_name
    return str(step)


def _validate_env_requirements(job_sequence: list, jobs_by_name: dict, target_env: str) -> None:
    if target_env:
        return
    for step in job_sequence:
        job_name = _job_name_of(step)
        job = jobs_by_name.get(job_name)
        if job is not None and job.job_type not in _SINGLE_ENV_JOB_TYPES:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Job '{job_name}' (type '{job.job_type}') requires a target_env; "
                    "only single-environment job types can run with target_env omitted"
                ),
            )


@router.post("/{selection_id}/launch", response_model=RunStatusOut, status_code=202)
def launch_selection(
    selection_id: int,
    body: JobSelectionLaunchRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_session),
):
    repo = JobSelectionRepository(db)
    selection = repo.get(selection_id)
    if selection is None:
        raise HTTPException(status_code=404, detail="Job selection not found")

    version = (
        repo.get_version(selection_id, body.version) if body.version is not None
        else repo.latest_version(selection_id)
    )
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")

    jobs_by_name = {j.name: j for j in JobRepository(db).list()}
    _validate_env_requirements(version.job_sequence or [], jobs_by_name, body.target_env)

    trigger = RunTrigger(
        source_env=body.source_env,
        target_env=body.target_env,
        source_connection=body.source_connection,
        target_connection=body.target_connection,
        job_sequence=version.job_sequence or [],
        config_id=body.config_id,
        config_data=body.config_data,
        run_settings=version.run_settings_json or {},
    )

    run_id = str(uuid.uuid4())
    ordered_jobs = trigger.job_sequence
    config_snapshot = _snapshot_from_trigger(trigger, db)
    config_snapshot["job_sequence"] = _dump_job_sequence(ordered_jobs)
    config_snapshot["run_settings"] = trigger.run_settings.model_dump()

    RunRepository(db).create_run(
        run_id=run_id,
        source_env=trigger.source_env,
        target_env=trigger.target_env,
        config_snapshot=config_snapshot or None,
        selection_id=selection_id,
        selection_version=version.version_number,
    )
    AuditService(db).log(
        request, "selection.launched", "job_selection", selection_id,
        {
            "run_id": run_id, "source_env": trigger.source_env,
            "target_env": trigger.target_env, "version": version.version_number,
        },
    )
    background_tasks.add_task(
        _execute_run, run_id, ordered_jobs, trigger.source_env, trigger.target_env,
        trigger.run_settings, config_snapshot,
    )
    return RunStatusOut(run_id=run_id, status="PENDING")
