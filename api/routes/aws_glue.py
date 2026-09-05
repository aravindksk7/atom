from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.schemas import (
    GlueCatalogCompareOut,
    GlueCatalogCompareRequest,
    GlueConfigRequest,
    GlueDatabasesOut,
    GlueTableOut,
    GlueTableRequest,
    GlueTablesOut,
    GlueTablesRequest,
)
from api.services.audit_service import AuditService
from api.services.aws_glue_service import AwsGlueService
from etl_framework.repository.repository import ConfigRepository

router = APIRouter(tags=["aws-glue"])


class StartGlueJobRequest(BaseModel):
    config_id: int | str
    arguments: dict[str, str] | None = None


class RunGlueJobRequest(BaseModel):
    config_id: int | str
    arguments: dict[str, str] | None = None
    poll_interval_seconds: float = 2.0
    max_attempts: int = 120


def get_aws_glue_service(db: Session = Depends(get_session)) -> AwsGlueService:
    return AwsGlueService(ConfigRepository(db))


def _handle(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail={"error_type": type(exc).__name__, "message": str(exc)},
        ) from exc


@router.post("/databases", response_model=GlueDatabasesOut)
def glue_databases(
    body: GlueConfigRequest,
    request: Request,
    service: AwsGlueService = Depends(get_aws_glue_service),
    db: Session = Depends(get_session),
) -> GlueDatabasesOut:
    result = _handle(service.list_databases, body.config_id)
    AuditService(db).log(request, "aws_glue.check", "aws_glue", "databases", {"op": "databases"})
    return result


@router.post("/tables", response_model=GlueTablesOut)
def glue_tables(
    body: GlueTablesRequest,
    request: Request,
    service: AwsGlueService = Depends(get_aws_glue_service),
    db: Session = Depends(get_session),
) -> GlueTablesOut:
    result = _handle(service.list_tables, body.config_id, body.database)
    AuditService(db).log(request, "aws_glue.check", "aws_glue", body.database, {"op": "tables"})
    return result


@router.post("/table", response_model=GlueTableOut)
def glue_table(
    body: GlueTableRequest,
    request: Request,
    service: AwsGlueService = Depends(get_aws_glue_service),
    db: Session = Depends(get_session),
) -> GlueTableOut:
    result = _handle(service.describe_table, body.config_id, body.database, body.table)
    AuditService(db).log(request, "aws_glue.check", "aws_glue", body.database, {"op": "table", "table": body.table})
    return result


@router.post("/compare-tables", response_model=GlueCatalogCompareOut)
def glue_compare_tables(
    body: GlueCatalogCompareRequest,
    request: Request,
    service: AwsGlueService = Depends(get_aws_glue_service),
    db: Session = Depends(get_session),
) -> GlueCatalogCompareOut:
    result = _handle(
        service.compare_tables,
        body.config_id,
        body.source_database,
        body.source_table,
        body.target_database,
        body.target_table,
        body.compare_location,
        body.compare_formats,
        body.compare_partitions,
    )
    AuditService(db).log(
        request,
        "aws_glue.check",
        "aws_glue",
        body.source_database,
        {
            "op": "compare_tables",
            "source_table": body.source_table,
            "target_database": body.target_database,
            "target_table": body.target_table,
        },
    )
    return result


@router.get("/jobs")
def list_glue_jobs(
    request: Request,
    config_id: int | str = Query(...),
    service: AwsGlueService = Depends(get_aws_glue_service),
    db: Session = Depends(get_session),
) -> dict:
    jobs = _handle(service.list_jobs, config_id)
    AuditService(db).log(
        request,
        "aws_glue.check",
        "aws_glue_jobs",
        str(config_id),
        {"count": len(jobs)},
    )
    return {"jobs": jobs}


@router.get("/jobs/{job_name}")
def get_glue_job(
    job_name: str,
    request: Request,
    config_id: int | str = Query(...),
    service: AwsGlueService = Depends(get_aws_glue_service),
    db: Session = Depends(get_session),
) -> dict:
    return _handle(service.get_job, config_id, job_name)


@router.post("/jobs/{job_name}/start")
def start_glue_job(
    job_name: str,
    req: StartGlueJobRequest,
    request: Request,
    service: AwsGlueService = Depends(get_aws_glue_service),
    db: Session = Depends(get_session),
) -> dict:
    return _handle(service.start_job_run, req.config_id, job_name, req.arguments)


@router.get("/jobs/{job_name}/runs/{job_run_id}")
def get_glue_job_run(
    job_name: str,
    job_run_id: str,
    request: Request,
    config_id: int | str = Query(...),
    service: AwsGlueService = Depends(get_aws_glue_service),
    db: Session = Depends(get_session),
) -> dict:
    return _handle(service.get_job_run_status, config_id, job_name, job_run_id)


@router.post("/jobs/{job_name}/run")
def run_glue_job(
    job_name: str,
    req: RunGlueJobRequest,
    request: Request,
    service: AwsGlueService = Depends(get_aws_glue_service),
    db: Session = Depends(get_session),
) -> dict:
    return _handle(
        service.run_job_to_completion,
        req.config_id,
        job_name,
        req.arguments,
        req.poll_interval_seconds,
        req.max_attempts,
    )
