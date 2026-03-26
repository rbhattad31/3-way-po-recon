"""Field-level encryption for ERP connection secrets.

Uses Fernet symmetric encryption with a key derived from Django's
SECRET_KEY via PBKDF2.  This provides AES-128-CBC encryption with
HMAC-SHA256 authentication.

Usage:
    from apps.erp_integration.crypto import encrypt_value, decrypt_value

    ciphertext = encrypt_value("my-secret-password")
    plaintext = decrypt_value(ciphertext)

Encrypted values are stored as base64 strings in the database.
An empty or blank input returns an empty string (no encryption needed).
"""
from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

logger = logging.getLogger(__name__)


def _derive_key() -> bytes:
    """Derive a Fernet-compatible 32-byte key from SECRET_KEY via PBKDF2."""
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        settings.SECRET_KEY.encode(),
        b"erp-integration-field-encryption",
        iterations=100_000,
    )
    return base64.urlsafe_b64encode(dk)


def encrypt_value(plaintext: str) -> str:
    """Encrypt a plaintext string.  Returns empty string for blank input."""
    if not plaintext:
        return ""
    key = _derive_key()
    f = Fernet(key)
    return f.encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    """Decrypt a ciphertext string.  Returns empty string for blank input."""
    if not ciphertext:
        return ""
    key = _derive_key()
    f = Fernet(key)
    try:
        return f.decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        logger.error("Failed to decrypt ERP credential -- invalid token or key change")
        raise ValueError("Cannot decrypt stored credential. The encryption key may have changed.")
