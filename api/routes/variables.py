from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.schemas import CustomVariableCreate, CustomVariableOut, CustomVariableUpdate
from etl_framework.repository.repository import CustomVariableRepository

router = APIRouter(tags=["variables"])


def _to_out(var) -> CustomVariableOut:
    return CustomVariableOut(
        id=var.id, name=var.name, var_type=var.var_type,
        default_value=var.default_value, description=var.description,
        created_at=var.created_at, updated_at=var.updated_at,
    )


@router.get("", response_model=list[CustomVariableOut])
def list_variables(db: Session = Depends(get_session)):
    return [_to_out(v) for v in CustomVariableRepository(db).list()]


@router.post("", response_model=CustomVariableOut, status_code=201)
def create_variable(body: CustomVariableCreate, db: Session = Depends(get_session)):
    repo = CustomVariableRepository(db)
    if repo.get_by_name(body.name) is not None:
        raise HTTPException(status_code=409, detail=f"A variable named '{body.name}' already exists")
    var = repo.create(
        name=body.name, var_type=body.var_type,
        default_value=body.default_value, description=body.description,
    )
    return _to_out(var)


@router.put("/{variable_id}", response_model=CustomVariableOut)
def update_variable(variable_id: int, body: CustomVariableUpdate, db: Session = Depends(get_session)):
    repo = CustomVariableRepository(db)
    existing = repo.get(variable_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Variable not found")
    kwargs = {k: v for k, v in body.model_dump(exclude_unset=True).items()}
    if "default_value" in kwargs and kwargs["default_value"] and "var_type" not in kwargs:
        from api.services.variable_types import validate_variable_value
        try:
            validate_variable_value(kwargs["default_value"], existing.var_type)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    var = repo.update(variable_id, **kwargs)
    return _to_out(var)


@router.delete("/{variable_id}", status_code=204)
def delete_variable(variable_id: int, db: Session = Depends(get_session)):
    if not CustomVariableRepository(db).delete(variable_id):
        raise HTTPException(status_code=404, detail="Variable not found")
