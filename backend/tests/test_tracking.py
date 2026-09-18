"""Tests for external address tracking and the NEAR Intents client additions."""
import asyncio
import random

import pytest

import database as dbm
from wallets import tracking as TR
from intents.near_intents import NearIntentsClient


def test_detect_family():
    assert TR.detect_family("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045") == "evm"
    assert TR.detect_family("So11111111111111111111111111111111111111112") == "solana"
    assert TR.detect_family("not-an-address") is None
    assert TR.detect_family("") is None


def test_add_list_dedup_remove_tracked():
    uid = random.randint(10_000_000, 99_000_000)

    async def run():
        e = await TR.add_tracked(uid, "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045", "Cold")
        assert e["family"] == "evm" and e["label"] == "Cold"
        # duplicate (case-insensitive) rejected
        with pytest.raises(ValueError) as ex:
            await TR.add_tracked(uid, "0xD8DA6BF26964AF9D7EED9E03E53415D37AA96045", "again")
        assert str(ex.value) == "already_tracked"
        # invalid rejected
        with pytest.raises(ValueError):
            await TR.add_tracked(uid, "garbage", None)
        lst = await TR.list_tracked(uid)
        assert len(lst) == 1
        assert await TR.remove_tracked(uid, e["tracked_id"]) is True
        assert await TR.list_tracked(uid) == []
        await dbm.tracked_wallets.delete_many({"telegram_user_id": uid})
    asyncio.run(run())


def test_intents_client_builds_submit_and_headers():
    c = NearIntentsClient(base="https://1click.chaindefuser.com", jwt="")
    assert c.configured is True
    # submit_deposit + status + quote methods exist
    assert hasattr(c, "submit_deposit")
    assert hasattr(c, "request_quote")
    assert hasattr(c, "get_status")
    headers = c._headers()
    assert headers["Content-Type"] == "application/json"
