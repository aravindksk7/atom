from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.services.audit_service import AuditService
from api.services.aws_airflow_service import AwsAirflowService
from etl_framework.repository.repository import ConfigRepository

router = APIRouter(tags=["aws-airflow"])


def get_aws_airflow_service(db: Session = Depends(get_session)) -> AwsAirflowService:
    return AwsAirflowService(ConfigRepository(db))


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


class AirflowTriggerRequest(BaseModel):
    config_id: int
    conf: dict[str, Any] | None = None


class AirflowRunRequest(BaseModel):
    config_id: int
    conf: dict[str, Any] | None = None
    poll_interval_seconds: float = Field(default=1.0, ge=0)
    max_attempts: int = Field(default=60, ge=1)


@router.get("/dags")
def list_dags(
    request: Request,
    service: AwsAirflowService = Depends(get_aws_airflow_service),
    db: Session = Depends(get_session),
    config_id: int = Query(...),
):
    result = _handle(service.list_dags, config_id)
    AuditService(db).log(request, "aws_airflow.check", "aws_airflow", config_id, {"op": "list_dags"})
    return {"dags": result}


@router.get("/dags/{dag_id}")
def dag_details(
    dag_id: str,
    request: Request,
    service: AwsAirflowService = Depends(get_aws_airflow_service),
    db: Session = Depends(get_session),
    config_id: int = Query(...),
):
    result = _handle(service.get_dag_details, config_id, dag_id)
    AuditService(db).log(
        request,
        "aws_airflow.check",
        "aws_airflow",
        dag_id,
        {"op": "dag_details", "config_id": config_id},
    )
    return result


@router.post("/dags/{dag_id}/trigger")
def trigger_dag_run(
    dag_id: str,
    body: AirflowTriggerRequest,
    request: Request,
    service: AwsAirflowService = Depends(get_aws_airflow_service),
    db: Session = Depends(get_session),
):
    result = _handle(service.trigger_dag_run, body.config_id, dag_id, body.conf)
    AuditService(db).log(
        request,
        "aws_airflow.check",
        "aws_airflow",
        dag_id,
        {"op": "trigger", "config_id": body.config_id},
    )
    return result


@router.get("/dags/{dag_id}/runs/{dag_run_id}")
def dag_run_status(
    dag_id: str,
    dag_run_id: str,
    request: Request,
    service: AwsAirflowService = Depends(get_aws_airflow_service),
    db: Session = Depends(get_session),
    config_id: int = Query(...),
):
    result = _handle(service.get_dag_run_status, config_id, dag_id, dag_run_id)
    AuditService(db).log(
        request,
        "aws_airflow.check",
        "aws_airflow",
        dag_run_id,
        {"op": "run_status", "config_id": config_id, "dag_id": dag_id},
    )
    return result


@router.post("/dags/{dag_id}/run")
def run_dag(
    dag_id: str,
    body: AirflowRunRequest,
    request: Request,
    service: AwsAirflowService = Depends(get_aws_airflow_service),
    db: Session = Depends(get_session),
):
    result = _handle(
        service.run_dag_to_completion,
        body.config_id,
        dag_id,
        body.conf,
        body.poll_interval_seconds,
        body.max_attempts,
    )
    AuditService(db).log(
        request,
        "aws_airflow.check",
        "aws_airflow",
        dag_id,
        {"op": "run", "config_id": body.config_id},
    )
    return result