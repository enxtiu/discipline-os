"""Hashing and token helpers.

Design notes
------------
* **Device tokens / API tokens** are long random strings. We never store them in
  clear text: the DB keeps a SHA-256 hex digest plus a short display prefix.
  SHA-256 (not bcrypt) is intentional — the token has full 256-bit entropy, so
  there is nothing to brute force, and lookup must be a single indexed query.
* **Pairing codes** are short (8 chars, ~40 bits) and therefore stored as a
  keyed HMAC-SHA256 (pepper = ``SECRET_KEY``) so a DB dump alone does not allow
  an offline brute force within the 15 minute window.
* **Passwords** do not exist in this product, but the owner CLI optionally
  stores an argon2 hash of the recovery phrase so future features (e.g. re-pair
  after losing all devices) can be built without a migration.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

__all__ = [
    "constant_time_eq",
    "generate_pairing_code",
    "hash_password",
    "hash_token",
    "hkdf_sha256",
    "hmac_sha256",
    "new_secret_token",
    "normalise_pairing_code",
    "token_prefix",
    "verify_password",
    "verify_token",
]

_PASSWORD_HASHER = PasswordHasher()

# Ambiguous characters (0/O, 1/I/L) removed — the code is typed from a phone.
_PAIRING_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_PAIRING_LEN = 8


def new_secret_token(prefix: str = "", nbytes: int = 32) -> str:
    """URL-safe secret with full entropy. 32 bytes -> 256 bits."""
    return f"{prefix}{secrets.token_urlsafe(nbytes)}"


def token_prefix(token: str, length: int = 12) -> str:
    """Short, non-reversible-looking label shown in the devices/tokens list."""
    return token[:length]


def hash_token(token: str) -> str:
    """Stable digest used for DB lookups of bearer tokens."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_token(token: str, expected_hash: str) -> bool:
    return constant_time_eq(hash_token(token), expected_hash)


def generate_pairing_code() -> str:
    """Human-friendly one-time code, e.g. ``K7QP-M3XA``."""
    raw = "".join(secrets.choice(_PAIRING_ALPHABET) for _ in range(_PAIRING_LEN))
    return f"{raw[:4]}-{raw[4:]}"


def normalise_pairing_code(code: str) -> str:
    """Uppercase, strip separators and common Cyrillic lookalikes."""
    text = (code or "").strip().upper()
    for src, dst in (
        ("А", "A"),
        ("В", "B"),
        ("С", "C"),
        ("Е", "E"),
        ("Н", "H"),
        ("К", "K"),
        ("М", "M"),
        ("О", "O"),
        ("Р", "P"),
        ("Т", "T"),
        ("У", "Y"),
        ("Х", "X"),
        ("З", "3"),
    ):
        text = text.replace(src, dst)
    return "".join(ch for ch in text if ch.isalnum())


def hmac_sha256(value: str, key: str) -> str:
    return hmac.new(key.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def hkdf_sha256(key_material: bytes, *, salt: bytes, info: bytes, length: int = 32) -> bytes:
    """HKDF with SHA-256 (RFC 5869): a key for one purpose (``info``) derived from a
    secret, so the secret itself is never used as that key."""
    if not 0 < length <= 255 * hashlib.sha256().digest_size:
        raise ValueError("HKDF-SHA256 derives 1..8160 bytes")
    prk = hmac.new(salt or bytes(hashlib.sha256().digest_size), key_material, hashlib.sha256).digest()
    out, block = b"", b""
    counter = 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        out += block
        counter += 1
    return out[:length]


def constant_time_eq(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def hash_password(password: str) -> str:
    return _PASSWORD_HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _PASSWORD_HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
