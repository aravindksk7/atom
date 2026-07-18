"""HTTP client for the Atom API."""
from __future__ import annotations


class AtomAPIError(Exception):
    """Generic Atom API failure."""


class AtomConnectionError(AtomAPIError):
    """Could not reach the API after retries."""


class AtomAuthError(AtomAPIError):
    """401/403 from the API."""


class AtomNotFoundError(AtomAPIError):
    """404 from the API."""


class AtomClient:
    def __init__(self, base_url: str, token: str | None = None, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
