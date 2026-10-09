"""Cross-field rules for file server profiles. Pure: takes the merged profile
(the stored row overlaid with the request body) as a dict and returns a list
of messages, because an update may send only some fields."""
from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlsplit

_HTTP_TOKEN_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
# Headers the HTTP client must own; letting a profile set them would corrupt
# the request framing or the session cookie.
_FORBIDDEN_HEADERS = frozenset({"cookie", "host", "content-length", "transfer-encoding"})
_API_KEY_SCOPE_ERROR = "api_key and api_key_header are only valid for a crushftp profile using https"


def insecure_http_allowed() -> bool:
    """Plain http:// CrushFTP URLs are refused unless this is set -- it exists
    for a local Docker container, never for a real server."""
    return os.environ.get("CRUSHFTP_ALLOW_INSECURE_HTTP") == "1"


def file_server_profile_errors(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if data.get("kind") != "crushftp":
        if data.get("api_key") or data.get("api_key_header"):
            errors.append(_API_KEY_SCOPE_ERROR)
        return errors

    protocol = data.get("protocol")
    auth_method = data.get("auth_method")
    if protocol not in ("https", "sftp"):
        return ["crushftp profile requires protocol 'https' or 'sftp'"]

    if protocol == "sftp":
        if not data.get("host"):
            errors.append("crushftp sftp profile requires host")
        if auth_method not in ("password", "private_key"):
            errors.append("crushftp sftp profile auth_method must be 'password' or 'private_key'")
        if data.get("api_key") or data.get("api_key_header"):
            errors.append(_API_KEY_SCOPE_ERROR)
        return errors

    url = data.get("endpoint_url") or ""
    parts = urlsplit(url)
    if not url:
        errors.append("crushftp https profile requires endpoint_url")
    elif (
        not parts.hostname
        or parts.scheme not in ("https", "http")
        or (parts.scheme == "http" and not insecure_http_allowed())
    ):
        errors.append("crushftp endpoint_url must start with https://")
    if auth_method == "password":
        if not data.get("username"):
            errors.append("crushftp password auth requires username")
        if not data.get("password"):
            errors.append("crushftp password auth requires password")
    elif auth_method == "api_key":
        header = data.get("api_key_header") or ""
        if not _HTTP_TOKEN_RE.match(header) or header.lower() in _FORBIDDEN_HEADERS:
            errors.append("crushftp api_key_header must be a valid HTTP header name")
        if not data.get("api_key"):
            errors.append("crushftp api_key auth requires api_key")
    else:
        errors.append("crushftp https profile auth_method must be 'password' or 'api_key'")
    return errors
