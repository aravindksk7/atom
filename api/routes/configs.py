import os
import tempfile

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
import yaml
from sqlalchemy.orm import Session
from api.schemas import (
    ConfigCreate,
    ConfigImportYamlRequest,
    ConfigUpdate,
    ConfigOut,
    ConfigValidationRequest,
    ConfigValidationOut,
    FrameworkErrorOut,
)
from api.dependencies import get_session
from etl_framework.config.loader import ConfigLoader
from etl_framework.config.models import EnvironmentConfig
from etl_framework.exceptions import ConfigurationError
from etl_framework.repository.repository import ConfigRepository
from api.services.audit_service import AuditService

router = APIRouter(tags=["configs"])

_SENSITIVE_KEYS = {"db_password", "automic_password", "bo_password"}
_MASK = "********"


def _mask(data: dict) -> dict:
    """Replace sensitive credential values with a fixed mask before returning to callers."""
    return {k: (_MASK if k in _SENSITIVE_KEYS and v is not None else v) for k, v in data.items()}


@router.get("", response_model=list[ConfigOut])
def list_configs(db: Session = Depends(get_session)):
    repo = ConfigRepository(db)
    cfgs = repo.list()
    default_keys = EnvironmentConfig(name="template", db_host="localhost", db_password="").model_dump(exclude={"name"})
    return [
        ConfigOut(id=c.id, name=c.name, env_name=c.env_name,
                  config_data=_mask({**default_keys, **(c.config_json or {})}),
                  created_at=c.created_at, updated_at=c.updated_at)
        for c in cfgs
    ]


@router.post("", response_model=ConfigOut, status_code=201)
def create_config(body: ConfigCreate, request: Request, db: Session = Depends(get_session)):
    repo = ConfigRepository(db)
    cfg = repo.create(name=body.name, env_name=body.env_name, config_data=body.config_data)
    AuditService(db).log(
        request, "config.created", "config", cfg.id,
        {"name": cfg.name, "env_name": cfg.env_name},
    )
    return ConfigOut(id=cfg.id, name=cfg.name, env_name=cfg.env_name,
                     config_data=_mask(cfg.config_json or {}),
                     created_at=cfg.created_at, updated_at=cfg.updated_at)


@router.post("/validate", response_model=ConfigValidationOut)
def validate_config(body: ConfigValidationRequest):
    try:
        env_config = EnvironmentConfig.model_validate(
            {"name": body.env_name, **body.config_data}
        )
    except ValidationError as exc:
        errors = [
            FrameworkErrorOut(
                error_type="validation_error",
                message=err["msg"],
                field_name=".".join(str(part) for part in err["loc"]),
                details={"input": err.get("input")},
            )
            for err in exc.errors()
        ]
        return ConfigValidationOut(ok=False, env_name=body.env_name, errors=errors)
    return ConfigValidationOut(
        ok=True,
        env_name=body.env_name,
        config_data=_mask(env_config.model_dump(exclude={"name"})),
    )


@router.post("/import-yaml", response_model=list[ConfigOut], status_code=201)
def import_yaml_config(body: ConfigImportYamlRequest, request: Request, db: Session = Depends(get_session)):
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as tmp:
            temp_path = tmp.name
            tmp.write(body.yaml_content)
        envs = ConfigLoader().load(temp_path)
    except ConfigurationError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error_type": type(exc).__name__,
                "message": str(exc),
                "field_name": getattr(exc, "field_name", None),
            },
        ) from exc
    except yaml.YAMLError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error_type": type(exc).__name__,
                "message": str(exc),
                "field_name": None,
            },
        ) from exc
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)

    repo = ConfigRepository(db)
    imported = []
    for env_name, env_config in envs.items():
        config_data = env_config.model_dump(exclude={"name"})
        existing = repo.get_by_name(env_name)
        if existing is None:
            cfg = repo.create(name=env_name, env_name=env_name, config_data=config_data)
        else:
            cfg = repo.update(existing.id, env_name=env_name, config_data=config_data)
        imported.append(
            ConfigOut(
                id=cfg.id,
                name=cfg.name,
                env_name=cfg.env_name,
                config_data=_mask(cfg.config_json or {}),
                created_at=cfg.created_at,
                updated_at=cfg.updated_at,
            )
        )
        AuditService(db).log(
            request,
            "config.imported" if existing is None else "config.updated",
            "config",
            cfg.id,
            {"name": cfg.name, "env_name": cfg.env_name, "source": "yaml"},
        )
    return imported


@router.get("/{config_id}", response_model=ConfigOut)
def get_config(config_id: int, db: Session = Depends(get_session)):
    repo = ConfigRepository(db)
    cfg = repo.get(config_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="Config not found")
    default_keys = EnvironmentConfig(name="template", db_host="localhost", db_password="").model_dump(exclude={"name"})
    full_data = {**default_keys, **(cfg.config_json or {})}
    return ConfigOut(id=cfg.id, name=cfg.name, env_name=cfg.env_name,
                     config_data=_mask(full_data),
                     created_at=cfg.created_at, updated_at=cfg.updated_at)


@router.put("/{config_id}", response_model=ConfigOut)
def update_config(config_id: int, body: ConfigUpdate, request: Request, db: Session = Depends(get_session)):
    repo = ConfigRepository(db)
    before = repo.get(config_id)
    before_data = None
    if before is not None:
        before_data = {
            "name": before.name,
            "env_name": before.env_name,
            "config_data": before.config_json,
        }
    kwargs = {}
    if body.config_data is not None:
        kwargs["config_data"] = body.config_data
    if body.name is not None:
        kwargs["name"] = body.name
    if body.env_name is not None:
        kwargs["env_name"] = body.env_name
    cfg = repo.update(config_id, **kwargs)
    if cfg is None:
        raise HTTPException(status_code=404, detail="Config not found")
    AuditService(db).log(
        request,
        "config.updated",
        "config",
        cfg.id,
        {
            "before": before_data,
            "after": {"name": cfg.name, "env_name": cfg.env_name, "config_data": _mask(cfg.config_json or {})},
        },
    )
    return ConfigOut(id=cfg.id, name=cfg.name, env_name=cfg.env_name,
                     config_data=_mask(cfg.config_json or {}),
                     created_at=cfg.created_at, updated_at=cfg.updated_at)


@router.delete("/{config_id}", status_code=204)
def delete_config(config_id: int, request: Request, db: Session = Depends(get_session)):
    repo = ConfigRepository(db)
    cfg = repo.get(config_id)
    if not repo.delete(config_id):
        raise HTTPException(status_code=404, detail="Config not found")
    AuditService(db).log(
        request,
        "config.deleted",
        "config",
        config_id,
        {"name": cfg.name if cfg else None, "env_name": cfg.env_name if cfg else None},
    )
