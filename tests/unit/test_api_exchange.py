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


from api.services.api_exchange import BODY_LIMIT, render_body


def test_text_body_passes_through():
    body, truncated, binary = render_body(b'{"a":1}', "application/json")
    assert body == '{"a":1}'
    assert truncated is False
    assert binary is False


def test_long_body_is_truncated():
    body, truncated, binary = render_body(b"x" * (BODY_LIMIT + 100), "text/plain")
    assert len(body) == BODY_LIMIT
    assert truncated is True


def test_binary_body_is_hex_not_decoded():
    body, truncated, binary = render_body(b"\x89PNG\r\n\x1a\n", "image/png")
    assert binary is True
    assert body.startswith("89504e47")


def test_undecodable_text_body_falls_back_to_replacement():
    body, truncated, binary = render_body(b"\xff\xfe bad", "text/plain")
    assert binary is False
    assert "�" in body


def test_empty_body_is_empty_string_not_none():
    body, truncated, binary = render_body(b"", "application/json")
    assert body == ""
