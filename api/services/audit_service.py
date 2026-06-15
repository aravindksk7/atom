from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from etl_framework.repository.models import AuditEvent
from etl_framework.repository.repository import AuditRepository


class AuditService:
    def __init__(self, db: Session) -> None:
        self._repo = AuditRepository(db)

    @staticmethod
    def actor_from_request(request: Request | None) -> str | None:
        if request is None:
            return None
        actor = getattr(request.state, "token_actor", None)
        if actor:
            return actor
        token = getattr(request.state, "token", None)
        if token is not None:
            try:
                return getattr(token, "name", None) or f"token:{getattr(token, 'id', '')}"
            except Exception:
                token_id = getattr(request.state, "token_id", None)
                return f"token:{token_id}" if token_id else None
        return request.headers.get("x-actor") or request.headers.get("x-user")

    def log(
        self,
        request: Request | None,
        action: str,
        resource_type: str,
        resource_id: str | int | None = None,
        diff: dict | None = None,
        actor: str | None = None,
    ) -> AuditEvent:
        return self._repo.log(
            actor=actor or self.actor_from_request(request),
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            diff=diff,
        )

    def list(
        self,
        resource_type: str | None = None,
        resource_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditEvent]:
        return self._repo.list(
            resource_type=resource_type,
            resource_id=resource_id,
            limit=limit,
            offset=offset,
        )
