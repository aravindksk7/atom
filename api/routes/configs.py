import json
import os
import tempfile

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ValidationError
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
from etl_framework.config.models import ApiEndpointEntry, EnvironmentConfig, resolve_connection
from etl_framework.exceptions import ConfigurationError
from etl_framework.repository.repository import ConfigRepository
from api.services.audit_service import AuditService

router = APIRouter(tags=["configs"])

_SENSITIVE_KEYS = {
    "db_password", "automic_password", "bo_password",
    "api_key", "bearer_token", "basic_password", "sap_bo_logon_token",
}
_MASK = "********"


def _mask(data: dict) -> dict:
    """Replace sensitive credential values with a fixed mask before returning to callers."""
    result = {
        k: (_MASK if k in _SENSITIVE_KEYS and v is not None else v)
        for k, v in data.items()
        if k not in ("connections", "api_endpoints")
    }
    if "connections" in data and isinstance(data["connections"], dict):
        result["connections"] = {
            conn_name: {
                k: (_MASK if k in _SENSITIVE_KEYS and v is not None else v)
                for k, v in entry.items()
            }
            for conn_name, entry in data["connections"].items()
        }
    if "api_endpoints" in data and isinstance(data["api_endpoints"], dict):
        result["api_endpoints"] = {
            ep_name: {
                k: (_MASK if k in _SENSITIVE_KEYS and v is not None else v)
                for k, v in entry.items()
            }
            for ep_name, entry in data["api_endpoints"].items()
        }
    return result


def _preserve_masked_secrets(incoming: dict, existing: dict | None) -> dict:
    """Keep stored secret values when the client submits the display mask."""
    if not existing:
        return incoming
    result = dict(incoming)
    for key in _SENSITIVE_KEYS:
        if result.get(key) == _MASK:
            result[key] = existing.get(key, "")

    incoming_connections = result.get("connections")
    existing_connections = existing.get("connections")
    if isinstance(incoming_connections, dict) and isinstance(existing_connections, dict):
        merged_connections = {}
        for conn_name, entry in incoming_connections.items():
            if not isinstance(entry, dict):
                merged_connections[conn_name] = entry
                continue
            merged_entry = dict(entry)
            existing_entry = existing_connections.get(conn_name, {})
            if isinstance(existing_entry, dict):
                for key in _SENSITIVE_KEYS:
                    if merged_entry.get(key) == _MASK:
                        merged_entry[key] = existing_entry.get(key, "")
            merged_connections[conn_name] = merged_entry
        result["connections"] = merged_connections

    incoming_endpoints = result.get("api_endpoints")
    existing_endpoints = existing.get("api_endpoints")
    if isinstance(incoming_endpoints, dict) and isinstance(existing_endpoints, dict):
        merged_endpoints = {}
        for ep_name, entry in incoming_endpoints.items():
            if not isinstance(entry, dict):
                merged_endpoints[ep_name] = entry
                continue
            merged_entry = dict(entry)
            existing_entry = existing_endpoints.get(ep_name, {})
            if isinstance(existing_entry, dict):
                for key in _SENSITIVE_KEYS:
                    if merged_entry.get(key) == _MASK:
                        merged_entry[key] = existing_entry.get(key, "")
            merged_endpoints[ep_name] = merged_entry
        result["api_endpoints"] = merged_endpoints
    return result


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
            {"name": body.env_name, **{k: v for k, v in body.config_data.items() if k != "connections"}}
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

    connection_errors: list[FrameworkErrorOut] = []
    for conn_name in (body.config_data.get("connections") or {}):
        try:
            resolve_connection(body.config_data, conn_name, env_name=body.env_name)
        except Exception as exc:
            connection_errors.append(FrameworkErrorOut(
                error_type="validation_error",
                message=str(exc),
                field_name=f"connections.{conn_name}",
                details={},
            ))

    for ep_name, ep_data in (body.config_data.get("api_endpoints") or {}).items():
        if not isinstance(ep_data, dict):
            connection_errors.append(FrameworkErrorOut(
                error_type="validation_error",
                message=f"api_endpoints.{ep_name} must be a mapping of fields, got {type(ep_data).__name__}",
                field_name=f"api_endpoints.{ep_name}",
                details={},
            ))
            continue
        try:
            ApiEndpointEntry.model_validate({"name": ep_name, **ep_data})
        except ValidationError as exc:
            for err in exc.errors():
                connection_errors.append(FrameworkErrorOut(
                    error_type="validation_error",
                    message=err["msg"],
                    field_name=f"api_endpoints.{ep_name}." + ".".join(str(p) for p in err["loc"]),
                    details={"input": err.get("input")},
                ))

    if connection_errors:
        return ConfigValidationOut(ok=False, env_name=body.env_name, errors=connection_errors)

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
        kwargs["config_data"] = _preserve_masked_secrets(
            body.config_data,
            before.config_json if before is not None else None,
        )
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


def _build_env(cfg, connection_name: str | None = None) -> EnvironmentConfig:
    return resolve_connection(
        cfg.config_json or {},
        connection_name,
        env_name=cfg.env_name or cfg.name,
    )


@router.get("/{config_id}/schema")
def get_db_schema(config_id: int, db: Session = Depends(get_session)):
    """Return all tables and columns visible to this config's database credentials."""
    repo = ConfigRepository(db)
    cfg = repo.get(config_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="Config not found")
    try:
        env = _build_env(cfg)
        from etl_framework.db.engine import DBEngine
        engine = DBEngine(env)
        df = engine.execute_query(
            "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE "
            "FROM INFORMATION_SCHEMA.COLUMNS "
            "ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION"
        )
        engine.dispose()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"DB connection failed: {exc}")

    tables: dict[tuple, list] = {}
    for _, row in df.iterrows():
        key = (str(row["TABLE_SCHEMA"]), str(row["TABLE_NAME"]))
        if key not in tables:
            tables[key] = []
        tables[key].append({"name": str(row["COLUMN_NAME"]), "type": str(row["DATA_TYPE"])})

    return [{"schema": k[0], "table": k[1], "columns": cols} for k, cols in tables.items()]


class _PreviewRequest(BaseModel):
    query: str
    limit: int = 50


@router.post("/{config_id}/preview-query")
def preview_query(config_id: int, body: _PreviewRequest, db: Session = Depends(get_session)):
    """Execute a SQL query against this config's database and return the first N rows."""
    repo = ConfigRepository(db)
    cfg = repo.get(config_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="Config not found")

    limit = max(1, min(200, body.limit))
    safe_sql = f"SELECT TOP {limit} * FROM ({body.query}) AS _preview"

    try:
        env = _build_env(cfg)
        from etl_framework.db.engine import DBEngine
        engine = DBEngine(env)
        df = engine.execute_query(safe_sql)
        engine.dispose()
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Query failed: {exc}")

    rows = json.loads(df.to_json(orient="values", date_format="iso"))
    return {"columns": list(df.columns), "rows": rows}
