import pytest
from security.encryption import encrypt, decrypt, InvalidToken


def test_roundtrip():
    secret = "0x" + "ab" * 32
    token = encrypt(secret)
    assert token.startswith("v1:")
    assert secret not in token  # ciphertext must not embed plaintext
    assert decrypt(token) == secret


def test_unique_nonce_per_encryption():
    a = encrypt("hello world")
    b = encrypt("hello world")
    assert a != b  # random nonce => different ciphertext


def test_tamper_detection():
    token = encrypt("sensitive-material")
    # flip a character in the base64 payload
    prefix, payload = token.split(":", 1)
    tampered = f"{prefix}:{'A' if payload[0] != 'A' else 'B'}{payload[1:]}"
    with pytest.raises(InvalidToken):
        decrypt(tampered)


def test_malformed_rejected():
    with pytest.raises(InvalidToken):
        decrypt("not-a-token")
    with pytest.raises(InvalidToken):
        decrypt("v9:zzzz")
