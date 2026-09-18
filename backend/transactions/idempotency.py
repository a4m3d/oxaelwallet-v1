"""Idempotency key derivation for send operations."""
from __future__ import annotations
import hashlib


def make_key(telegram_user_id: int, wallet_id: str, network: str, asset: str,
             to_address: str, amount: str, session_nonce: str) -> str:
    """Deterministic per-intent key. Same review => same key => no double send.
    `session_nonce` ties the key to a single review screen so a genuinely new
    transfer with identical params still gets a distinct key.
    """
    raw = "|".join([str(telegram_user_id), wallet_id, network, asset,
                    to_address.lower(), str(amount), session_nonce])
    return hashlib.sha256(raw.encode()).hexdigest()
