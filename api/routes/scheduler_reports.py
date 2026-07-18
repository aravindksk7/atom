from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.services.scheduler_reporting import SchedulerReportFilters, SchedulerReportingService

router = APIRouter(tags=["scheduler-reports"])


def _filters(
    from_dt: Annotated[datetime | None, Query(alias="from")] = None,
    to_dt: Annotated[datetime | None, Query(alias="to")] = None,
    days: int | None = Query(7, ge=1, le=365),
    schedule_id: int | None = Query(None, ge=1),
    job: str | None = None,
    status: str | None = None,
    exit_code: int | None = None,
) -> SchedulerReportFilters:
    if from_dt is not None and to_dt is not None and from_dt > to_dt:
        raise HTTPException(status_code=422, detail="from must be earlier than to")
    return SchedulerReportFilters(
        from_dt=from_dt,
        to_dt=to_dt,
        days=days,
        schedule_id=schedule_id,
        job=job,
        status=status,
        exit_code=exit_code,
    )


@router.get("/summary")
def summary(filters: SchedulerReportFilters = Depends(_filters), db: Session = Depends(get_session)):
    return SchedulerReportingService(db).summary(filters)


@router.get("/grid")
def grid(filters: SchedulerReportFilters = Depends(_filters), db: Session = Depends(get_session)):
    return SchedulerReportingService(db).grid(filters)


@router.get("/timeline")
def timeline(filters: SchedulerReportFilters = Depends(_filters), db: Session = Depends(get_session)):
    return SchedulerReportingService(db).timeline(filters)


@router.get("/metrics")
def metrics(filters: SchedulerReportFilters = Depends(_filters), db: Session = Depends(get_session)):
    return SchedulerReportingService(db).metrics(filters)


@router.get("/export")
def export_report(
    format: str = Query("json", pattern="^(json|csv)$"),
    filters: SchedulerReportFilters = Depends(_filters),
    db: Session = Depends(get_session),
):
    service = SchedulerReportingService(db)
    if format == "csv":
        return Response(
            content=service.export_csv(filters),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=scheduler-report.csv"},
        )
    return Response(
        content=json.dumps({"rows": service.export_rows(filters)}, default=str),
        media_type="application/json",
    )


@router.post("/prune")
def prune(retention_days: int = Query(30, ge=1, le=365), db: Session = Depends(get_session)):
    return SchedulerReportingService(db).prune(retention_days=retention_days)
