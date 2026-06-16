from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel


router = APIRouter(prefix="/api/auth", tags=["auth"])


class AuthVerifyOut(BaseModel):
    ok: bool
    actor: str | None = None
    token_id: int | None = None


@router.get("/verify", response_model=AuthVerifyOut)
def verify_auth(request: Request):
    return AuthVerifyOut(
        ok=True,
        actor=getattr(request.state, "token_actor", None),
        token_id=getattr(request.state, "token_id", None),
    )
