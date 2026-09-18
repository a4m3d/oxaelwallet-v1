"""Authenticated encryption for wallet signing material.

Format (versioned, URL-safe base64 of the payload):
    "v1:" + b64( nonce(12) || ciphertext_with_gcm_tag )

AES-256-GCM with a random 96-bit nonce per encryption and a 128-bit auth tag.
Tampering with any byte causes decryption to raise InvalidToken.
"""
import base64
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

from config import settings

_VERSION = "v1"
_NONCE_LEN = 12


class InvalidToken(Exception):
    """Raised when ciphertext is malformed or authentication fails (tamper)."""


def _load_key() -> bytes:
    raw = settings.WALLET_ENCRYPTION_KEY
    if not raw:
        raise RuntimeError("WALLET_ENCRYPTION_KEY is not configured")
    try:
        key = base64.b64decode(raw)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("WALLET_ENCRYPTION_KEY must be base64-encoded") from exc
    if len(key) != 32:
        raise RuntimeError("WALLET_ENCRYPTION_KEY must decode to exactly 32 bytes")
    return key


def encrypt(plaintext: str, aad: bytes | None = None) -> str:
    key = _load_key()
    aes = AESGCM(key)
    nonce = os.urandom(_NONCE_LEN)
    ct = aes.encrypt(nonce, plaintext.encode("utf-8"), aad)
    payload = base64.b64encode(nonce + ct).decode("ascii")
    return f"{_VERSION}:{payload}"


def decrypt(token: str, aad: bytes | None = None) -> str:
    if not token or ":" not in token:
        raise InvalidToken("malformed ciphertext")
    version, _, payload = token.partition(":")
    if version != _VERSION:
        raise InvalidToken(f"unsupported ciphertext version: {version}")
    try:
        blob = base64.b64decode(payload)
    except Exception as exc:  # noqa: BLE001
        raise InvalidToken("invalid base64 payload") from exc
    if len(blob) <= _NONCE_LEN:
        raise InvalidToken("ciphertext too short")
    nonce, ct = blob[:_NONCE_LEN], blob[_NONCE_LEN:]
    key = _load_key()
    aes = AESGCM(key)
    try:
        pt = aes.decrypt(nonce, ct, aad)
    except InvalidTag as exc:
        raise InvalidToken("authentication failed (tampered or wrong key)") from exc
    return pt.decode("utf-8")
