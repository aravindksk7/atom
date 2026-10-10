"""Client for a CrushFTP server's HTTP(S) WebInterface API.

Every piece of CrushFTP wire knowledge lives in this module. The requests it
makes follow what was observed on CrushFTP 11.5.5 -- see section 10 of
docs/superpowers/specs/2026-10-10-crushftp-file-server-design.md.

Two ways to authenticate, which may be combined: a username and password
(session login), and an API key sent in a configurable header on every request
(for an instance behind a gateway that checks the key).
"""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import tempfile
from dataclasses import dataclass
from typing import Any, Iterator
from urllib.parse import quote, unquote, urlsplit

import requests

from api.services.file_server_validation import insecure_http_allowed

_FUNCTION_PATH = "/WebInterface/function/"
_RESPONSE_RE = re.compile(r"<response>(.*?)</response>", re.DOTALL)
_C2F_RE = re.compile(r"<c2f>(.*?)</c2f>")
_MD5_RE = re.compile(r"^[0-9a-f]{32}$")
_UPLOAD_BLOCK = 65536


class CrushFtpConnectError(RuntimeError):
    """Raised only for connection-establishment and authentication failures
    (unreachable host, TLS failure, rejected login or API key, expired
    session, a redirect). ``file_transfer.is_transport_error`` treats it as a
    transport error."""


@dataclass(frozen=True)
class CrushFtpEntry:
    name: str
    path: str
    size: int
    modified: float  # seconds since the epoch
    is_dir: bool


class _Reader:
    """File-like view of a streamed download, optionally capped at ``limit``
    bytes. ``close()`` releases the connection."""

    def __init__(self, response: requests.Response, limit: int | None) -> None:
        self._response = response
        self._remaining = limit

    def read(self, size: int = -1) -> bytes:
        if self._remaining is not None:
            size = self._remaining if size is None or size < 0 else min(size, self._remaining)
        if size == 0:
            return b""
        data = self._response.raw.read(None if size is None or size < 0 else size, decode_content=True)
        if self._remaining is not None:
            self._remaining -= len(data)
        return data

    def close(self) -> None:
        self._response.close()

    def __enter__(self) -> "_Reader":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class CrushFtpHttpClient:
    def __init__(
        self,
        base_url: str,
        *,
        username: str | None = None,
        password: str | None = None,
        api_key_header: str | None = None,
        api_key: str | None = None,
        ca_bundle_pem: str | None = None,
        timeout: tuple[float, float] = (10, 120),
    ) -> None:
        self._base = base_url.rstrip("/")
        parts = urlsplit(self._base)
        self._origin = f"{parts.scheme}://{parts.netloc}"
        self._username = username
        self._password = password
        self._api_key = api_key if api_key_header else None
        self._timeout = timeout
        self._c2f: str | None = None
        self._logged_in = False
        self._closed = False
        self._ca_path: str | None = None
        # verify is the system trust store or a private CA, never False.
        self._verify: bool | str = True
        if ca_bundle_pem and ca_bundle_pem.strip():
            handle = tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False, encoding="ascii")
            with handle:
                handle.write(ca_bundle_pem)
            self._ca_path = handle.name
            self._verify = handle.name
        self._session = requests.Session()
        if api_key_header and api_key:
            self._session.headers[api_key_header] = api_key

    # -- plumbing -----------------------------------------------------------
    def _scrub(self, text: str) -> str:
        """Remove every credential this client holds from ``text`` before it
        can reach an exception message, a log line, or a run result."""
        secrets = [self._password, self._api_key]
        try:
            secrets.append(self._session.cookies.get("CrushAuth"))
        except Exception:
            pass
        for secret in secrets:
            if secret:
                text = text.replace(secret, "***").replace(quote(secret, safe=""), "***")
        return text

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        try:
            response = self._session.request(
                method, url, allow_redirects=False, timeout=self._timeout, verify=self._verify, **kwargs,
            )
        except requests.RequestException as exc:
            raise CrushFtpConnectError(
                self._scrub(f"CrushFTP request to {self._origin} failed: {type(exc).__name__}: {exc}")
            ) from None
        if 300 <= response.status_code < 400:
            # Never followed: a redirect could carry the API key header to
            # another host, and the server redirects a rejected or expired
            # session to its login page.
            location = response.headers.get("Location", "")
            response.close()
            raise CrushFtpConnectError(self._scrub(
                f"CrushFTP at {self._origin} redirected to '{location}' -- the session was rejected or has "
                "expired, or the base URL is wrong (redirects are never followed)"
            ))
        if response.status_code == 401 or (response.status_code == 403 and method != "PUT"):
            response.close()
            raise CrushFtpConnectError(
                f"CrushFTP at {self._origin} rejected the credentials (HTTP {response.status_code})"
            )
        return response

    def _url(self, path: str) -> str:
        return self._base + quote("/" + path.lstrip("/"), safe="/")

    def _current_c2f(self) -> str | None:
        if self._c2f:
            return self._c2f
        try:
            cookie = self._session.cookies.get("CrushAuth")
        except Exception:
            cookie = None
        return cookie[-4:] if cookie else None

    def _command(self, command: str, **fields: str) -> requests.Response:
        data = {"command": command, **fields}
        c2f = self._current_c2f()
        if c2f and command != "login":
            data["c2f"] = c2f
        # Percent-encoded by hand: the server does not decode "+" as a space,
        # which is how requests would encode one.
        body = "&".join(f"{quote(key, safe='')}={quote(value, safe='')}" for key, value in data.items())
        response = self._request(
            "POST", self._base + _FUNCTION_PATH, data=body.encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code == 404:
            raise CrushFtpConnectError(
                f"CrushFTP at {self._origin} did not accept the '{command}' command -- "
                "the session was rejected or has expired"
            )
        if response.status_code != 200:
            raise OSError(self._scrub(f"CrushFTP '{command}' failed with HTTP {response.status_code}"))
        return response

    @staticmethod
    def _response_text(text: str) -> str:
        match = _RESPONSE_RE.search(text)
        return unquote(match.group(1)).strip() if match else text.strip()

    def _mutate(self, command: str, description: str, **fields: str) -> None:
        """Run a command whose success is an empty ``<response>``."""
        message = self._response_text(self._command(command, **fields).text)
        if not message:
            return
        if "(c2f)" in message:
            raise CrushFtpConnectError(f"CrushFTP at {self._origin} rejected the session token for '{command}'")
        raise OSError(self._scrub(f"CrushFTP could not {description}: {message}"))

    # -- session ------------------------------------------------------------
    def login(self) -> None:
        """Open a session when a username is configured. Without one (API key
        only) there is nothing to do: the key travels on every request."""
        if not self._username:
            return
        response = self._command("login", username=self._username, password=self._password or "")
        if self._response_text(response.text) != "success":
            raise CrushFtpConnectError(
                f"CrushFTP at {self._origin} rejected the login for user '{self._username}'"
            )
        match = _C2F_RE.search(response.text)
        self._c2f = match.group(1) if match else None
        self._logged_in = True

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._logged_in:
                self._command("logout").close()
        except Exception:
            pass  # best effort: the session expires on its own
        finally:
            self._session.close()
            if self._ca_path:
                try:
                    os.unlink(self._ca_path)
                except OSError:
                    pass

    # -- reads --------------------------------------------------------------
    def list(self, path: str) -> list[CrushFtpEntry]:
        directory = "/" + path.strip("/")
        requested = directory if directory == "/" else directory + "/"
        response = self._command("getXMLListing", path=requested, format="JSONOBJ")
        try:
            payload = json.loads(response.text)
        except ValueError:
            raise CrushFtpConnectError(
                f"CrushFTP at {self._origin} returned an unexpected response to a directory listing"
            ) from None
        if not isinstance(payload, dict) or "listing" not in payload:
            raise FileNotFoundError(f"CrushFTP directory not found: {requested}")
        # An account with a single virtual folder gets that folder's contents
        # for "/", so the directory the entries live in comes from the response.
        actual = str(payload.get("path") or requested)
        entries: list[CrushFtpEntry] = []
        for item in payload["listing"] or []:
            # The listing escapes "%" in names (and nothing else).
            name = str(item.get("name") or "").replace("%25", "%")
            if not name:
                continue
            entries.append(CrushFtpEntry(
                name=name,
                path=posixpath.join(actual, name),
                size=int(item.get("size") or 0),
                modified=float(item.get("modified") or 0) / 1000.0,
                is_dir=str(item.get("type") or "").upper() == "DIR",
            ))
        return entries

    def _head(self, path: str) -> requests.Response | None:
        response = self._request("HEAD", self._url(path))
        response.close()
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise OSError(f"CrushFTP HEAD {path} failed with HTTP {response.status_code}")
        return response

    def exists(self, path: str) -> bool:
        return self._head(path) is not None

    def size(self, path: str) -> int:
        response = self._head(path)
        if response is None:
            raise FileNotFoundError(f"CrushFTP file not found: {path}")
        return int(response.headers.get("Content-Length") or 0)

    def open_read(self, path: str, limit: int | None = None) -> _Reader:
        headers = {"Range": f"bytes=0-{limit - 1}"} if limit else {}
        response = self._request("GET", self._url(path), stream=True, headers=headers)
        if response.status_code == 404:
            response.close()
            raise FileNotFoundError(f"CrushFTP file not found: {path}")
        if response.status_code not in (200, 206):
            response.close()
            raise OSError(f"CrushFTP download of {path} failed with HTTP {response.status_code}")
        return _Reader(response, limit)

    # -- writes -------------------------------------------------------------
    @staticmethod
    def _refuse_percent(path: str) -> None:
        if "%" in path:
            # Upload, listing and rename each treat "%" differently on the
            # server, so such a name cannot be written reliably.
            raise OSError(f"CrushFTP paths containing '%' cannot be written: {path}")

    def upload(self, path: str, stream: Any) -> None:
        self._refuse_percent(path)
        digest = hashlib.md5()  # noqa: S324 -- matches the checksum the server returns, not a security control

        def blocks() -> Iterator[bytes]:
            while True:
                block = stream.read(_UPLOAD_BLOCK)
                if not block:
                    return
                digest.update(block)
                yield block

        response = self._request("PUT", self._url(path), data=blocks())
        body = response.text.strip().lower()
        if response.status_code == 403:
            raise PermissionError(
                f"CrushFTP refused the upload to {path} -- the directory is missing or the account cannot write there"
            )
        if response.status_code not in (200, 201):
            raise OSError(f"CrushFTP upload to {path} failed with HTTP {response.status_code}")
        # The server answers with the MD5 of what it stored.
        if _MD5_RE.match(body) and body != digest.hexdigest():
            raise OSError(f"CrushFTP upload to {path} failed: checksum mismatch between sent and stored bytes")

    def mkdir(self, path: str) -> None:
        directory = "/" + path.strip("/")
        if directory == "/":
            return
        self._refuse_percent(directory)
        self._mutate("makedir", f"create directory {directory}", path=directory + "/")

    def rename(self, source: str, destination: str) -> None:
        self._refuse_percent(source)
        self._refuse_percent(destination)
        directory = posixpath.dirname(source)
        relative = posixpath.relpath(destination, directory)
        if relative.startswith(".."):
            raise OSError(f"CrushFTP rename must stay under {directory}: {destination}")
        self._mutate(
            "rename", f"rename {source} to {destination}",
            path=directory.rstrip("/") + "/", name1=posixpath.basename(source), name2=relative,
        )

    def delete(self, path: str) -> None:
        target = "/" + path.strip("/")
        if target == "/":
            raise OSError("refusing to delete the CrushFTP root")
        self._mutate("delete", f"delete {target}", names=target)


def build_crushftp_client(profile: Any, spec: Any) -> CrushFtpHttpClient:
    """Client for a ``crushftp`` profile using the https protocol, already
    logged in when the profile has a username."""
    if profile is None:
        raise ValueError(f"'{spec.kind}' source requires credentials_ref, but none was set")
    url = profile.endpoint_url or ""
    scheme = urlsplit(url).scheme
    if scheme != "https" and not (scheme == "http" and insecure_http_allowed()):
        raise CrushFtpConnectError(
            f"File server profile '{profile.name}' needs a base URL starting with https://"
        )
    use_key = profile.auth_method == "api_key"
    client = CrushFtpHttpClient(
        url,
        username=profile.username,
        password=profile.password,
        api_key_header=profile.api_key_header if use_key else None,
        api_key=profile.api_key if use_key else None,
        ca_bundle_pem=profile.tls_ca_bundle,
    )
    try:
        client.login()
    except Exception:
        client.close()
        raise
    return client
