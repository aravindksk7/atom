from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.schemas import (
    ExecutionSequenceCreate,
    ExecutionSequenceDetailOut,
    ExecutionSequenceOut,
    ExecutionSequenceUpdate,
    ExecutionSequenceVersionCreate,
    ExecutionSequenceVersionOut,
    RunStatusOut,
    RunTrigger,
    RunBatchOut,
    SequenceLaunchRequest,
    SequenceBatchLaunchRequest,
    SequenceRef,
    SequenceUsageOut,
    SequenceValidateRequest,
    SequenceValidateResponse,
)
from api.routes.selections import _dump_job_sequence
from api.routes.runs import _execute_run, _snapshot_from_trigger
from api.services.audit_service import AuditService
from api.services.job_env_validation import validate_env_requirements
from api.services.sequence_preconditions import check_for_session as check_preconditions
from api.services.sequence_resolver import SequenceResolutionError, resolve as resolve_sequence
from api.services.sequence_validation import (
    SequenceCycleError,
    topological_order,
    validate_steps,
)
from api.services.batch_launch import run_batch, validate_batch_variable
from etl_framework.repository.database import SessionLocal
from etl_framework.repository.repository import JobRepository, RunBatchRepository, RunRepository
from etl_framework.repository.sequence_repository import ExecutionSequenceRepository

router = APIRouter(tags=["sequences"])


def _known_job_names(db: Session) -> set[str]:
    return {j.name for j in JobRepository(db).list() if j.enabled}


def _check_or_422(db: Session, steps, preconditions) -> None:
    errors = validate_steps(steps, _known_job_names(db))
    if errors:
        raise HTTPException(status_code=422, detail=errors)


def _dump(models) -> list:
    return [m.model_dump() for m in models]


def _version_out(version) -> ExecutionSequenceVersionOut:
    return ExecutionSequenceVersionOut(
        version_number=version.version_number,
        steps=version.steps_json or [],
        preconditions=version.preconditions_json,
        defaults=version.defaults_json or {},
        created_at=version.created_at,
    )


def _sequence_out(sequence) -> ExecutionSequenceOut:
    latest = sequence.versions[-1] if sequence.versions else None
    return ExecutionSequenceOut(
        id=sequence.id,
        name=sequence.name,
        description=sequence.description,
        tags=sequence.tags or [],
        archived=sequence.archived,
        latest_version=latest.version_number if latest else 0,
        step_count=len(latest.steps_json or []) if latest else 0,
        created_at=sequence.created_at,
        updated_at=sequence.updated_at,
    )


def _detail_out(sequence) -> ExecutionSequenceDetailOut:
    return ExecutionSequenceDetailOut(
        **_sequence_out(sequence).model_dump(),
        versions=[_version_out(v) for v in sequence.versions],
    )


def _get_or_404(db: Session, sequence_id: int):
    sequence = ExecutionSequenceRepository(db).get(sequence_id)
    if sequence is None:
        raise HTTPException(status_code=404, detail="Execution sequence not found")
    return sequence


@router.get("", response_model=list[ExecutionSequenceOut])
def list_sequences(
    include_archived: bool = Query(False), db: Session = Depends(get_session)
):
    return [
        _sequence_out(s)
        for s in ExecutionSequenceRepository(db).list(include_archived=include_archived)
    ]


@router.post("", response_model=ExecutionSequenceOut, status_code=201)
def create_sequence(
    body: ExecutionSequenceCreate, request: Request, db: Session = Depends(get_session)
):
    repo = ExecutionSequenceRepository(db)
    if repo.get_by_name(body.name) is not None:
        raise HTTPException(
            status_code=409, detail="An execution sequence with this name already exists"
        )
    _check_or_422(db, body.steps, body.preconditions)
    sequence = repo.create(
        name=body.name, description=body.description, tags=body.tags,
        steps=_dump(body.steps),
        preconditions=body.preconditions.model_dump() if body.preconditions else None,
        defaults=body.defaults.model_dump(),
    )
    AuditService(db).log(
        request, "sequence.created", "execution_sequence", sequence.id,
        {"name": sequence.name, "step_count": len(body.steps)},
    )
    return _sequence_out(sequence)


# Registered before /{sequence_id} so "validate" is never read as an id.
@router.post("/validate", response_model=SequenceValidateResponse)
def validate_sequence(body: SequenceValidateRequest, db: Session = Depends(get_session)):
    errors = validate_steps(body.steps, _known_job_names(db))
    if errors:
        return SequenceValidateResponse(ok=False, errors=errors, order=[])
    try:
        order = topological_order(body.steps)
    except SequenceCycleError as exc:  # pragma: no cover — validate_steps catches this first
        return SequenceValidateResponse(
            ok=False, errors=[{"step_id": None, "field": "depends_on", "message": str(exc)}], order=[]
        )
    return SequenceValidateResponse(ok=True, errors=[], order=order)


@router.get("/{sequence_id}", response_model=ExecutionSequenceDetailOut)
def get_sequence(sequence_id: int, db: Session = Depends(get_session)):
    return _detail_out(_get_or_404(db, sequence_id))


@router.patch("/{sequence_id}", response_model=ExecutionSequenceDetailOut)
def update_sequence(
    sequence_id: int, body: ExecutionSequenceUpdate, request: Request,
    db: Session = Depends(get_session),
):
    repo = ExecutionSequenceRepository(db)
    _get_or_404(db, sequence_id)
    if body.name is not None:
        clash = repo.get_by_name(body.name)
        if clash is not None and clash.id != sequence_id:
            raise HTTPException(
                status_code=409, detail="An execution sequence with this name already exists"
            )
    sequence = repo.update_metadata(
        sequence_id, name=body.name, description=body.description,
        tags=body.tags, archived=body.archived,
    )
    AuditService(db).log(
        request, "sequence.updated", "execution_sequence", sequence_id, {"name": sequence.name}
    )
    return _detail_out(sequence)


@router.delete("/{sequence_id}", status_code=204)
def archive_sequence(sequence_id: int, request: Request, db: Session = Depends(get_session)):
    _get_or_404(db, sequence_id)
    try:
        ExecutionSequenceRepository(db).archive_or_raise(sequence_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    AuditService(db).log(request, "sequence.archived", "execution_sequence", sequence_id)


@router.get("/{sequence_id}/versions", response_model=list[ExecutionSequenceVersionOut])
def list_sequence_versions(sequence_id: int, db: Session = Depends(get_session)):
    return [_version_out(v) for v in _get_or_404(db, sequence_id).versions]


@router.post("/{sequence_id}/versions", response_model=ExecutionSequenceVersionOut, status_code=201)
def create_sequence_version(
    sequence_id: int, body: ExecutionSequenceVersionCreate, request: Request,
    db: Session = Depends(get_session),
):
    _get_or_404(db, sequence_id)
    _check_or_422(db, body.steps, body.preconditions)
    version = ExecutionSequenceRepository(db).create_new_version(
        sequence_id, steps=_dump(body.steps),
        preconditions=body.preconditions.model_dump() if body.preconditions else None,
        defaults=body.defaults.model_dump() if body.defaults is not None else None,
    )
    AuditService(db).log(
        request, "sequence.version_created", "execution_sequence", sequence_id,
        {"version": version.version_number},
    )
    return _version_out(version)


@router.get("/{sequence_id}/versions/{version_number}", response_model=ExecutionSequenceVersionOut)
def get_sequence_version(
    sequence_id: int, version_number: int, db: Session = Depends(get_session)
):
    _get_or_404(db, sequence_id)
    version = ExecutionSequenceRepository(db).get_version(sequence_id, version_number)
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")
    return _version_out(version)


@router.get("/{sequence_id}/usage", response_model=SequenceUsageOut)
def get_sequence_usage(sequence_id: int, db: Session = Depends(get_session)):
    _get_or_404(db, sequence_id)
    return SequenceUsageOut(**ExecutionSequenceRepository(db).usage(sequence_id))


@router.post("/{sequence_id}/launch", response_model=RunStatusOut, status_code=202)
def launch_sequence(
    sequence_id: int,
    body: SequenceLaunchRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_session),
):
    run_id = _do_launch_sequence(sequence_id, body, background_tasks, request, db)
    return RunStatusOut(run_id=run_id, status="PENDING")


def _do_launch_sequence(
    sequence_id: int,
    body: SequenceLaunchRequest,
    background_tasks: BackgroundTasks | None,
    request: Request | None,
    db: Session,
    run_batch_id: str | None = None,
) -> str:
    try:
        resolved = resolve_sequence(
            db, SequenceRef(sequence_id=sequence_id, sequence_version=body.version)
        )
    except SequenceResolutionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    gate = check_preconditions(db, resolved.preconditions)
    if not gate.ok:
        raise HTTPException(status_code=422, detail=gate.reason)

    source_env = body.source_env or resolved.defaults.source_env
    if not source_env:
        raise HTTPException(
            status_code=422,
            detail="source_env is required: no value was given and the sequence has no default.",
        )
    target_env = body.target_env or resolved.defaults.target_env or ""
    config_id = body.config_id if body.config_id is not None else resolved.defaults.config_id
    run_settings = resolved.defaults.run_settings or {}

    job_sequence = resolved.as_linear_steps()
    jobs_by_name = {j.name: j for j in JobRepository(db).list()}
    validate_env_requirements(job_sequence, jobs_by_name, target_env)

    trigger = RunTrigger(
        source_env=source_env,
        target_env=target_env,
        source_connection=body.source_connection,
        target_connection=body.target_connection,
        job_sequence=job_sequence,
        config_id=config_id,
        config_data=body.config_data,
        run_settings=run_settings,
        variable_overrides=body.variable_overrides,
    )

    run_id = str(uuid.uuid4())
    config_snapshot = _snapshot_from_trigger(trigger, db)
    config_snapshot["job_sequence"] = _dump_job_sequence(trigger.job_sequence)
    config_snapshot["run_settings"] = trigger.run_settings.model_dump()
    config_snapshot["sequence"] = resolved.snapshot_meta()

    RunRepository(db).create_run(
        run_id=run_id,
        source_env=trigger.source_env,
        target_env=trigger.target_env,
        config_snapshot=config_snapshot or None,
        ci_context=body.ci_context,
        run_batch_id=run_batch_id,
    )
    if request is not None:
        AuditService(db).log(
            request, "sequence.launched", "execution_sequence", sequence_id,
            {
                "run_id": run_id, "source_env": trigger.source_env,
                "target_env": trigger.target_env, "version": resolved.version_number,
            },
        )
    args = (run_id, resolved.steps, trigger.source_env, trigger.target_env, trigger.run_settings, config_snapshot)
    if background_tasks is None:
        _execute_run(*args)
    else:
        background_tasks.add_task(_execute_run, *args)
    return run_id


@router.post("/{sequence_id}/launch-batch", response_model=RunBatchOut, status_code=202)
def launch_sequence_batch(
    sequence_id: int,
    body: SequenceBatchLaunchRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_session),
):
    validate_batch_variable(db, body.batch.variable_name)
    probe = SequenceLaunchRequest(**body.model_dump(exclude={"batch"}))
    _validate_sequence_launch(sequence_id, probe, db)
    batch_id = str(uuid.uuid4())
    batch = RunBatchRepository(db).create(
        batch_id=batch_id,
        target_type="sequence",
        target_id=sequence_id,
        variable_name=body.batch.variable_name,
        start_value=body.batch.start_value.isoformat(),
        iterations=body.batch.iterations,
        step_days=body.batch.step_days,
        weekend_policy=body.batch.weekend_policy,
        stop_on_failure=body.batch.stop_on_failure,
    )

    launch_body = body.model_dump(exclude={"batch"})

    def _launch(iter_db: Session, target_id: int, overrides: dict[str, str]) -> str:
        merged = {**launch_body.get("variable_overrides", {}), **overrides}
        return _do_launch_sequence(
            target_id,
            SequenceLaunchRequest(**{**launch_body, "variable_overrides": merged}),
            None,
            None,
            iter_db,
            run_batch_id=batch_id,
        )

    background_tasks.add_task(run_batch, batch_id, SessionLocal, _launch)
    return _batch_out(db, batch)


def _validate_sequence_launch(sequence_id: int, body: SequenceLaunchRequest, db: Session) -> None:
    class _NoTasks:
        def add_task(self, *args, **kwargs):
            return None
    _do_launch_sequence(sequence_id, body, _NoTasks(), None, db)
    run = RunRepository(db).list_runs(limit=1)[0]
    db.delete(run)
    db.commit()


def _batch_out(db: Session, batch) -> RunBatchOut:
    runs = RunBatchRepository(db).member_runs(batch.batch_id)
    return RunBatchOut(
        **{k: getattr(batch, k) for k in (
            "batch_id", "target_type", "target_id", "status", "variable_name", "start_value",
            "iterations", "step_days", "weekend_policy", "stop_on_failure", "completed",
            "current_iteration", "current_value", "created_at", "completed_at",
        )},
        runs=[{"run_id": r.run_id, "status": r.status, "started_at": r.started_at, "completed_at": r.completed_at} for r in runs],
    )
