from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.schemas import (
    AdapterTestOut,
    AutomicJobStatusOut,
    AutomicJobCreateRequest,
    AutomicLookupRequest,
    BODocOut,
    BOJobCreateRequest,
    BOReportOut,
    JobDefinition,
    BOTestRequest,
)
from api.services.adapter_service import AdapterService
from etl_framework.repository.repository import ConfigRepository, JobRepository
from api.services.audit_service import AuditService

router = APIRouter(tags=["adapters"])

_MIME_MAP = {
    "pdf":  "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv":  "text/csv",
}
_EXT_MAP = {"pdf": "pdf", "xlsx": "xlsx", "csv": "csv"}


def get_adapter_service(db: Session = Depends(get_session)) -> AdapterService:
    return AdapterService(ConfigRepository(db))


# ---------------------------------------------------------------------------
# SAP BO
# ---------------------------------------------------------------------------

@router.post("/sap-bo/test", response_model=AdapterTestOut)
def test_bo_connection(
    body: BOTestRequest,
    service: AdapterService = Depends(get_adapter_service),
):
    return service.test_bo_connection(body.config_id)


@router.get("/sap-bo/documents", response_model=list[BODocOut])
def list_bo_documents(
    config_id: int,
    service: AdapterService = Depends(get_adapter_service),
):
    return service.list_bo_documents(config_id)


@router.get("/sap-bo/documents/{doc_id}/reports", response_model=list[BOReportOut])
def list_bo_reports(
    doc_id: str,
    config_id: int,
    service: AdapterService = Depends(get_adapter_service),
):
    return service.list_bo_reports(config_id, doc_id)


@router.get("/sap-bo/documents/{doc_id}/reports/{report_id}/download")
def download_bo_report(
    doc_id: str,
    report_id: str,
    config_id: int,
    format: str = "xlsx",
    service: AdapterService = Depends(get_adapter_service),
):
    content = service.download_bo_report(config_id, doc_id, report_id, fmt=format)
    mime = _MIME_MAP.get(format, "application/octet-stream")
    ext = _EXT_MAP.get(format, "bin")
    return Response(
        content=content,
        media_type=mime,
        headers={
            "Content-Disposition": f'attachment; filename="report_{doc_id}_{report_id}.{ext}"'
        },
    )


# ---------------------------------------------------------------------------
# Automic
# ---------------------------------------------------------------------------

@router.post("/automic/lookup", response_model=AutomicJobStatusOut)
def lookup_automic_job(
    body: AutomicLookupRequest,
    service: AdapterService = Depends(get_adapter_service),
):
    return service.lookup_automic_job(body.config_id, body.identifier, body.id_type)


# ---------------------------------------------------------------------------
# Job creation from adapters
# ---------------------------------------------------------------------------

@router.post("/jobs/from-bo-report", response_model=JobDefinition, status_code=201)
def create_job_from_bo_report(
    body: BOJobCreateRequest,
    request: Request,
    db: Session = Depends(get_session),
):
    job_data = {
        "name": body.name,
        "description": f"SAP BO Report: {body.title}",
        "tags": ["bo_report"],
        "job_type": "bo_report",
        "query": "",
        "key_columns": body.key_columns,
        "exclude_columns": [],
        "params": {
            "report_id": body.doc_id,
            "bo_report_id": body.report_id,
            "format": body.format,
        },
        "enabled": True,
    }
    JobRepository(db).upsert(job_data)
    AuditService(db).log(
        request, "job.created", "job", body.name,
        {"source": "sap_bo", "params": job_data["params"]},
    )
    return JobDefinition(**job_data)


@router.post("/jobs/from-automic", response_model=JobDefinition, status_code=201)
def create_job_from_automic(
    body: AutomicJobCreateRequest,
    request: Request,
    db: Session = Depends(get_session),
):
    identifier = body.job_name or body.run_id or ""
    params = {"job_name": body.job_name} if body.job_name else {"run_id": body.run_id}
    job_data = {
        "name": body.name,
        "description": f"Automic Job: {identifier}",
        "tags": ["automic_job"],
        "job_type": "automic_job",
        "query": "",
        "key_columns": [],
        "exclude_columns": [],
        "params": params,
        "enabled": True,
    }
    JobRepository(db).upsert(job_data)
    AuditService(db).log(
        request, "job.created", "job", body.name,
        {"source": "automic", "params": params},
    )
    return JobDefinition(**job_data)
