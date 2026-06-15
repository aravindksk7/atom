from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from etl_framework.repository import database as _db_module
from etl_framework.repository.repository import TokenRepository

# Paths that bypass token auth (health bootstrap + token creation itself)
_EXEMPT_PREFIXES = (
    "/api/health",
    "/api/tokens",
    "/api/runs/",   # badge SVG endpoints are public (set in PR3)
)
_EXEMPT_EXACT = {"/", "/api/health"}


def _is_exempt(path: str) -> bool:
    if path in _EXEMPT_EXACT:
        return True
    # static assets
    if not path.startswith("/api/"):
        return True
    for prefix in _EXEMPT_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


class BearerTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if _is_exempt(request.url.path):
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse(
                {"detail": "Missing or invalid Authorization header"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        raw_token = auth[len("Bearer "):]
        db = _db_module.SessionLocal()
        try:
            token = TokenRepository(db).verify(raw_token)
        finally:
            db.close()

        if token is None:
            return JSONResponse(
                {"detail": "Invalid or expired token"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        request.state.token = token
        return await call_next(request)
