"""Fire-and-forget webhook notifier for run completion events."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import threading
from urllib.parse import urlparse
from datetime import datetime, timezone
from typing import TYPE_CHECKING, NamedTuple

# RFC-1918 / loopback / link-local ranges that must never receive outbound webhooks.
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),   # link-local / AWS IMDS
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


def _is_ssrf_target(url: str) -> bool:
    """Return True if the URL resolves to a private/loopback address."""
    try:
        host = urlparse(url).hostname or ""
        addr = ipaddress.ip_address(socket.gethostbyname(host))
        return any(addr in net for net in _BLOCKED_NETWORKS)
    except Exception:
        return True  # block on resolution failure

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger("api.notifier")

# All valid event names
EVENTS = {
    "run.passed",
    "run.failed",
    "run.slow",
    "run.error",
    "run.completed",
    "run.held",
    "run.cancelled",
}


def _status_to_event(status: str) -> list[str]:
    """Map a run status (or event name) to the set of events it should fire."""
    # Allow callers to pass an event name directly (e.g. "run.held")
    if status.lower() in EVENTS:
        return [status.lower()]
    s = status.upper()
    events = ["run.completed"]
    if s == "PASSED":
        events.append("run.passed")
    elif s == "FAILED":
        events.append("run.failed")
    elif s == "SLOW":
        events.append("run.slow")
    elif s == "ERROR":
        events.append("run.error")
    elif s == "CANCELLED":
        events.append("run.cancelled")
    return events


class DeliveryResult(NamedTuple):
    ok: bool
    status_code: int | None = None
    response_body: str | None = None
    error: str | None = None


def _post(url: str, payload: dict, secret: str | None) -> DeliveryResult:
    """Synchronous HTTP POST. Errors are logged and returned, never raised."""
    if _is_ssrf_target(url):
        logger.warning("Webhook delivery to %s blocked: resolves to a private address", url)
        return DeliveryResult(False, error="Blocked: webhook URL resolves to a private/loopback address")

    try:
        import httpx

        body = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
        if secret:
            sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            headers["X-ETL-Signature"] = f"sha256={sig}"

        with httpx.Client(timeout=10) as client:
            resp = client.post(url, content=body, headers=headers)

            response_body = resp.text[:1000] if resp.text else None
            if resp.status_code >= 400:
                error = f"HTTP {resp.status_code}: {resp.reason_phrase}"
                logger.warning("Webhook %s returned %s", url, resp.status_code)
                return DeliveryResult(False, resp.status_code, response_body, error)
            return DeliveryResult(True, resp.status_code, response_body)

    except Exception as exc:
        logger.warning("Webhook delivery to %s failed: %s", url, exc)
        return DeliveryResult(False, error=str(exc)[:500])


def _post_and_track(
    url: str,
    payload: dict,
    secret: str | None,
    delivery_id: int,
) -> None:
    """Deliver a webhook and finalize tracking in a thread-owned DB session."""
    result = _post(url, payload, secret)
    try:
        from etl_framework.repository.database import SessionLocal
        from etl_framework.repository.repository import NotificationDeliveryRepository

        with SessionLocal() as db:
            NotificationDeliveryRepository(db).update_delivery_status(
                delivery_id=delivery_id,
                status="success" if result.ok else "failed",
                error_message=result.error,
                response_status_code=result.status_code,
                response_body=result.response_body,
            )
    except Exception as exc:
        logger.warning("Could not update webhook delivery %s: %s", delivery_id, exc)


def notify(
    run_id: str,
    status: str,
    extra: dict | None = None,
    hooks: list | None = None,
    db_session: "Session | None" = None,
) -> None:
    """Send webhook notifications for a run completion (non-blocking)."""
    if not hooks:
        return

    fired_events = _status_to_event(status)
    payload = {
        "run_id": run_id,
        "status": status,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        **(extra or {}),
    }

    delivery_repo = None
    if db_session is not None:
        from etl_framework.repository.repository import NotificationDeliveryRepository
        delivery_repo = NotificationDeliveryRepository(db_session)

    from api.services.secret_store import decrypt_secret

    for hook in hooks:
        if not hook.enabled:
            continue
        hook_events = hook.events or []
        if not any(e in fired_events for e in hook_events):
            continue
        for event in fired_events:
            if event in hook_events:
                p = {**payload, "event": event}
                hook_secret = decrypt_secret(hook.secret)

                delivery_id = None
                if delivery_repo:
                    delivery_attempt = delivery_repo.create_delivery_attempt(
                        hook_id=hook.id,
                        run_id=run_id,
                        event=event
                    )
                    delivery_id = delivery_attempt.id

                target = _post_and_track if delivery_id is not None else _post
                args = ((hook.url, p, hook_secret, delivery_id)
                        if delivery_id is not None else (hook.url, p, hook_secret))
                t = threading.Thread(
                    target=target,
                    args=args,
                    daemon=True
                )
                t.start()
                break  # one notification per hook per run
