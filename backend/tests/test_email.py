import asyncio
import random

import database as dbm
from wallets import service as W
from services import email_service


def _uid():
    return random.randint(1_000_000_000, 1_999_000_000)


def test_email_validation():
    assert email_service.is_valid_email("user@example.com")
    assert not email_service.is_valid_email("nope")
    assert not email_service.is_valid_email("a@b")


def test_start_verification_stores_only_hash():
    async def run():
        a = _uid()
        await W.get_or_create_user(a)
        await email_service.start_verification(a, "person@example.com")
        rec = await dbm.email_verifications.find_one({"telegram_user_id": a})
        assert rec is not None
        assert "token" not in rec           # raw token never stored
        assert rec["token_hash"] and len(rec["token_hash"]) == 64
    asyncio.run(run())


def test_verify_and_duplicate_welcome_prevention(monkeypatch):
    async def run():
        a = _uid()
        await W.get_or_create_user(a)
        # force "delivery" to succeed so the idempotency flag stays set
        monkeypatch.setattr(email_service, "_deliver", lambda *a, **k: True)

        token = "KNOWN_TEST_TOKEN_123"
        from datetime import datetime, timedelta, timezone
        await dbm.email_verifications.insert_one({
            "telegram_user_id": a, "email": "dup@example.com",
            "token_hash": email_service._hash_token(token),
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
            "used": False, "created_at": datetime.now(timezone.utc).isoformat(),
        })
        res = await email_service.verify(token)
        assert res is not None
        # a second verify with the same token must fail (used)
        assert await email_service.verify(token) is None
        # welcome only sent once
        await email_service._maybe_send_welcome(a, "dup@example.com")
        count = await dbm.email_events.count_documents({"telegram_user_id": a, "type": "welcome"})
        assert count == 1
    asyncio.run(run())
