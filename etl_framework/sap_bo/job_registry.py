import logging
from typing import Any
from fastapi import HTTPException

logger = logging.getLogger("api.services.job_registry")

class JobRegistryService:
    def __init__(self, repository):
        self._repository = repository

    def list_jobs(self) -> list[dict[str, Any]]:
        return self._repository.list_jobs()

    def get_job(self, name: str) -> dict[str, Any]:
        job = self._repository.get_job(name)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job '{name}' not found.")
        return job

    def save_job(self, name: str, raw_data: dict[str, Any]) -> dict[str, Any]:
        from api.schemas import JobDefinition
        from pydantic import ValidationError
        
        try:
            JobDefinition(**raw_data)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
            
        self._repository.save_job(name, raw_data)
        return self.get_job(name)

    def delete_job(self, name: str) -> None:
        if not self._repository.get_job(name):
            raise HTTPException(status_code=404, detail=f"Job '{name}' not found.")
        self._repository.delete_job(name)