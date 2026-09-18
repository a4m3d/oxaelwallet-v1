"""Gas Account — a dedicated EVM keypair per user that funds gas on any EVM chain.

Reality: on EVM chains gas is always paid by the tx signer; a true "pay gas from
another account" needs an ERC-4337 paymaster/relayer. Without that infra we do the
genuinely on-chain equivalent used by many wallets: keep one funding account (same
address on every EVM chain) and, right before a send/swap, top up the sending
wallet with the exact native shortfall so the fee effectively comes from the Gas
Account. The private key is the user's — exportable via /gasaccount.
"""
from __future__ import annotations
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import database as dbm
from security.encryption import encrypt, decrypt
from chains.evm import evm_adapter
from assets.catalog import evm_networks, NETWORKS

logger = logging.getLogger("oxael.gas")

FUND_BUFFER = Decimal("1.15")   # top up 15% over the estimated shortfall
_WAIT_SECONDS = 75


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def get_or_create(telegram_user_id: int) -> dict:
    doc = await dbm.gas_accounts.find_one({"telegram_user_id": telegram_user_id}, {"_id": 0})
    if doc:
        return {"address": doc["address"], "created_at": doc.get("created_at")}
    keys = evm_adapter.generate()
    doc = {
        "gas_id": uuid.uuid4().hex,
        "telegram_user_id": telegram_user_id,
        "address": keys.address,
        "enc": encrypt(keys.secret),
        "secret_type": keys.secret_type,
        "created_at": _now(),
    }
    keys.secret = ""
    await dbm.gas_accounts.insert_one(dict(doc))
    logger.info("gas account created uid=%s", telegram_user_id)
    return {"address": doc["address"], "created_at": doc["created_at"]}


async def get_address(telegram_user_id: int) -> str:
    return (await get_or_create(telegram_user_id))["address"]


async def _secret(telegram_user_id: int) -> str:
    doc = await dbm.gas_accounts.find_one({"telegram_user_id": telegram_user_id}, {"_id": 0, "enc": 1})
    if not doc:
        await get_or_create(telegram_user_id)
        doc = await dbm.gas_accounts.find_one({"telegram_user_id": telegram_user_id}, {"_id": 0, "enc": 1})
    return decrypt(doc["enc"])


async def reveal_private_key(telegram_user_id: int) -> str:
    """Explicit, user-initiated secret export (never logged)."""
    return await _secret(telegram_user_id)


async def balances(telegram_user_id: int) -> list[dict]:
    """Native balance per EVM chain: [{network, name, symbol, amount, status}]."""
    addr = await get_address(telegram_user_id)

    async def one(net):
        try:
            bal = await evm_adapter.native_balance(net.key, addr)
            return {"network": net.key, "name": net.name, "symbol": net.symbol,
                    "amount": bal, "status": "ok"}
        except Exception:  # noqa: BLE001
            return {"network": net.key, "name": net.name, "symbol": net.symbol,
                    "amount": Decimal(0), "status": "unavailable"}

    return list(await asyncio.gather(*[one(n) for n in evm_networks()]))


async def ensure_funded(telegram_user_id: int, network: str, target_address: str,
                        needed_native: Decimal) -> dict:
    """Make sure `target_address` holds at least `needed_native` on `network`,
    topping up the shortfall from the Gas Account. Returns:
      {funded: bool, topped_up: bool, tx_hash?, error?}
    Only used for EVM networks.
    """
    net = NETWORKS.get(network)
    try:
        have = await evm_adapter.native_balance(network, target_address)
    except Exception:  # noqa: BLE001
        have = Decimal(0)
    if have >= needed_native:
        return {"funded": True, "topped_up": False}

    shortfall = (needed_native - have) * FUND_BUFFER
    gas_addr = await get_address(telegram_user_id)
    if gas_addr.lower() == target_address.lower():
        return {"funded": have >= needed_native, "topped_up": False,
                "error": "the sending wallet IS the gas account"}
    try:
        gas_bal = await evm_adapter.native_balance(network, gas_addr)
        transfer_fee = (await evm_adapter.estimate_native_fee(network)).fee_native
    except Exception:  # noqa: BLE001
        return {"funded": False, "topped_up": False,
                "error": f"gas provider unavailable on {net.name if net else network}"}
    if gas_bal < shortfall + transfer_fee:
        sym = net.symbol if net else network
        return {"funded": False, "topped_up": False,
                "error": (f"Gas Account is low on {net.name if net else network}: "
                          f"has {gas_bal} {sym}, needs ~{shortfall + transfer_fee} {sym}. "
                          f"Top it up via /gasaccount.")}
    secret = await _secret(telegram_user_id)
    try:
        tx_hash = await evm_adapter.send_native(network, secret, target_address, shortfall)
    except Exception as e:  # noqa: BLE001
        return {"funded": False, "topped_up": False, "error": f"gas top-up failed: {type(e).__name__}"}
    finally:
        secret = ""
    # wait for the top-up to land so the main tx sees the balance
    waited = 0
    while waited < _WAIT_SECONDS:
        await asyncio.sleep(5)
        waited += 5
        try:
            status = await evm_adapter.get_receipt_status(network, tx_hash)
        except Exception:  # noqa: BLE001
            status = None
        if status == 1:
            return {"funded": True, "topped_up": True, "tx_hash": tx_hash}
        if status == 0:
            return {"funded": False, "topped_up": True, "tx_hash": tx_hash,
                    "error": "gas top-up transaction reverted"}
    return {"funded": False, "topped_up": True, "tx_hash": tx_hash,
            "error": "gas top-up is taking longer than expected; try again shortly"}
