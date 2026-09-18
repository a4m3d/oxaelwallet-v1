"""Secret redaction for logs and exceptions.

Masks private keys, mnemonics, tokens and known env secrets so they never
reach logs, even inside exception strings.
"""
import logging
import re

# hex private key (with/without 0x), 64 hex chars
_HEX_KEY = re.compile(r"\b(0x)?[0-9a-fA-F]{64}\b")
# base58 secret-ish (solana keys ~87-88 chars)
_B58_LONG = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{80,}\b")
# bearer/JWT
_JWT = re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b")

_MASK = "[REDACTED]"

# Words that, if present, strongly suggest a mnemonic; we redact long lowercase runs.
_MNEMONIC = re.compile(r"\b(?:[a-z]{3,10}\s+){11,}[a-z]{3,10}\b")


def redact(text: str) -> str:
    if not text:
        return text
    text = _JWT.sub(_MASK, text)
    text = _MNEMONIC.sub(_MASK, text)
    text = _HEX_KEY.sub(_MASK, text)
    text = _B58_LONG.sub(_MASK, text)
    return text


class RedactingFilter(logging.Filter):
    """Logging filter that redacts secrets from every emitted record."""

    def __init__(self, extra_secrets: list[str] | None = None):
        super().__init__()
        self._extra = [s for s in (extra_secrets or []) if s]

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        for secret in self._extra:
            if secret and secret in msg:
                msg = msg.replace(secret, _MASK)
        msg = redact(msg)
        record.msg = msg
        record.args = ()
        return True


def install(extra_secrets: list[str] | None = None) -> None:
    """Attach the redacting filter to the root logger and all handlers."""
    flt = RedactingFilter(extra_secrets)
    root = logging.getLogger()
    root.addFilter(flt)
    for h in root.handlers:
        h.addFilter(flt)
