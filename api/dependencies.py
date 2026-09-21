from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from etl_framework.repository.database import get_db


def get_session(db: Session = Depends(get_db)) -> Session:
    return db


def is_admin_request(request: Request) -> bool:
    """True when the caller presented an admin token. BearerTokenMiddleware
    puts the row on request.state; routes that gate only *some* of their
    requests (see api/routes/jobs.py) ask this instead of depending on
    require_admin."""
    token = getattr(request.state, "token", None)
    return token is not None and bool(getattr(token, "is_admin", False))


def require_admin(request: Request) -> None:
    if not is_admin_request(request):
        raise HTTPException(status_code=403, detail="Admin token required")
