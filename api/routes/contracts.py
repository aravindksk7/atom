"""REST API routes for Data Contracts."""
from __future__ import annotations
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.dependencies import get_session
from etl_framework.repository.contract_repository import ContractRepository

router = APIRouter(tags=["contracts"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class ContractCreate(BaseModel):
    name: str
    source_job: str
    owner: str
    sla_hours: float
    consumers: list[str] = []
    breach_severity: str = "error"
    version: str = "1.0"


class ContractUpdate(BaseModel):
    owner: str | None = None
    sla_hours: float | None = None
    consumers: list[str] | None = None
    breach_severity: str | None = None


class ContractOut(BaseModel):
    id: int
    name: str
    version: str
    source_job: str
    owner: str
    sla_hours: float
    consumers: list[str]
    breach_severity: str
    active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_orm(cls, c) -> "ContractOut":
        consumers = c.consumers
        if isinstance(consumers, str):
            try:
                consumers = json.loads(consumers)
            except Exception:
                consumers = []
        return cls(
            id=c.id,
            name=c.name,
            version=c.version,
            source_job=c.source_job,
            owner=c.owner,
            sla_hours=c.sla_hours,
            consumers=consumers,
            breach_severity=c.breach_severity,
            active=c.active,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )


class BreachOut(BaseModel):
    id: int
    contract_id: int
    run_id: str
    breach_type: str
    opened_at: datetime
    resolved_at: datetime | None = None
    resolution_run_id: str | None = None
    escalated: bool
    escalated_at: datetime | None = None
    duration_hours: float | None = None


class VersionOut(BaseModel):
    id: int
    contract_id: int
    version: str
    bump_type: str
    note: str | None = None
    bumped_at: datetime


class BumpRequest(BaseModel):
    bump_type: str = "minor"  # "minor" or "major"
    note: str | None = None


# ---------------------------------------------------------------------------
# CRUD endpoints
# ---------------------------------------------------------------------------

@router.get("", response_model=list[ContractOut])
def list_contracts(db: Session = Depends(get_session)):
    return [ContractOut.from_orm(c) for c in ContractRepository(db).list()]


@router.post("", response_model=ContractOut, status_code=201)
def create_contract(body: ContractCreate, db: Session = Depends(get_session)):
    try:
        contract = ContractRepository(db).create(body.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return ContractOut.from_orm(contract)


@router.get("/{name}", response_model=ContractOut)
def get_contract(name: str, db: Session = Depends(get_session)):
    contract = ContractRepository(db).get(name)
    if contract is None:
        raise HTTPException(status_code=404, detail=f"Contract '{name}' not found")
    return ContractOut.from_orm(contract)


@router.put("/{name}", response_model=ContractOut)
def update_contract(name: str, body: ContractUpdate, db: Session = Depends(get_session)):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    contract = ContractRepository(db).update(name, **updates)
    if contract is None:
        raise HTTPException(status_code=404, detail=f"Contract '{name}' not found")
    return ContractOut.from_orm(contract)


@router.delete("/{name}", status_code=204)
def delete_contract(name: str, db: Session = Depends(get_session)):
    deleted = ContractRepository(db).delete(name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Contract '{name}' not found")


# ---------------------------------------------------------------------------
# Status and breach history
# ---------------------------------------------------------------------------

@router.get("/{name}/status")
def get_status(name: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    status = ContractRepository(db).get_status(name)
    if status["status"] == "NOT_FOUND":
        raise HTTPException(status_code=404, detail=f"Contract '{name}' not found")
    return status


@router.get("/{name}/breaches", response_model=list[BreachOut])
def list_breaches(name: str, db: Session = Depends(get_session)):
    repo = ContractRepository(db)
    contract = repo.get(name)
    if contract is None:
        raise HTTPException(status_code=404, detail=f"Contract '{name}' not found")
    return repo.list_breaches(contract.id)


# ---------------------------------------------------------------------------
# Version management
# ---------------------------------------------------------------------------

@router.get("/{name}/versions", response_model=list[VersionOut])
def list_versions(name: str, db: Session = Depends(get_session)):
    repo = ContractRepository(db)
    if repo.get(name) is None:
        raise HTTPException(status_code=404, detail=f"Contract '{name}' not found")
    return repo.list_versions(name)


@router.post("/{name}/bump", response_model=VersionOut)
def bump_version(name: str, body: BumpRequest, db: Session = Depends(get_session)):
    try:
        return ContractRepository(db).bump_version(name, body.bump_type, body.note)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ---------------------------------------------------------------------------
# Derived: DQ rules + schema from source_job
# ---------------------------------------------------------------------------

@router.get("/{name}/rules")
def get_rules(name: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    repo = ContractRepository(db)
    contract = repo.get(name)
    if contract is None:
        raise HTTPException(status_code=404, detail=f"Contract '{name}' not found")

    from etl_framework.repository.repository import JobRepository
    job = JobRepository(db).get(contract.source_job)
    if job is None:
        return {"contract": name, "source_job": contract.source_job, "rules": []}

    rules = []
    params = job.params or {}
    if "null_check_columns" in params:
        for col in params["null_check_columns"]:
            rules.append({"type": "not_null", "column": col})
    if "key_columns" in (job.__dict__ if hasattr(job, "__dict__") else {}):
        for col in (job.key_columns or []):
            rules.append({"type": "unique_key", "column": col})
    rules.append({"type": "row_count_non_zero", "source_job": contract.source_job})

    return {"contract": name, "source_job": contract.source_job, "rules": rules}


@router.get("/{name}/schema")
def get_schema(name: str, environment: str = "both", db: Session = Depends(get_session)) -> dict[str, Any]:
    repo = ContractRepository(db)
    contract = repo.get(name)
    if contract is None:
        raise HTTPException(status_code=404, detail=f"Contract '{name}' not found")

    from etl_framework.repository.repository import SchemaSnapshotRepository
    snapshot = SchemaSnapshotRepository(db).get_latest(contract.source_job, environment)
    if snapshot is None:
        return {"contract": name, "source_job": contract.source_job, "columns": [], "captured_at": None}

    columns = snapshot.columns
    if isinstance(columns, str):
        try:
            columns = json.loads(columns)
        except Exception:
            columns = []

    return {
        "contract": name,
        "source_job": contract.source_job,
        "columns": columns,
        "captured_at": snapshot.captured_at.isoformat() if snapshot.captured_at else None,
    }
