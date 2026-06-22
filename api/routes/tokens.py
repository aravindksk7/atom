from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.dependencies import get_session, require_admin
from api.middleware.auth import evict_token_cache
from api.services.audit_service import AuditService
from etl_framework.repository.repository import TokenRepository

logger = logging.getLogger(__name__)
router = APIRouter(tags=["tokens"])


class TokenCreate(BaseModel):
    name: str
    expires_at: datetime | None = None
    is_admin: bool = False


class TokenOut(BaseModel):
    id: int
    name: str
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    enabled: bool
    is_admin: bool
    token_hint: str
    model_config = {"from_attributes": True}


class TokenCreatedOut(TokenOut):
    raw_token: str  # shown once only


def _verify_admin_from_request(request: Request, db: Session) -> None:
    """Verify admin access directly from the Authorization header.

    POST /api/tokens is exempt from BearerTokenMiddleware (bootstrap path), so
    request.state.token is not set by the middleware.  For the non-bootstrap case
    we perform the token lookup ourselves and check is_admin.
    """
    # Fast path: middleware already validated and attached the token
    token = getattr(request.state, "token", None)
    if token is not None:
        if not getattr(token, "is_admin", False):
            raise HTTPException(status_code=403, detail="Admin token required")
        return

    # Slow path: middleware was exempt, check the Authorization header ourselves
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
            detail="Missing or invalid Authorization header",
        )

    raw_token = auth[len("Bearer "):]
    verified = TokenRepository(db).verify(raw_token)
    if verified is None:
        raise HTTPException(
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
            detail="Invalid or expired token",
        )
    if not getattr(verified, "is_admin", False):
        raise HTTPException(status_code=403, detail="Admin token required")


@router.post("", response_model=TokenCreatedOut, status_code=201)
def create_token(body: TokenCreate, request: Request, db: Session = Depends(get_session)):
    repo = TokenRepository(db)
    is_bootstrap = repo.count() == 0

    if not is_bootstrap:
        _verify_admin_from_request(request, db)

    is_admin = True if is_bootstrap else body.is_admin
    raw, token = repo.create(body.name, body.expires_at, is_admin=is_admin)

    if is_bootstrap:
        logger.warning(
            "Bootstrap admin token created — store this value securely, "
            "it will not be shown again. Token hint: ...%s",
            raw[-8:],
        )

    AuditService(db).log(
        request,
        "token.created",
        "token",
        token.id,
        {
            "name": token.name,
            "is_admin": token.is_admin,
            "expires_at": token.expires_at.isoformat() if token.expires_at else None,
        },
        actor=body.name,
    )
    return TokenCreatedOut(
        id=token.id,
        name=token.name,
        created_at=token.created_at,
        last_used_at=token.last_used_at,
        expires_at=token.expires_at,
        enabled=token.enabled,
        is_admin=token.is_admin,
        token_hint=token.token_hint,
        raw_token=raw,
    )


@router.get("", response_model=list[TokenOut], dependencies=[Depends(require_admin)])
def list_tokens(db: Session = Depends(get_session)):
    return TokenRepository(db).list()


@router.delete("/{token_id}", status_code=204, dependencies=[Depends(require_admin)])
def revoke_token(token_id: int, request: Request, db: Session = Depends(get_session)):
    token_hash = TokenRepository(db).revoke(token_id)
    if token_hash is None:
        raise HTTPException(status_code=404, detail="Token not found")
    evict_token_cache(token_hash)
    AuditService(db).log(request, "token.revoked", "token", token_id)


class TokenPatch(BaseModel):
    expires_at: datetime | None = None
    enabled: bool | None = None


@router.patch("/{token_id}", response_model=TokenOut, dependencies=[Depends(require_admin)])
def update_token(token_id: int, body: TokenPatch, request: Request, db: Session = Depends(get_session)):
    """Update expiry or enabled state of an existing token."""
    from etl_framework.repository.models import ApiToken as _ApiToken
    token = db.get(_ApiToken, token_id)
    if token is None:
        raise HTTPException(status_code=404, detail="Token not found")
    changed: dict = {}
    if body.expires_at is not None:
        token.expires_at = body.expires_at
        changed["expires_at"] = body.expires_at.isoformat()
    if body.enabled is not None:
        token.enabled = body.enabled
        changed["enabled"] = body.enabled
        if not body.enabled:
            evict_token_cache(token.token_hash)
    db.commit()
    db.refresh(token)
    AuditService(db).log(request, "token.updated", "token", token_id, changed)
    return token


@router.post("/{token_id}/rotate", response_model=TokenCreatedOut, dependencies=[Depends(require_admin)])
def rotate_token(token_id: int, request: Request, db: Session = Depends(get_session)):
    """Atomically revoke old token and issue a replacement with same name/role/expiry."""
    from etl_framework.repository.models import ApiToken as _ApiToken
    repo = TokenRepository(db)
    old = db.get(_ApiToken, token_id)
    if old is None or not old.enabled:
        raise HTTPException(status_code=404, detail="Token not found")

    old_hash = repo.revoke(token_id)
    evict_token_cache(old_hash)

    raw, new_token = repo.create(old.name, old.expires_at, is_admin=old.is_admin)
    AuditService(db).log(
        request, "token.rotated", "token", new_token.id,
        {"replaced_token_id": token_id, "name": new_token.name},
    )
    return TokenCreatedOut(
        id=new_token.id,
        name=new_token.name,
        created_at=new_token.created_at,
        last_used_at=new_token.last_used_at,
        expires_at=new_token.expires_at,
        enabled=new_token.enabled,
        is_admin=new_token.is_admin,
        token_hint=new_token.token_hint,
        raw_token=raw,
    )
