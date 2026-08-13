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
    SequenceRef,
)
from api.routes.runs import _execute_run, _snapshot_from_trigger
from api.services.audit_service import AuditService
from api.services.job_env_validation import (
    SINGLE_ENV_JOB_TYPES as _SINGLE_ENV_JOB_TYPES,  # noqa: F401 — back-compat re-export
    job_name_of as _job_name_of,  # noqa: F401 — back-compat re-export
    validate_env_requirements as _validate_env_requirements,
)
from api.services.sequence_resolver import SequenceResolutionError, resolve as resolve_sequence
from api.services.sequence_preconditions import check_for_session as check_preconditions
from etl_framework.repository.repository import ConfigRepository, JobRepository, JobSelectionRepository, RunRepository
from etl_framework.repository.sequence_repository import ExecutionSequenceRepository

router = APIRouter(tags=["selections"])


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
        config_id=version.config_id,
        sequence_ref=version.sequence_ref,
        created_at=version.created_at,
    )


def _validate_config_id_or_404(config_id: int | None, db: Session) -> None:
    if config_id is not None and ConfigRepository(db).get(config_id) is None:
        raise HTTPException(status_code=404, detail="Config not found")


def _validate_sequence_ref_or_404(ref: SequenceRef | None, db: Session) -> dict | None:
    if ref is None:
        return None
    repo = ExecutionSequenceRepository(db)
    if repo.get(ref.sequence_id) is None:
        raise HTTPException(status_code=404, detail="Execution sequence not found")
    if ref.sequence_version is not None and repo.get_version(ref.sequence_id, ref.sequence_version) is None:
        raise HTTPException(status_code=404, detail="Execution sequence version not found")
    return ref.model_dump()


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
    _validate_config_id_or_404(body.config_id, db)
    sequence_ref = _validate_sequence_ref_or_404(body.sequence_ref, db)
    job_sequence = _dump_job_sequence(body.job_sequence)
    selection = repo.create(
        name=body.name, description=body.description, tags=body.tags,
        job_sequence=job_sequence, run_settings=body.run_settings.model_dump(),
        config_id=body.config_id, sequence_ref=sequence_ref,
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

    config_id_set = "config_id" in body.model_fields_set
    sequence_ref_set = "sequence_ref" in body.model_fields_set
    if body.job_sequence is not None or body.run_settings is not None or config_id_set or sequence_ref_set:
        if config_id_set:
            _validate_config_id_or_404(body.config_id, db)
        job_sequence = _dump_job_sequence(body.job_sequence) if body.job_sequence is not None else None
        run_settings = body.run_settings.model_dump() if body.run_settings is not None else None
        version_kwargs = {}
        if config_id_set:
            version_kwargs["config_id"] = body.config_id
        if sequence_ref_set:
            version_kwargs["sequence_ref"] = _validate_sequence_ref_or_404(body.sequence_ref, db)
        repo.create_new_version(selection_id, job_sequence, run_settings, **version_kwargs)

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

    resolved = None
    if version.sequence_ref:
        try:
            resolved = resolve_sequence(db, SequenceRef.model_validate(version.sequence_ref))
        except SequenceResolutionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        job_sequence = resolved.as_linear_steps()   # flat shape, for env validation + snapshot
        dag_steps = resolved.steps                  # real graph, for execution
        gate = check_preconditions(db, resolved.preconditions)
        if not gate.ok:
            raise HTTPException(status_code=422, detail=gate.reason)
    else:
        job_sequence = version.job_sequence or []

    jobs_by_name = {j.name: j for j in JobRepository(db).list()}
    _validate_env_requirements(job_sequence, jobs_by_name, body.target_env)

    trigger = RunTrigger(
        source_env=body.source_env,
        target_env=body.target_env,
        source_connection=body.source_connection,
        target_connection=body.target_connection,
        job_sequence=job_sequence,
        # The selection remembers its own config (saved on the selection so
        # launching doesn't require re-picking one every time); an explicit
        # config_id on the launch request overrides it for a one-off run.
        config_id=body.config_id if body.config_id is not None else version.config_id,
        config_data=body.config_data,
        run_settings=version.run_settings_json or {},
    )

    run_id = str(uuid.uuid4())
    ordered_jobs = trigger.job_sequence
    config_snapshot = _snapshot_from_trigger(trigger, db)
    config_snapshot["job_sequence"] = _dump_job_sequence(ordered_jobs)
    config_snapshot["run_settings"] = trigger.run_settings.model_dump()
    if resolved is not None:
        config_snapshot["sequence"] = resolved.snapshot_meta()

    RunRepository(db).create_run(
        run_id=run_id,
        source_env=trigger.source_env,
        target_env=trigger.target_env,
        config_snapshot=config_snapshot or None,
        selection_id=selection_id,
        selection_version=version.version_number,
        ci_context=body.ci_context,
    )
    AuditService(db).log(
        request, "selection.launched", "job_selection", selection_id,
        {
            "run_id": run_id, "source_env": trigger.source_env,
            "target_env": trigger.target_env, "version": version.version_number,
        },
    )
    background_tasks.add_task(
        _execute_run, run_id, dag_steps if resolved is not None else ordered_jobs,
        trigger.source_env, trigger.target_env, trigger.run_settings, config_snapshot,
    )
    return RunStatusOut(run_id=run_id, status="PENDING")
