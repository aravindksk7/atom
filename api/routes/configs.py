from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from api.schemas import ConfigCreate, ConfigUpdate, ConfigOut
from api.dependencies import get_session
from etl_framework.repository.repository import ConfigRepository

router = APIRouter(tags=["configs"])


@router.get("", response_model=list[ConfigOut])
def list_configs(db: Session = Depends(get_session)):
    repo = ConfigRepository(db)
    cfgs = repo.list()
    return [ConfigOut(id=c.id, name=c.name, env_name=c.env_name,
                      config_data=c.config_json,
                      created_at=c.created_at, updated_at=c.updated_at)
            for c in cfgs]


@router.post("", response_model=ConfigOut, status_code=201)
def create_config(body: ConfigCreate, db: Session = Depends(get_session)):
    repo = ConfigRepository(db)
    cfg = repo.create(name=body.name, env_name=body.env_name, config_data=body.config_data)
    return ConfigOut(id=cfg.id, name=cfg.name, env_name=cfg.env_name,
                     config_data=cfg.config_json,
                     created_at=cfg.created_at, updated_at=cfg.updated_at)


@router.get("/{config_id}", response_model=ConfigOut)
def get_config(config_id: int, db: Session = Depends(get_session)):
    repo = ConfigRepository(db)
    cfg = repo.get(config_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="Config not found")
    return ConfigOut(id=cfg.id, name=cfg.name, env_name=cfg.env_name,
                     config_data=cfg.config_json,
                     created_at=cfg.created_at, updated_at=cfg.updated_at)


@router.put("/{config_id}", response_model=ConfigOut)
def update_config(config_id: int, body: ConfigUpdate, db: Session = Depends(get_session)):
    repo = ConfigRepository(db)
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
    return ConfigOut(id=cfg.id, name=cfg.name, env_name=cfg.env_name,
                     config_data=cfg.config_json,
                     created_at=cfg.created_at, updated_at=cfg.updated_at)


@router.delete("/{config_id}", status_code=204)
def delete_config(config_id: int, db: Session = Depends(get_session)):
    repo = ConfigRepository(db)
    if not repo.delete(config_id):
        raise HTTPException(status_code=404, detail="Config not found")
