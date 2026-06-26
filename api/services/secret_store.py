"""Symmetric encryption for webhook signing secrets stored in the DB.

Set WEBHOOK_ENCRYPTION_KEY to a URL-safe base64-encoded 32-byte key:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

If the env var is absent, secrets are stored as plaintext (backward-compatible
with existing deployments) and a warning is emitted at startup.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_KEY_ENV = "WEBHOOK_ENCRYPTION_KEY"
_raw_key = os.environ.get(_KEY_ENV, "").strip().encode()

if _raw_key:
    try:
        from cryptography.fernet import Fernet
        _fernet = Fernet(_raw_key)
    except Exception as exc:
        logger.error("Invalid %s — webhook secrets will be stored in plaintext: %s", _KEY_ENV, exc)
        _fernet = None
else:
    logger.warning(
        "%s is not set — webhook signing secrets are stored in plaintext. "
        "Set this env var to a Fernet key to encrypt them at rest.",
        _KEY_ENV,
    )
    _fernet = None


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret for DB storage.  Returns the plaintext if no key is configured."""
    if _fernet is None or not plaintext:
        return plaintext
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_secret(stored: str) -> str:
    """Decrypt a stored secret.  Returns the value unchanged if it isn't encrypted."""
    if _fernet is None or not stored:
        return stored
    try:
        return _fernet.decrypt(stored.encode()).decode()
    except Exception:
        # Value was stored before encryption was enabled — return as-is.
        return stored
