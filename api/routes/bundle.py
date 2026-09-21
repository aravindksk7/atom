from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.dependencies import get_session, is_admin_request
from api.services.audit_service import AuditService
from api.services.config_bundle import ENTITY_TYPES, apply_bundle, build_bundle
from etl_framework.repository.repository import (
    ConfigRepository,
    FileServerProfileRepository,
    JobRepository,
    JobSelectionRepository,
)
from etl_framework.repository.sequence_repository import ExecutionSequenceRepository

router = APIRouter(tags=["bundle"])


class BundleListItem(BaseModel):
    type: str
    name: str


class BundleExportRequest(BaseModel):
    selection: dict[str, list[str]]


class BundleItemOut(BaseModel):
    type: str
    name: str
    status: str
    reason: str | None = None


@router.get("/list", response_model=list[BundleListItem])
def list_bundle_items(db: Session = Depends(get_session)):
    items: list[BundleListItem] = []
    items += [BundleListItem(type="file_servers", name=p.name) for p in FileServerProfileRepository(db).list()]
    items += [BundleListItem(type="configs", name=c.name) for c in ConfigRepository(db).list()]
    items += [BundleListItem(type="jobs", name=j.name) for j in JobRepository(db).list()]
    items += [BundleListItem(type="sequences", name=s.name) for s in ExecutionSequenceRepository(db).list()]
    items += [BundleListItem(type="selections", name=s.name) for s in JobSelectionRepository(db).list()]
    return items


@router.post("/export")
def export_bundle(body: BundleExportRequest, db: Session = Depends(get_session)):
    unknown = sorted(set(body.selection) - set(ENTITY_TYPES))
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown entity type(s): {unknown}")
    return build_bundle(db, body.selection)


@router.post("/import", response_model=list[BundleItemOut])
def import_bundle(body: dict, request: Request, db: Session = Depends(get_session)):
    try:
        results = apply_bundle(db, body, is_admin=is_admin_request(request))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    AuditService(db).log(request, "bundle.imported", "bundle", None, {"summary": counts})
    return [BundleItemOut(**r.to_dict()) for r in results]
