from __future__ import annotations

from etl_framework.config.models import ApiEndpointEntry
from api.services.api_exchange import redact_headers


def _entry(**overrides) -> ApiEndpointEntry:
    base = {"base_url": "https://api.example.com/v1/orders"}
    base.update(overrides)
    return ApiEndpointEntry(**base)


def test_authorization_is_redacted():
    out = redact_headers({"Authorization": "Bearer abcdefghijklmnop"}, _entry())
    assert out["Authorization"] == "<23 chars, ...mnop>"


def test_redaction_is_case_insensitive():
    out = redact_headers({"authorization": "Bearer abcdefghijklmnop"}, _entry())
    assert out["authorization"].startswith("<23 chars")


def test_cookie_and_logon_token_are_redacted():
    out = redact_headers(
        {"Cookie": "session=aaaabbbb", "X-SAP-LogonToken": "tokenvalue1234"}, _entry()
    )
    assert out["Cookie"].startswith("<")
    assert out["X-SAP-LogonToken"].startswith("<")


def test_configured_api_key_header_is_redacted():
    entry = _entry(auth_type="api_key", api_key_header="X-Custom-Token", api_key="s3cret-value")
    out = redact_headers({"X-Custom-Token": "s3cret-value"}, entry)
    assert out["X-Custom-Token"] == "<12 chars, ...alue>"


def test_ordinary_headers_pass_through():
    out = redact_headers({"Content-Type": "application/json"}, _entry())
    assert out["Content-Type"] == "application/json"


def test_short_secret_does_not_leak_the_whole_value():
    out = redact_headers({"Authorization": "abc"}, _entry())
    assert "abc" not in out["Authorization"]
