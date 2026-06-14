from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from typing import Any

from api.dependencies import get_session
from etl_framework.repository.repository import JobRepository
from api.services.job_registry import JobRegistryService
from api.schemas import JobDefinition

router = APIRouter(tags=["jobs"])

def get_job_service(db: Session = Depends(get_session)) -> JobRegistryService:
    return JobRegistryService(repository=JobRepository(db))

@router.get("")
def list_jobs(service: JobRegistryService = Depends(get_job_service)) -> list[dict[str, Any]]:
    return service.list_jobs()

@router.get("/{name}")
def get_job(name: str, service: JobRegistryService = Depends(get_job_service)) -> dict[str, Any]:
    return service.get_job(name)

@router.post("/{name}")
def save_job(name: str, job_def: JobDefinition, service: JobRegistryService = Depends(get_job_service)) -> dict[str, Any]:
    return service.save_job(name, job_def.model_dump())

@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(name: str, service: JobRegistryService = Depends(get_job_service)):
    service.delete_job(name)