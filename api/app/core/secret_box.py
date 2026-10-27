"""Encryption at rest for provider secrets (stage 9: the Notion connection secret).

Fernet (AES-128-CBC + HMAC-SHA256, from ``cryptography``) with a key derived by
HKDF-SHA256 (``info = b"dsos-integrations-v1"``) from ``INTEGRATIONS_KEY`` — or from
``SECRET_KEY`` when that is empty. A database dump alone therefore never reveals a
secret, and the derived key is never the raw ``SECRET_KEY`` (which also peppers the
pairing codes).

Rotating the key that is in use makes stored secrets unreadable: :func:`open_secret`
raises :class:`SecretUnreadable` and the owner connects again. ``INTEGRATIONS_KEY``
exists so ``SECRET_KEY`` can be rotated without that.

Secrets never appear in an exception message, a ``repr`` or a log line here.
"""

from __future__ import annotations

import base64
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import get_settings

__all__ = ["HKDF_INFO", "SecretUnreadable", "open_secret", "seal_secret"]

HKDF_INFO = b"dsos-integrations-v1"


class SecretUnreadable(Exception):
    """The stored secret cannot be decrypted with the current key (key rotated)."""


@lru_cache(maxsize=4)
def _fernet(material: str) -> Fernet:
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=HKDF_INFO).derive(material.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(key))


def _box() -> Fernet:
    settings = get_settings()
    return _fernet(settings.integrations_key or settings.secret_key)


def seal_secret(secret: str) -> str:
    """Encrypt ``secret`` → an ASCII Fernet token for the database."""
    return _box().encrypt(secret.encode("utf-8")).decode("ascii")


def open_secret(token: str) -> str:
    """Decrypt a token written by :func:`seal_secret`."""
    try:
        return _box().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError) as exc:
        raise SecretUnreadable("the stored secret cannot be decrypted with the current key") from exc
