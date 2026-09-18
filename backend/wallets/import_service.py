"""Secret detection helper for wallet import (no plaintext logging/echo)."""
from __future__ import annotations
import re

_PK_RE = re.compile(r"^(0x)?[0-9a-fA-F]{64}$")


def classify_secret(secret: str) -> str:
    """Return a coarse type label without revealing the secret. For UI hints only."""
    s = (secret or "").strip()
    if _PK_RE.match(s):
        return "evm_private_key"
    words = s.split()
    if len(words) in (12, 15, 18, 21, 24):
        return "mnemonic"
    if 32 <= len(s) <= 100 and re.match(r"^[1-9A-HJ-NP-Za-km-z]+$", s):
        return "solana_keypair"
    return "unknown"
