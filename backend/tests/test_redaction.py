from security.redaction import redact, RedactingFilter
import logging


def test_redact_hex_private_key():
    pk = "0x" + "a" * 64
    assert pk not in redact(f"key is {pk} done")


def test_redact_jwt():
    jwt = "eyJhbGciOi.eyJzdWIiOi.abc-DEF_123"
    assert jwt not in redact(f"token {jwt}")


def test_redact_mnemonic():
    phrase = " ".join(["abandon"] * 12)
    assert redact(f"seed {phrase}") != f"seed {phrase}"


def test_filter_masks_extra_secret():
    flt = RedactingFilter(extra_secrets=["SUPERSECRETTOKEN"])
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "leak SUPERSECRETTOKEN here", None, None)
    flt.filter(rec)
    assert "SUPERSECRETTOKEN" not in rec.getMessage()
