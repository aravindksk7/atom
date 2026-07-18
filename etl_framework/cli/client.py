"""HTTP client for the Atom API."""
from __future__ import annotations

from typing import Any

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


class AtomAPIError(Exception):
    """Generic Atom API failure."""


class AtomConnectionError(AtomAPIError):
    """Could not reach the API after retries."""


class AtomAuthError(AtomAPIError):
    """401/403 from the API."""


class AtomNotFoundError(AtomAPIError):
    """404 from the API."""


def _detail(resp: requests.Response) -> str:
    try:
        return str(resp.json().get("detail", resp.text[:200]))
    except ValueError:
        return resp.text[:200]


class AtomClient:
    def __init__(self, base_url: str, token: str | None = None, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        if token:
            self._session.headers["Authorization"] = f"Bearer {token}"

    @retry(
        retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=5),
        reraise=True,
    )
    def _send(self, method: str, path: str, **kwargs) -> requests.Response:
        return self._session.request(
            method, f"{self.base_url}{path}", timeout=self.timeout, **kwargs
        )

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        try:
            resp = self._send(method, path, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise AtomConnectionError(
                f"cannot reach Atom API at {self.base_url}: {exc}"
            ) from exc
        if resp.status_code in (401, 403):
            raise AtomAuthError(
                "authentication failed - check ATOM_API_TOKEN / --token"
            )
        if resp.status_code == 404:
            raise AtomNotFoundError(_detail(resp))
        if resp.status_code >= 400:
            raise AtomAPIError(f"API error {resp.status_code}: {_detail(resp)}")
        return resp

    def get_json(self, path: str, **kwargs) -> Any:
        return self._request("GET", path, **kwargs).json()

    def post_json(self, path: str, payload: dict) -> Any:
        return self._request("POST", path, json=payload).json()

    def get_bytes(self, path: str) -> bytes:
        return self._request("GET", path).content
