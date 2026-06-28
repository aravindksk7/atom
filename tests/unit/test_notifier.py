"""Tests for the webhook notifier service."""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from api.services.notifier import notify, _status_to_event, _post, EVENTS


# ---------------------------------------------------------------------------
# _status_to_event
# ---------------------------------------------------------------------------

def test_passed_fires_run_passed_and_completed():
    events = _status_to_event("PASSED")
    assert "run.passed" in events
    assert "run.completed" in events


def test_failed_fires_run_failed_and_completed():
    events = _status_to_event("FAILED")
    assert "run.failed" in events
    assert "run.completed" in events


def test_error_fires_run_error_and_completed():
    events = _status_to_event("ERROR")
    assert "run.error" in events
    assert "run.completed" in events


def test_slow_fires_run_slow_and_completed():
    events = _status_to_event("SLOW")
    assert "run.slow" in events
    assert "run.completed" in events


# ---------------------------------------------------------------------------
# notify()
# ---------------------------------------------------------------------------

def _make_hook(url: str, events: list[str], secret: str | None = None,
               enabled: bool = True):
    h = MagicMock()
    h.url = url
    h.events = events
    h.secret = secret
    h.enabled = enabled
    return h


def test_notify_no_hooks_does_nothing():
    # Should not raise
    notify("run-1", "PASSED", hooks=[])
    notify("run-1", "PASSED", hooks=None)


def test_notify_disabled_hook_is_skipped():
    posted = []
    with patch("api.services.notifier._post", side_effect=lambda url, p, s: posted.append(url)):
        hook = _make_hook("http://example.com", ["run.passed"], enabled=False)
        notify("run-1", "PASSED", hooks=[hook])
        time.sleep(0.05)
    assert posted == []


def test_notify_event_mismatch_is_skipped():
    posted = []
    with patch("api.services.notifier._post", side_effect=lambda url, p, s: posted.append(url)):
        hook = _make_hook("http://example.com", ["run.failed"])
        notify("run-1", "PASSED", hooks=[hook])
        time.sleep(0.05)
    assert posted == []


def test_notify_matching_event_calls_post():
    called = threading.Event()
    captured = {}

    def fake_post(url, payload, secret):
        captured["url"] = url
        captured["event"] = payload.get("event")
        called.set()

    with patch("api.services.notifier._post", side_effect=fake_post):
        hook = _make_hook("http://hook.test/cb", ["run.completed", "run.passed"])
        notify("run-xyz", "PASSED", hooks=[hook])
        called.wait(timeout=1)

    assert captured["url"] == "http://hook.test/cb"
    assert captured["event"] in ("run.passed", "run.completed")


# ---------------------------------------------------------------------------
# HMAC signing in _post
# ---------------------------------------------------------------------------

def test_post_adds_hmac_signature_when_secret_set():
    captured_headers = {}

    def fake_client_post(url, content, headers):
        captured_headers.update(headers)
        resp = MagicMock()
        resp.status_code = 200
        return resp

    mock_client = MagicMock()
    mock_client.__enter__ = lambda s: s
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post = fake_client_post

    with patch("httpx.Client", return_value=mock_client):
        _post("http://example.com", {"event": "run.passed"}, secret="mysecret")

    sig_header = captured_headers.get("X-ETL-Signature", "")
    assert sig_header.startswith("sha256=")

    # Verify the signature is correct
    body = json.dumps({"event": "run.passed"}).encode()
    expected = "sha256=" + hmac.new(b"mysecret", body, hashlib.sha256).hexdigest()
    assert sig_header == expected


def test_post_no_signature_when_no_secret():
    captured_headers = {}

    def fake_post(url, content, headers):
        captured_headers.update(headers)
        resp = MagicMock()
        resp.status_code = 200
        return resp

    mock_client = MagicMock()
    mock_client.__enter__ = lambda s: s
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post = fake_post

    with patch("httpx.Client", return_value=mock_client):
        _post("http://example.com", {"event": "run.passed"}, secret=None)

    assert "X-ETL-Signature" not in captured_headers


def test_post_does_not_raise_on_http_error():
    with patch("httpx.Client") as mock_cls:
        instance = MagicMock()
        instance.__enter__ = lambda s: s
        instance.__exit__ = MagicMock(return_value=False)
        instance.post.side_effect = Exception("connection refused")
        mock_cls.return_value = instance
        # Must not raise
        _post("http://bad-url.test", {}, None)


# ---------------------------------------------------------------------------
# Contract event types
# ---------------------------------------------------------------------------

def test_contract_breached_is_valid_event():
    assert "contract.breached" in EVENTS


def test_contract_resolved_is_valid_event():
    assert "contract.resolved" in EVENTS


def test_contract_escalated_is_valid_event():
    assert "contract.escalated" in EVENTS


def test_contract_event_passes_through_status_to_event():
    assert _status_to_event("contract.breached") == ["contract.breached"]
    assert _status_to_event("contract.resolved") == ["contract.resolved"]
    assert _status_to_event("contract.escalated") == ["contract.escalated"]
