from __future__ import annotations

import base64
import binascii

from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.schemas import (
    AdapterTestOut,
    AutomicBulkImportRequest,
    AutomicBulkImportResponse,
    AutomicJobStatusOut,
    AutomicJobSummary,
    AutomicJobCreateRequest,
    AutomicLookupRequest,
    BODocOut,
    BODocRanOnOut,
    BOAuthSessionOut,
    BOJobCreateRequest,
    BOLogoffRequest,
    BOLogonRequest,
    BOParamOut,
    BOReportDownloadRequest,
    BOReportOut,
    JobDefinition,
    BOTestRequest,
    RestApiPreviewRequest,
    RestApiTestRequest,
    SAPDSJobCreateRequest,
    SAPDSJobStatusOut,
    SAPDSLookupRequest,
    SAPDSTestRequest,
)
from api.services.adapter_service import AdapterService, SAPBOAuthContext
from api.services.bo_archive import EXT_MAP as _EXT_MAP
from etl_framework.repository.repository import ConfigRepository, JobRepository, SettingsRepository
from api.services.audit_service import AuditService

router = APIRouter(tags=["adapters"])

_MIME_MAP = {
    "pdf":  "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv":  "text/csv",
}


def _download_response(result, doc_id: str, report_id: str, fmt: str) -> Response:
    """One Response builder for all four download routes.

    Both headers are percent-encoded: HTTP header values are latin-1, and a
    Windows share path or an OS error string can carry characters outside it.
    """
    mime = _MIME_MAP.get(fmt, "application/octet-stream")
    ext = _EXT_MAP.get(fmt, "bin")
    name = f"report_{doc_id}_{report_id}.{ext}" if report_id else f"report_{doc_id}.{ext}"
    headers = {"Content-Disposition": f'attachment; filename="{name}"'}
    if result.saved_path is not None:
        headers["X-Saved-Path"] = quote(str(result.saved_path))
    if result.save_error:
        headers["X-Save-Error"] = quote(result.save_error)
    return Response(content=result.content, media_type=mime, headers=headers)


def get_adapter_service(db: Session = Depends(get_session)) -> AdapterService:
    return AdapterService(ConfigRepository(db))


def _sap_bo_auth_from_request(request: Request, auth_type: str | None = None) -> SAPBOAuthContext | None:
    token = (request.headers.get("x-sap-logontoken") or "").strip()
    if token:
        return SAPBOAuthContext(scheme="x-sap-logontoken", token=token, auth_type=auth_type)

    auth = (request.headers.get("authorization") or "").strip()
    if not auth.lower().startswith("basic "):
        return None

    raw = auth[6:].strip()
    try:
        decoded = base64.b64decode(raw, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=401,
            detail="Invalid SAP BO Basic Authorization header",
            headers={"WWW-Authenticate": "Basic"},
        ) from exc
    if ":" not in decoded:
        raise HTTPException(
            status_code=401,
            detail="Invalid SAP BO Basic Authorization header",
            headers={"WWW-Authenticate": "Basic"},
        )
    username, password = decoded.split(":", 1)
    if not username:
        raise HTTPException(
            status_code=401,
            detail="SAP BO Basic username is required",
            headers={"WWW-Authenticate": "Basic"},
        )
    return SAPBOAuthContext(
        scheme="basic",
        username=username,
        password=password,
        auth_type=auth_type,
    )


# ---------------------------------------------------------------------------
# SAP BO
# ---------------------------------------------------------------------------

@router.post("/sap-bo/logon", response_model=BOAuthSessionOut)
def logon_bo_session(
    body: BOLogonRequest,
    request: Request,
    service: AdapterService = Depends(get_adapter_service),
):
    return service.create_bo_session(
        body.config_id,
        _sap_bo_auth_from_request(request, auth_type=body.auth_type),
    )


@router.post("/sap-bo/logoff", response_model=BOAuthSessionOut)
def logoff_bo_session(
    body: BOLogoffRequest,
    request: Request,
    service: AdapterService = Depends(get_adapter_service),
):
    token = (request.headers.get("x-sap-logontoken") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="X-SAP-LogonToken header is required")
    return service.logoff_bo_session(body.config_id, token)


@router.post("/sap-bo/test", response_model=AdapterTestOut)
def test_bo_connection(
    body: BOTestRequest,
    request: Request,
    service: AdapterService = Depends(get_adapter_service),
):
    return service.test_bo_connection(body.config_id, _sap_bo_auth_from_request(request))


@router.get("/sap-bo/documents", response_model=list[BODocOut])
def list_bo_documents(
    config_id: int,
    request: Request,
    service: AdapterService = Depends(get_adapter_service),
):
    return service.list_bo_documents(config_id, _sap_bo_auth_from_request(request))


@router.get("/sap-bo/documents/ran-on", response_model=BODocRanOnOut)
def list_bo_document_ids_with_runs_on(
    config_id: int,
    run_date: date,
    request: Request,
    service: AdapterService = Depends(get_adapter_service),
):
    return service.list_bo_document_ids_with_runs_on(config_id, run_date, _sap_bo_auth_from_request(request))


@router.get("/sap-bo/documents/{doc_id}/reports", response_model=list[BOReportOut])
def list_bo_reports(
    doc_id: str,
    config_id: int,
    request: Request,
    service: AdapterService = Depends(get_adapter_service),
):
    return service.list_bo_reports(config_id, doc_id, _sap_bo_auth_from_request(request))


@router.get("/sap-bo/documents/{doc_id}/parameters", response_model=list[BOParamOut])
def list_bo_document_parameters(
    doc_id: str,
    config_id: int,
    request: Request,
    service: AdapterService = Depends(get_adapter_service),
):
    return service.get_bo_document_parameters(config_id, doc_id, _sap_bo_auth_from_request(request))


@router.get("/sap-bo/documents/{doc_id}/download")
def download_whole_bo_document(
    doc_id: str,
    config_id: int,
    request: Request,
    format: str = "xlsx",
    db: Session = Depends(get_session),
    service: AdapterService = Depends(get_adapter_service),
):
    """SAP's primary step 5: every tab of the document in one file. Naming no
    report is the whole point, so this cannot be folded into the report-scoped
    route below — a path segment always names a tab."""
    result = service.download_bo_report(
        config_id,
        doc_id,
        "",
        fmt=format,
        auth=_sap_bo_auth_from_request(request),
        download_dir=SettingsRepository(db).get_bo_download_dir(),
    )
    return _download_response(result, doc_id, "", format)


@router.post("/sap-bo/documents/{doc_id}/download")
def download_whole_bo_document_with_parameters(
    doc_id: str,
    config_id: int,
    body: BOReportDownloadRequest,
    request: Request,
    db: Session = Depends(get_session),
    service: AdapterService = Depends(get_adapter_service),
):
    settings = SettingsRepository(db)
    result = service.download_bo_report(
        config_id,
        doc_id,
        "",
        fmt=body.format,
        auth=_sap_bo_auth_from_request(request),
        parameters=[p.model_dump() for p in body.parameters],
        timezone=settings.get_timezone(),
        download_dir=settings.get_bo_download_dir(),
    )
    return _download_response(result, doc_id, "", body.format)


@router.get("/sap-bo/documents/{doc_id}/reports/{report_id}/download")
def download_bo_report(
    doc_id: str,
    report_id: str,
    config_id: int,
    request: Request,
    format: str = "xlsx",
    db: Session = Depends(get_session),
    service: AdapterService = Depends(get_adapter_service),
):
    result = service.download_bo_report(
        config_id,
        doc_id,
        report_id,
        fmt=format,
        auth=_sap_bo_auth_from_request(request),
        download_dir=SettingsRepository(db).get_bo_download_dir(),
    )
    return _download_response(result, doc_id, report_id, format)


@router.post("/sap-bo/documents/{doc_id}/reports/{report_id}/download")
def download_bo_report_with_parameters(
    doc_id: str,
    report_id: str,
    config_id: int,
    body: BOReportDownloadRequest,
    request: Request,
    db: Session = Depends(get_session),
    service: AdapterService = Depends(get_adapter_service),
):
    settings = SettingsRepository(db)
    result = service.download_bo_report(
        config_id,
        doc_id,
        report_id,
        fmt=body.format,
        auth=_sap_bo_auth_from_request(request),
        parameters=[p.model_dump() for p in body.parameters],
        timezone=settings.get_timezone(),
        download_dir=settings.get_bo_download_dir(),
    )
    return _download_response(result, doc_id, report_id, body.format)


# ---------------------------------------------------------------------------
# Automic
# ---------------------------------------------------------------------------

@router.post("/automic/lookup", response_model=AutomicJobStatusOut)
def lookup_automic_job(
    body: AutomicLookupRequest,
    service: AdapterService = Depends(get_adapter_service),
):
    return service.lookup_automic_job(body.config_id, body.identifier, body.id_type)


@router.get("/automic/search", response_model=list[AutomicJobSummary])
def search_automic_jobs(
    config_id: int,
    filter: str,
    service: AdapterService = Depends(get_adapter_service),
):
    return service.search_automic_jobs(config_id, filter)


# ---------------------------------------------------------------------------
# SAP Data Services (SAP DS)
# ---------------------------------------------------------------------------

@router.post("/sap-ds/test", response_model=AdapterTestOut)
def test_ds_connection(
    body: SAPDSTestRequest,
    service: AdapterService = Depends(get_adapter_service),
):
    return service.test_ds_connection(body.config_id)


@router.post("/sap-ds/lookup", response_model=SAPDSJobStatusOut)
def lookup_ds_job(
    body: SAPDSLookupRequest,
    service: AdapterService = Depends(get_adapter_service),
):
    return service.lookup_ds_job(body.config_id, body.identifier, body.id_type, body.repository)


# ---------------------------------------------------------------------------
# REST API endpoints
# ---------------------------------------------------------------------------

@router.post("/rest-api/test", response_model=AdapterTestOut)
def test_rest_api_endpoint(
    body: RestApiTestRequest,
    service: AdapterService = Depends(get_adapter_service),
):
    return service.test_api_endpoint(body.config_id, body.endpoint_name)


@router.post("/rest-api/preview")
def preview_rest_api_endpoint(
    body: RestApiPreviewRequest,
    service: AdapterService = Depends(get_adapter_service),
):
    return service.preview_api_endpoint(body.config_id, body.endpoint_name, body.limit)


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
            "bo_parameters": [p.model_dump() for p in body.parameters],
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


@router.post("/jobs/from-automic/bulk", response_model=AutomicBulkImportResponse, status_code=201)
def bulk_create_jobs_from_automic(
    body: AutomicBulkImportRequest,
    request: Request,
    db: Session = Depends(get_session),
):
    imported = []
    errors: dict[str, str] = {}
    for job_name in body.job_names:
        slug = job_name.lower().replace(" ", "_")
        job_data = {
            "name": slug,
            "description": f"Automic Job: {job_name}",
            "tags": ["automic_job"],
            "job_type": "automic_job",
            "query": "",
            "key_columns": [],
            "exclude_columns": [],
            "params": {"job_name": job_name},
            "enabled": True,
        }
        try:
            JobRepository(db).upsert(job_data)
            AuditService(db).log(
                request, "job.created", "job", slug,
                {"source": "automic_browse", "params": {"job_name": job_name}},
            )
            imported.append(JobDefinition(**job_data))
        except Exception as exc:
            errors[job_name] = str(exc)
    return AutomicBulkImportResponse(imported=imported, errors=errors)


@router.post("/jobs/from-sap-ds", response_model=JobDefinition, status_code=201)
def create_job_from_sap_ds(
    body: SAPDSJobCreateRequest,
    request: Request,
    db: Session = Depends(get_session),
):
    job_data = {
        "name": body.name,
        "description": f"SAP DS Job: {body.job_name}",
        "tags": ["ds_job"],
        "job_type": "ds_job",
        "query": "",
        "key_columns": [],
        "exclude_columns": [],
        "params": {
            "job_name": body.job_name,
            "repository": body.repository or "",
            "poll_interval_s": body.poll_interval_s,
            "timeout_s": body.timeout_s,
        },
        "enabled": True,
    }
    JobRepository(db).upsert(job_data)
    AuditService(db).log(
        request, "job.created", "job", body.name,
        {"source": "sap_ds", "params": job_data["params"]},
    )
    return JobDefinition(**job_data)
