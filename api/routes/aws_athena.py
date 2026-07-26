from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.schemas import (
    AthenaQueryResultsOut,
    AthenaQueryResultsRequest,
    AthenaQueryStatusOut,
    AthenaQueryStatusRequest,
    AthenaRunQueryOut,
    AthenaRunQueryRequest,
    AthenaStartQueryOut,
    AthenaStartQueryRequest,
)
from api.services.audit_service import AuditService
from api.services.aws_athena_service import (
    AthenaQueryFailedError,
    AwsAthenaService,
)
from etl_framework.repository.repository import ConfigRepository

router = APIRouter(tags=["aws-athena"])


def get_aws_athena_service(db: Session = Depends(get_session)) -> AwsAthenaService:
    return AwsAthenaService(ConfigRepository(db))


def _handle(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except HTTPException:
        raise
    except AthenaQueryFailedError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error_type": type(exc).__name__,
                "message": str(exc),
                "status": exc.status.model_dump(mode="json"),
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail={"error_type": type(exc).__name__, "message": str(exc)},
        ) from exc


@router.post("/start-query", response_model=AthenaStartQueryOut)
def start_query(
    body: AthenaStartQueryRequest,
    request: Request,
    service: AwsAthenaService = Depends(get_aws_athena_service),
    db: Session = Depends(get_session),
) -> AthenaStartQueryOut:
    result = _handle(service.start_query, body.config_id, body.database, body.query, body.output_location, body.workgroup)
    AuditService(db).log(request, "aws_athena.check", "aws_athena", "start_query", {"database": body.database})
    return result


@router.post("/query-status", response_model=AthenaQueryStatusOut)
def query_status(
    body: AthenaQueryStatusRequest,
    request: Request,
    service: AwsAthenaService = Depends(get_aws_athena_service),
    db: Session = Depends(get_session),
) -> AthenaQueryStatusOut:
    result = _handle(service.get_query_status, body.config_id, body.query_execution_id)
    AuditService(db).log(request, "aws_athena.check", "aws_athena", body.query_execution_id, {"op": "query_status"})
    return result


@router.post("/query-results", response_model=AthenaQueryResultsOut)
def query_results(
    body: AthenaQueryResultsRequest,
    request: Request,
    service: AwsAthenaService = Depends(get_aws_athena_service),
    db: Session = Depends(get_session),
) -> AthenaQueryResultsOut:
    result = _handle(service.get_query_results, body.config_id, body.query_execution_id, body.max_rows)
    AuditService(db).log(request, "aws_athena.check", "aws_athena", body.query_execution_id, {"op": "query_results"})
    return result


@router.post("/run-query", response_model=AthenaRunQueryOut)
def run_query(
    body: AthenaRunQueryRequest,
    request: Request,
    service: AwsAthenaService = Depends(get_aws_athena_service),
    db: Session = Depends(get_session),
) -> AthenaRunQueryOut:
    result = _handle(
        service.run_query,
        body.config_id,
        body.database,
        body.query,
        body.output_location,
        body.workgroup,
        body.poll_interval_seconds,
        body.max_attempts,
        body.max_rows,
    )
    AuditService(db).log(request, "aws_athena.check", "aws_athena", "run_query", {"database": body.database})
    return result
