from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.dependencies import get_session
from etl_framework.repository.repository import LineageRepository

router = APIRouter(prefix="/api/lineage", tags=["lineage"])


@router.get("/jobs")
def job_lineage(db: Session = Depends(get_session)):
    return LineageRepository(db).job_graph()


@router.get("/jobs/{job_name}/upstream")
def job_upstream(job_name: str, db: Session = Depends(get_session)):
    return {"job": job_name, "upstream": LineageRepository(db).get_upstream(job_name)}


@router.get("/jobs/{job_name}/downstream")
def job_downstream(job_name: str, db: Session = Depends(get_session)):
    return {"job": job_name, "downstream": LineageRepository(db).get_downstream(job_name)}
