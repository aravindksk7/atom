from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from typing import Any

from api.dependencies import get_session
from etl_framework.repository.repository import ConfigRepository
from api.services.config_service import ConfigService
from api.schemas import ConfigValidationRequest, ConfigValidationOut, ConfigImportYamlRequest

router = APIRouter(tags=["configs"])

def get_config_service(db: Session = Depends(get_session)) -> ConfigService:
    return ConfigService(repository=ConfigRepository(db))

@router.get("")
def list_configs(service: ConfigService = Depends(get_config_service)) -> list[dict[str, Any]]:
    return service.list_configs()

@router.get("/{name}")
def get_config(name: str, service: ConfigService = Depends(get_config_service)) -> dict[str, Any]:
    return service.get_config(name)

@router.post("/{name}")
def save_config(name: str, raw_data: dict[str, Any], service: ConfigService = Depends(get_config_service)) -> dict[str, Any]:
    return service.save_config(name, raw_data)

@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_config(name: str, service: ConfigService = Depends(get_config_service)):
    service.delete_config(name)

@router.post("/validate", response_model=ConfigValidationOut)
def validate_config(request: ConfigValidationRequest, service: ConfigService = Depends(get_config_service)):
    try:
        service.validate_config(request.name, request.config_data)
        return ConfigValidationOut(valid=True, errors=None)
    except Exception as e:
        from etl_framework.exceptions import ConfigurationError
        if isinstance(e, ConfigurationError):
            return ConfigValidationOut(valid=False, errors=[str(e)])
        raise

@router.post("/import-yaml")
def import_yaml(request: ConfigImportYamlRequest, service: ConfigService = Depends(get_config_service)):
    saved_envs = service.import_yaml(request.yaml_content)
    return {
        "message": f"Successfully imported {len(saved_envs)} environments.",
        "environments": list(saved_envs.keys())
    }