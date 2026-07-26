from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
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
):
    result = _handle(service.list_databases, body.config_id)
    AuditService(db).log(request, "aws_glue.check", "aws_glue", "databases", {"op": "databases"})
    return result


@router.post("/tables", response_model=GlueTablesOut)
def glue_tables(
    body: GlueTablesRequest,
    request: Request,
    service: AwsGlueService = Depends(get_aws_glue_service),
    db: Session = Depends(get_session),
):
    result = _handle(service.list_tables, body.config_id, body.database)
    AuditService(db).log(request, "aws_glue.check", "aws_glue", body.database, {"op": "tables"})
    return result


@router.post("/table", response_model=GlueTableOut)
def glue_table(
    body: GlueTableRequest,
    request: Request,
    service: AwsGlueService = Depends(get_aws_glue_service),
    db: Session = Depends(get_session),
):
    result = _handle(service.describe_table, body.config_id, body.database, body.table)
    AuditService(db).log(request, "aws_glue.check", "aws_glue", body.database, {"op": "table", "table": body.table})
    return result


@router.post("/compare-tables", response_model=GlueCatalogCompareOut)
def glue_compare_tables(
    body: GlueCatalogCompareRequest,
    request: Request,
    service: AwsGlueService = Depends(get_aws_glue_service),
    db: Session = Depends(get_session),
):
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
