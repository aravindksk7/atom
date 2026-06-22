from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.dependencies import get_session
from etl_framework.repository.repository import TokenRepository


router = APIRouter(prefix="/api/auth", tags=["auth"])


class AuthVerifyOut(BaseModel):
    ok: bool
    actor: str | None = None
    token_id: int | None = None
    is_admin: bool = False


class AuthSetupStatusOut(BaseModel):
    initialized: bool


@router.get("/setup-status", response_model=AuthSetupStatusOut)
def setup_status(db: Session = Depends(get_session)):
    return AuthSetupStatusOut(initialized=TokenRepository(db).count() > 0)


@router.get("/verify", response_model=AuthVerifyOut)
def verify_auth(request: Request):
    return AuthVerifyOut(
        ok=True,
        actor=getattr(request.state, "token_actor", None),
        token_id=getattr(request.state, "token_id", None),
        is_admin=bool(getattr(getattr(request.state, "token", None), "is_admin", False)),
    )
