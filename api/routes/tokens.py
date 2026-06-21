from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.dependencies import get_session
from etl_framework.repository.repository import TokenRepository
from api.services.audit_service import AuditService

router = APIRouter(tags=["tokens"])


class TokenCreate(BaseModel):
    name: str
    expires_at: datetime | None = None


class TokenOut(BaseModel):
    id: int
    name: str
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    enabled: bool
    model_config = {"from_attributes": True}


class TokenCreatedOut(TokenOut):
    raw_token: str  # shown once only


@router.post("", response_model=TokenCreatedOut, status_code=201)
def create_token(body: TokenCreate, request: Request, db: Session = Depends(get_session)):
    raw, token = TokenRepository(db).create(body.name, body.expires_at)
    AuditService(db).log(
        request, "token.created", "token", token.id,
        {"name": token.name, "expires_at": token.expires_at.isoformat() if token.expires_at else None},
        actor=body.name,
    )
    return TokenCreatedOut(
        id=token.id,
        name=token.name,
        created_at=token.created_at,
        last_used_at=token.last_used_at,
        expires_at=token.expires_at,
        enabled=token.enabled,
        raw_token=raw,
    )


@router.get("", response_model=list[TokenOut])
def list_tokens(db: Session = Depends(get_session)):
    return TokenRepository(db).list()


@router.delete("/{token_id}", status_code=204)
def revoke_token(token_id: int, request: Request, db: Session = Depends(get_session)):
    if TokenRepository(db).revoke(token_id) is None:
        raise HTTPException(status_code=404, detail="Token not found")
    AuditService(db).log(request, "token.revoked", "token", token_id)
