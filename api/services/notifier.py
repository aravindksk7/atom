"""Fire-and-forget webhook notifier for run completion events."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import smtplib
import socket
import threading
from email.message import EmailMessage
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
    "contract.breached",
    "contract.resolved",
    "contract.escalated",
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


def parse_mailto(url: str) -> list[str]:
    """Extract recipient list from a mailto: pseudo-URL, else []."""
    if not url.startswith("mailto:"):
        return []
    return [a.strip() for a in url[len("mailto:"):].split(",") if a.strip()]


_TEMPLATE_VAR_RE = re.compile(r"\{\{(\w+)\}\}")


def render_template(template: str, data: dict) -> str:
    """Replace {{var}} placeholders with values from data. Missing/None vars -> ''."""
    def _sub(match: "re.Match") -> str:
        value = data.get(match.group(1))
        return "" if value is None else str(value)
    return _TEMPLATE_VAR_RE.sub(_sub, template)


def _resolve_smtp_config() -> dict:
    """SMTP settings configured in the web UI win; ETL_SMTP_* env vars are the fallback."""
    try:
        from etl_framework.repository.database import SessionLocal
        from etl_framework.repository.repository import SettingsRepository

        with SessionLocal() as db:
            cfg = SettingsRepository(db).get_smtp_config()
            if cfg["host"]:
                return cfg
    except Exception as exc:
        logger.warning("Could not load SMTP settings from the database, falling back to env vars: %s", exc)

    return {
        "host": os.environ.get("ETL_SMTP_HOST", ""),
        "port": int(os.environ.get("ETL_SMTP_PORT", "25")),
        "from_addr": os.environ.get("ETL_SMTP_FROM", "etl-framework@localhost"),
        "user": os.environ.get("ETL_SMTP_USER", ""),
        "password": os.environ.get("ETL_SMTP_PASSWORD", ""),
        "use_tls": os.environ.get("ETL_SMTP_STARTTLS", "").lower() in ("1", "true"),
    }


def _send_email(recipients: list[str], subject: str, body: str, config: dict | None = None) -> DeliveryResult:
    """Synchronous SMTP send. Errors are logged and returned, never raised."""
    cfg = config if config is not None else _resolve_smtp_config()
    host = cfg.get("host", "")
    if not host:
        return DeliveryResult(False, error="SMTP is not configured — set it under Settings, or ETL_SMTP_HOST")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.get("from_addr") or "etl-framework@localhost"
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    try:
        with smtplib.SMTP(host, int(cfg.get("port") or 25), timeout=10) as server:
            if cfg.get("use_tls"):
                server.starttls()
            if cfg.get("user"):
                server.login(cfg["user"], cfg.get("password") or "")
            server.send_message(msg)
        return DeliveryResult(True)
    except Exception as exc:
        logger.warning("Email delivery to %s failed: %s", recipients, exc)
        return DeliveryResult(False, error=str(exc)[:500])


def _track_delivery(delivery_id: int, result: DeliveryResult) -> None:
    """Finalize a delivery attempt in a thread-owned DB session."""
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
        logger.warning("Could not update delivery %s: %s", delivery_id, exc)


def _post_and_track(url: str, payload: dict, secret: str | None, delivery_id: int) -> None:
    _track_delivery(delivery_id, _post(url, payload, secret))


def _send_email_and_track(recipients: list[str], subject: str, body: str, config: dict, delivery_id: int) -> None:
    _track_delivery(delivery_id, _send_email(recipients, subject, body, config))


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

                delivery_id = None
                if delivery_repo:
                    delivery_attempt = delivery_repo.create_delivery_attempt(
                        hook_id=hook.id,
                        run_id=run_id,
                        event=event
                    )
                    delivery_id = delivery_attempt.id

                if getattr(hook, "channel", "generic") == "email":
                    recipients = parse_mailto(hook.url)
                    if not recipients:
                        logger.warning("Email hook %s has no valid mailto: recipients", hook.id)
                        break
                    subject_template = getattr(hook, "subject_template", None)
                    body_template = getattr(hook, "body_template", None)
                    subject = render_template(subject_template, p) if subject_template \
                        else f"ETL run {p.get('status', '')}: {run_id}"
                    body = render_template(body_template, p) if body_template \
                        else json.dumps(p, indent=2, default=str)
                    smtp_config = _resolve_smtp_config()
                    target = _send_email_and_track if delivery_id is not None else _send_email
                    args = ((recipients, subject, body, smtp_config, delivery_id)
                            if delivery_id is not None else (recipients, subject, body, smtp_config))
                else:
                    hook_secret = decrypt_secret(hook.secret)
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
