from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.schemas import (
    ExecutionSequenceCreate,
    ExecutionSequenceDetailOut,
    ExecutionSequenceOut,
    ExecutionSequenceUpdate,
    ExecutionSequenceVersionCreate,
    ExecutionSequenceVersionOut,
    SequenceUsageOut,
    SequenceValidateRequest,
    SequenceValidateResponse,
)
from api.services.audit_service import AuditService
from api.services.sequence_validation import (
    SequenceCycleError,
    topological_order,
    validate_steps,
)
from etl_framework.repository.repository import JobRepository
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
