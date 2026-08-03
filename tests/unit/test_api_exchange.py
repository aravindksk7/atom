from __future__ import annotations

import base64
import logging
from unittest.mock import MagicMock

from etl_framework.config.models import ApiEndpointEntry
from api.services.api_exchange import (
    BODY_LIMIT,
    capture_exchange,
    redact_headers,
    render_body,
    render_request_body,
)


def _entry(**overrides) -> ApiEndpointEntry:
    base = {"base_url": "https://api.example.com/v1/orders"}
    base.update(overrides)
    return ApiEndpointEntry(**base)


def test_authorization_is_redacted():
    out = redact_headers({"Authorization": "Bearer abcdefghijklmnop"}, _entry())
    assert out["Authorization"] == "<23 chars, redacted>"


def test_redaction_is_case_insensitive():
    out = redact_headers({"authorization": "Bearer abcdefghijklmnop"}, _entry())
    assert out["authorization"] == "<23 chars, redacted>"


def test_cookie_and_logon_token_are_redacted():
    out = redact_headers(
        {"Cookie": "session=aaaabbbb", "X-SAP-LogonToken": "tokenvalue1234"}, _entry()
    )
    assert out["Cookie"].startswith("<")
    assert out["X-SAP-LogonToken"].startswith("<")


def test_configured_api_key_header_is_redacted():
    entry = _entry(auth_type="api_key", api_key_header="X-Custom-Token", api_key="s3cret-value")
    out = redact_headers({"X-Custom-Token": "s3cret-value"}, entry)
    assert out["X-Custom-Token"] == "<12 chars, redacted>"


def test_ordinary_headers_pass_through():
    out = redact_headers({"Content-Type": "application/json"}, _entry())
    assert out["Content-Type"] == "application/json"


def test_short_secret_does_not_leak_the_whole_value():
    out = redact_headers({"Authorization": "abc"}, _entry())
    assert "abc" not in out["Authorization"]


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
    assert render_body(b"", "application/json") == ("", False, False)


def _response(status=200, body=b'{"a":1}', content_type="application/json",
              req_body=b'{"filter":"recent"}', req_headers=None):
    request = MagicMock()
    request.method = "POST"
    request.url = "https://api.example.com/v1/orders/search?region=us"
    request.headers = req_headers if req_headers is not None else {"Content-Type": "application/json"}
    request.body = req_body

    resp = MagicMock()
    resp.request = request
    resp.status_code = status
    resp.content = body
    resp.headers = {"Content-Type": content_type}
    resp.history = []
    elapsed = MagicMock()
    elapsed.total_seconds.return_value = 0.412
    resp.elapsed = elapsed
    return resp


def test_capture_records_request_and_response():
    sink, seen = capture_exchange(_entry())
    sink(b'{"a":1}', 1, _response())
    assert len(seen) == 1
    exchange = seen[0]
    assert exchange["request"]["method"] == "POST"
    assert exchange["request"]["url"].endswith("?region=us")
    assert exchange["request"]["body"] == '{"filter":"recent"}'
    assert exchange["response"]["status"] == 200
    assert exchange["response"]["elapsed_ms"] == 412
    assert exchange["response"]["bytes"] == 7


def test_capture_records_a_missing_request_body_as_null():
    """The dropped-body case: this is what makes it visible."""
    sink, seen = capture_exchange(_entry())
    sink(b"{}", 1, _response(req_body=None))
    assert seen[0]["request"]["body"] is None


def test_capture_redacts_request_headers():
    sink, seen = capture_exchange(_entry())
    sink(b"{}", 1, _response(req_headers={"Authorization": "Bearer abcdefghijkl"}))
    assert seen[0]["request"]["headers"]["Authorization"].startswith("<")


def test_capture_counts_redirects():
    resp = _response()
    resp.history = [MagicMock(), MagicMock()]
    sink, seen = capture_exchange(_entry())
    sink(b"{}", 1, resp)
    assert seen[0]["response"]["redirects"] == 2


def test_capture_never_raises_on_a_malformed_response():
    sink, seen = capture_exchange(_entry())
    sink(b"{}", 1, object())  # no .request, no .headers
    assert seen == []


def test_free_form_secret_header_is_redacted():
    """The gap the allowlist left open: a secret under a name nobody enumerated."""
    out = redact_headers({"X-Client-Secret": "HUNTER2-SECRET-VALUE"}, _entry())
    assert "HUNTER2" not in out["X-Client-Secret"]
    assert out["X-Client-Secret"].startswith("<")


def test_version_header_is_not_redacted():
    out = redact_headers({"X-Api-Version": "2026-08-01"}, _entry())
    assert out["X-Api-Version"] == "2026-08-01"


def test_idempotency_key_is_redacted_as_an_accepted_false_positive():
    """Not a credential, but 'key' matches. Over-redaction is the deliberate trade."""
    out = redact_headers({"Idempotency-Key": "abc123def456"}, _entry())
    assert out["Idempotency-Key"].startswith("<")


def test_pattern_match_is_case_insensitive():
    out = redact_headers({"X-SESSION-ID": "sessionvalue99"}, _entry())
    assert out["X-SESSION-ID"].startswith("<")


def test_comparison_relevant_headers_stay_visible():
    headers = {
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate",
        "User-Agent": "python-requests/2.31.0",
        "Content-Length": "19",
    }
    assert redact_headers(headers, _entry()) == headers


class _ExplodingResponse:
    """Valid until `elapsed`, so it fails deeper in the sink than `object()` does."""

    def __init__(self):
        self.request = _response().request
        self.status_code = 200
        self.content = b"{}"
        self.headers = {"Content-Type": "application/json"}
        self.history = []

    @property
    def elapsed(self):
        raise RuntimeError("elapsed blew up")


def test_capture_logs_a_traceback_instead_of_swallowing_silently(caplog):
    sink, seen = capture_exchange(_entry())
    with caplog.at_level(logging.WARNING, logger="api.services.api_exchange"):
        sink(b"{}", 1, _ExplodingResponse())  # must not raise
    assert seen == []
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].exc_info is not None


def test_capture_uses_raw_bytes_not_response_content():
    """`raw_bytes` is the contract; `response.content` merely happens to match."""
    sink, seen = capture_exchange(_entry())
    sink(b'{"from":"raw_bytes"}', 1, _response(body=b"UNUSED"))
    assert seen[0]["response"]["body"] == '{"from":"raw_bytes"}'
    assert seen[0]["response"]["bytes"] == 20


def test_missing_content_type_renders_as_text():
    """The headline scenario: no declared type. Hex would be unreadable."""
    body, truncated, binary = render_body(b'{"error":"nope"}', None)
    assert binary is False
    assert body == '{"error":"nope"}'


def test_empty_content_type_renders_as_text():
    body, truncated, binary = render_body(b'{"error":"nope"}', "")
    assert binary is False
    assert body == '{"error":"nope"}'


def test_problem_json_is_textual():
    """RFC 7807 - the likeliest content type on the failure being debugged."""
    body, truncated, binary = render_body(b'{"title":"Bad Gateway"}', "application/problem+json")
    assert binary is False
    assert body == '{"title":"Bad Gateway"}'


def test_structured_suffix_types_are_textual():
    for content_type in (
        "application/xhtml+xml",
        "application/vnd.api+json",
        "application/hal+json",
        "application/ld+json",
    ):
        body, _truncated, binary = render_body(b"<hi/>", content_type)
        assert binary is False, content_type


def test_mask_never_emits_credential_bytes_for_short_base64_passwords():
    """`pw` -> `Basic cHc=`; a 4-char tail is a whole base64 quad decoding to `pw`."""
    for password in ("pw", "abc"):
        header = "Basic " + base64.b64encode(f"u:{password}".encode()).decode()
        masked = redact_headers({"Authorization": header}, _entry())["Authorization"]
        assert masked == f"<{len(header)} chars, redacted>"
        # Nothing in the output may decode back to any part of the credential.
        assert password not in masked
        tail = masked.split("redacted")[0]
        assert base64.b64encode(password.encode()).decode() not in tail
        for quad_start in range(0, len(header), 4):
            quad = header[quad_start:quad_start + 4]
            if len(quad) == 4 and quad not in ("Basi",):
                assert quad not in masked


def test_mask_shows_length_so_a_length_diff_is_still_possible():
    out = redact_headers({"Authorization": "Bearer " + "x" * 40}, _entry())
    assert out["Authorization"] == "<47 chars, redacted>"


def test_widened_patterns_catch_bearer_and_jwt_headers():
    out = redact_headers({"X-Bearer": "abcdefgh", "X-Jwt": "ey.abc.def"}, _entry())
    assert out["X-Bearer"].startswith("<")
    assert out["X-Jwt"].startswith("<")


def test_widened_patterns_catch_the_remaining_names():
    names = ["X-Hmac", "X-Nonce", "X-Api-Sig", "X-Otp", "X-Private-Cert", "X-Tenant-Pw"]
    out = redact_headers({n: "credentialvalue" for n in names}, _entry())
    for name in names:
        assert out[name] == "<15 chars, redacted>", name


def test_request_body_is_truncated_to_the_body_limit():
    body, truncated = render_request_body(b"x" * (BODY_LIMIT + 5000))
    assert len(body) == BODY_LIMIT
    assert truncated is True


def test_request_body_string_is_truncated_too():
    body, truncated = render_request_body("y" * (BODY_LIMIT + 10))
    assert len(body) == BODY_LIMIT
    assert truncated is True


def test_request_body_none_stays_none():
    assert render_request_body(None) == (None, False)


def test_capture_reports_request_body_truncation():
    sink, seen = capture_exchange(_entry())
    sink(b"{}", 1, _response(req_body=b"z" * (BODY_LIMIT + 1)))
    assert seen[0]["request"]["truncated"] is True
    assert len(seen[0]["request"]["body"]) == BODY_LIMIT


def test_capture_marks_a_short_request_body_untruncated():
    sink, seen = capture_exchange(_entry())
    sink(b"{}", 1, _response())
    assert seen[0]["request"]["truncated"] is False


def test_body_limit_is_a_byte_budget_not_a_character_budget():
    """A 15 KB CJK body previously decoded whole and reported truncated=False."""
    raw = "漢".encode() * 5000  # 15,000 bytes, 5,000 characters
    assert len(raw) > BODY_LIMIT
    body, truncated, binary = render_body(raw, "application/json")
    assert truncated is True
    # Only the byte budget was decoded, not all 5,000 characters. (Re-encoding
    # the result can exceed BODY_LIMIT by a byte or two, because a straddling
    # character at the cut becomes a 3-byte U+FFFD; the bound that matters is
    # on the input consumed.)
    assert len(body) <= BODY_LIMIT
    assert len(body) < 5000
