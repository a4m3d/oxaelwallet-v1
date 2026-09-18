import asyncio
import random

from wallets import service as W
from transactions import service as TX
from transactions import state_machine as SM


def _uid():
    return random.randint(100_000_000, 999_000_000)


def test_create_pending_is_idempotent():
    async def run():
        a = _uid()
        await W.get_or_create_user(a)
        w = await W.create_wallet(a, "Idem")
        addr = w["addresses"]["evm"]
        nonce = "fixed-session"
        t1 = await TX.create_pending_send(a, w["wallet_id"], "ethereum", "ETH",
                                          "0x000000000000000000000000000000000000dEaD",
                                          "0.001", "evm", addr, nonce)
        t2 = await TX.create_pending_send(a, w["wallet_id"], "ethereum", "ETH",
                                          "0x000000000000000000000000000000000000dEaD",
                                          "0.001", "evm", addr, nonce)
        assert t1["tx_id"] == t2["tx_id"]  # no duplicate row
        await W.delete_wallet(a, w["wallet_id"])
    asyncio.run(run())


def test_atomic_claim_prevents_double_execution():
    async def run():
        a = _uid()
        await W.get_or_create_user(a)
        w = await W.create_wallet(a, "Claim")
        addr = w["addresses"]["evm"]
        t = await TX.create_pending_send(a, w["wallet_id"], "ethereum", "ETH",
                                         "0x000000000000000000000000000000000000dEaD",
                                         "0.001", "evm", addr, "n1")
        first = await TX._atomic_claim_for_signing(a, t["tx_id"])
        second = await TX._atomic_claim_for_signing(a, t["tx_id"])
        assert first is not None and first["state"] == SM.SIGNING
        assert second is None  # a second button press cannot re-claim
        await W.delete_wallet(a, w["wallet_id"])
    asyncio.run(run())


def test_cancel_then_no_claim():
    async def run():
        a = _uid()
        await W.get_or_create_user(a)
        w = await W.create_wallet(a, "Cancel")
        addr = w["addresses"]["evm"]
        t = await TX.create_pending_send(a, w["wallet_id"], "solana", "SOL",
                                         "So11111111111111111111111111111111111111112",
                                         "0.01", "solana", addr, "n2")
        assert await TX.cancel(a, t["tx_id"]) is True
        assert await TX._atomic_claim_for_signing(a, t["tx_id"]) is None
        await W.delete_wallet(a, w["wallet_id"])
    asyncio.run(run())
