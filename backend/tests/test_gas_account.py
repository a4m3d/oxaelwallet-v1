"""Tests for the Gas Account (paymaster-style native gas funding)."""
import asyncio
import random
from decimal import Decimal

import database as dbm
from wallets import gas_account as GAS


def _uid():
    return random.randint(10_000_000, 99_000_000)


def test_gas_account_created_stable_and_revealable():
    uid = _uid()

    async def run():
        await dbm.gas_accounts.delete_many({"telegram_user_id": uid})
        a1 = await GAS.get_or_create(uid)
        a2 = await GAS.get_or_create(uid)
        assert a1["address"] == a2["address"], "gas account address must be stable"
        assert a1["address"].startswith("0x") and len(a1["address"]) == 42
        key = await GAS.reveal_private_key(uid)
        # exportable private key that derives back to the same address
        from eth_account import Account
        acct = Account.from_key(key if key.startswith("0x") else "0x" + key)
        assert acct.address.lower() == a1["address"].lower()
        await dbm.gas_accounts.delete_many({"telegram_user_id": uid})
    asyncio.run(run())


def test_ensure_funded_noop_when_enough(monkeypatch):
    uid = _uid()

    async def fake_native_balance(net, addr):
        return Decimal("1")  # target already has plenty

    monkeypatch.setattr(GAS.evm_adapter, "native_balance", fake_native_balance)

    async def run():
        await dbm.gas_accounts.delete_many({"telegram_user_id": uid})
        res = await GAS.ensure_funded(uid, "base", "0x" + "1" * 40, Decimal("0.001"))
        assert res["funded"] is True and res["topped_up"] is False
        await dbm.gas_accounts.delete_many({"telegram_user_id": uid})
    asyncio.run(run())


def test_ensure_funded_reports_low_gas_account(monkeypatch):
    uid = _uid()
    target = "0x" + "2" * 40

    async def fake_native_balance(net, addr):
        return Decimal("0")  # both target and gas account empty

    class _Fee:
        fee_native = Decimal("0.0004")

    async def fake_fee(net):
        return _Fee()

    monkeypatch.setattr(GAS.evm_adapter, "native_balance", fake_native_balance)
    monkeypatch.setattr(GAS.evm_adapter, "estimate_native_fee", fake_fee)

    async def run():
        await dbm.gas_accounts.delete_many({"telegram_user_id": uid})
        res = await GAS.ensure_funded(uid, "base", target, Decimal("0.01"))
        assert res["funded"] is False
        assert "Gas Account is low" in (res.get("error") or "")
        await dbm.gas_accounts.delete_many({"telegram_user_id": uid})
    asyncio.run(run())
