"""Fire-and-forget webhook notifier for run completion events."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
from datetime import datetime, timezone

logger = logging.getLogger("api.notifier")

# All valid event names
EVENTS = {
    "run.passed",
    "run.failed",
    "run.slow",
    "run.error",
    "run.completed",
}


def _status_to_event(status: str) -> list[str]:
    """Map a run status to the set of events it should fire."""
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
    return events


def _post(url: str, payload: dict, secret: str | None) -> None:
    """Synchronous HTTP POST — runs in a daemon thread."""
    try:
        import httpx

        body = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
        if secret:
            sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            headers["X-ETL-Signature"] = f"sha256={sig}"

        with httpx.Client(timeout=10) as client:
            resp = client.post(url, content=body, headers=headers)
            if resp.status_code >= 400:
                logger.warning("Webhook %s returned %s", url, resp.status_code)
    except Exception as exc:
        logger.warning("Webhook delivery to %s failed: %s", url, exc)


def notify(
    run_id: str,
    status: str,
    extra: dict | None = None,
    hooks: list | None = None,
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

    for hook in hooks:
        if not hook.enabled:
            continue
        hook_events = hook.events or []
        if not any(e in fired_events for e in hook_events):
            continue
        for event in fired_events:
            if event in hook_events:
                p = {**payload, "event": event}
                t = threading.Thread(
                    target=_post, args=(hook.url, p, hook.secret), daemon=True
                )
                t.start()
                break  # one notification per hook per run
