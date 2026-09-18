import asyncio
import random
import pytest

from wallets import service as W
from security.encryption import decrypt


def _uid():
    return random.randint(10_000_000, 99_000_000)


def test_user_isolation_and_ownership():
    async def run():
        a, b = _uid(), _uid()
        await W.get_or_create_user(a)
        await W.get_or_create_user(b)
        w = await W.create_wallet(a, "A wallet")
        wid = w["wallet_id"]

        # owner can read
        assert (await W.get_wallet(a, wid)) is not None
        # other user cannot read the same wallet_id (no IDOR)
        assert (await W.get_wallet(b, wid)) is None

        # owner can decrypt signing secret
        secret = await W.get_signing_secret(a, wid, "evm")
        assert secret and len(secret) >= 64
        # other user is denied
        with pytest.raises(PermissionError):
            await W.get_signing_secret(b, wid, "evm")

        # cleanup
        await W.delete_wallet(a, wid)
    asyncio.run(run())


def test_wallet_public_never_contains_secret():
    async def run():
        a = _uid()
        await W.get_or_create_user(a)
        w = await W.create_wallet(a, "Secretless")
        assert "enc" not in str(w)
        assert "accounts" not in w
        assert set(w["families"]) == {"evm", "solana"}
        await W.delete_wallet(a, w["wallet_id"])
    asyncio.run(run())


def test_primary_and_delete_reassignment():
    async def run():
        a = _uid()
        await W.get_or_create_user(a)
        w1 = await W.create_wallet(a, "One")
        w2 = await W.create_wallet(a, "Two")
        assert w1["is_primary"] is True
        assert w2["is_primary"] is False
        await W.set_primary(a, w2["wallet_id"])
        prim = await W.get_primary_wallet(a)
        assert prim["wallet_id"] == w2["wallet_id"]
        await W.delete_wallet(a, w2["wallet_id"])
        prim2 = await W.get_primary_wallet(a)
        assert prim2 is not None  # a remaining wallet becomes primary
        await W.delete_wallet(a, w1["wallet_id"])
    asyncio.run(run())
