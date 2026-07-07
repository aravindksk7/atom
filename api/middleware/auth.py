from __future__ import annotations

import re
import time
import logging
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from etl_framework.repository import database as _db_module
from etl_framework.repository.models import ApiToken as _ApiToken
from etl_framework.repository.repository import TokenRepository

logger = logging.getLogger(__name__)

_CACHE_TTL = 30.0          # seconds — max staleness for cached token lookups
_LAZY_WRITE_INTERVAL = 300.0  # seconds — min gap between last_used_at DB writes (5 min)

# module-level cache: token_hash -> (ApiToken, cached_at_monotonic)
_cache: dict[str, tuple] = {}

_EXEMPT_PREFIXES = ("/api/health",)
_EXEMPT_EXACT = {"/", "/api/health", "/api/auth/setup-status"}
_EXEMPT_PATTERNS = [re.compile(r"^/api/runs/[^/]+/badge\.svg$")]


def _has_sap_bo_auth(request: Request) -> bool:
    if not request.url.path.startswith("/api/adapters/sap-bo/"):
        return False
    if request.headers.get("x-sap-logontoken"):
        return True
    auth = request.headers.get("Authorization", "")
    return auth.lower().startswith("basic ")


def evict_token_cache(token_hash: str) -> None:
    _cache.pop(token_hash, None)


def _is_exempt(path: str, method: str) -> bool:
    if path in _EXEMPT_EXACT:
        return True
    if not path.startswith("/api/"):
        return True
    for prefix in _EXEMPT_PREFIXES:
        if path.startswith(prefix):
            return True
    for pattern in _EXEMPT_PATTERNS:
        if pattern.match(path):
            return True
    # Bootstrap: allow unauthenticated POST /api/tokens only
    if path == "/api/tokens" and method == "POST":
        return True
    return False


class BearerTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if _is_exempt(request.url.path, request.method):
            return await call_next(request)
        if _has_sap_bo_auth(request):
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            self._audit_failure(request, "missing_header")
            return JSONResponse(
                {"detail": "Missing or invalid Authorization header"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        raw_token = auth[len("Bearer "):]
        token_hash = TokenRepository._hash(raw_token)

        # --- cache lookup ---
        cached = _cache.get(token_hash)
        if cached is not None:
            token, cached_at = cached
            if time.monotonic() - cached_at < _CACHE_TTL:
                exp = token.expires_at
                if exp:
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=timezone.utc)
                    if exp < datetime.now(timezone.utc):
                        evict_token_cache(token_hash)
                        self._audit_failure(request, "expired")
                        return JSONResponse(
                            {"detail": "Invalid or expired token"},
                            status_code=401,
                            headers={"WWW-Authenticate": "Bearer"},
                        )
                request.state.token_actor = token.name
                request.state.token_id = token.id
                request.state.token = token
                return await call_next(request)
            else:
                del _cache[token_hash]

        # --- DB lookup ---
        db = _db_module.SessionLocal()
        token = None
        try:
            token_row = db.query(_ApiToken).filter_by(token_hash=token_hash, enabled=True).first()
            if token_row is not None:
                exp = token_row.expires_at
                if exp:
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=timezone.utc)
                    if exp < datetime.now(timezone.utc):
                        token_row = None
            if token_row is not None:
                now = datetime.now(timezone.utc)
                lu = token_row.last_used_at
                if lu is not None and lu.tzinfo is None:
                    lu = lu.replace(tzinfo=timezone.utc)
                if lu is None or (now - lu).total_seconds() > _LAZY_WRITE_INTERVAL:
                    token_row.last_used_at = now
                    db.commit()
                _cache[token_hash] = (token_row, time.monotonic())
                request.state.token_actor = token_row.name
                request.state.token_id = token_row.id
                request.state.token = token_row
                token = token_row
        finally:
            db.close()

        if token is None:
            self._audit_failure(request, "invalid_token")
            return JSONResponse(
                {"detail": "Invalid or expired token"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        return await call_next(request)

    def _audit_failure(self, request: Request, reason: str) -> None:
        try:
            from api.services.audit_service import AuditService
            db = _db_module.SessionLocal()
            try:
                AuditService(db).log(
                    request,
                    "token.auth_failed",
                    "token",
                    None,
                    {
                        "reason": reason,
                        "path": request.url.path,
                        "method": request.method,
                        "ip": request.client.host if request.client else None,
                    },
                    actor=None,
                )
            finally:
                db.close()
        except Exception:
            logger.warning("Failed to write auth_failed audit event", exc_info=True)
