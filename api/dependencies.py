from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from etl_framework.repository.database import get_db


def get_session(db: Session = Depends(get_db)) -> Session:
    return db


def require_admin(request: Request) -> None:
    token = getattr(request.state, "token", None)
    if token is None or not getattr(token, "is_admin", False):
        raise HTTPException(status_code=403, detail="Admin token required")
