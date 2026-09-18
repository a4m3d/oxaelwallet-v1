"""Transaction service: idempotent creation, atomic state transitions, execution.

Duplicate protection strategy:
  * A unique index on (telegram_user_id, idempotency_key) makes duplicate
    creation impossible at the DB layer.
  * Before broadcasting we do an atomic findOneAndUpdate that only matches when
    the record is still in a pre-signing state, so a second button press cannot
    trigger a second broadcast.
"""
from __future__ import annotations
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

import database as dbm
from security.redaction import redact
from transactions import state_machine as sm
from transactions.idempotency import make_key
from assets.catalog import NETWORKS
from wallets import service as wallet_service
from chains.evm import evm_adapter
from chains.solana import solana_adapter

logger = logging.getLogger("oxael.tx")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k != "_id"}


async def create_pending_send(
    telegram_user_id: int, wallet_id: str, network: str, asset: str,
    to_address: str, amount: str, family: str, from_address: str,
    session_nonce: str, token_address: str | None = None, decimals: int = 18,
) -> dict:
    key = make_key(telegram_user_id, wallet_id, network, asset, to_address, amount, session_nonce)
    existing = await dbm.transactions.find_one(
        {"telegram_user_id": telegram_user_id, "idempotency_key": key}, {"_id": 0}
    )
    if existing:
        return existing

    doc = {
        "tx_id": uuid.uuid4().hex,
        "telegram_user_id": telegram_user_id,
        "wallet_id": wallet_id,
        "network": network,
        "family": family,
        "asset": asset,
        "token_address": token_address,
        "decimals": decimals,
        "direction": "send",
        "amount": str(amount),
        "to_address": to_address,
        "from_address": from_address,
        "state": sm.AWAITING_CONFIRMATION,
        "idempotency_key": key,
        "tx_hash": None,
        "explorer_url": None,
        "error": None,
        "created_at": _now(),
        "updated_at": _now(),
    }
    try:
        await dbm.transactions.insert_one(dict(doc))
    except DuplicateKeyError:
        return await dbm.transactions.find_one(
            {"telegram_user_id": telegram_user_id, "idempotency_key": key}, {"_id": 0}
        )
    return _public(doc)


async def get_transaction(telegram_user_id: int, tx_id: str) -> dict | None:
    return await dbm.transactions.find_one(
        {"telegram_user_id": telegram_user_id, "tx_id": tx_id}, {"_id": 0}
    )


async def cancel(telegram_user_id: int, tx_id: str) -> bool:
    res = await dbm.transactions.update_one(
        {"telegram_user_id": telegram_user_id, "tx_id": tx_id,
         "state": {"$in": [sm.CREATED, sm.AWAITING_CONFIRMATION]}},
        {"$set": {"state": sm.CANCELLED, "updated_at": _now()}},
    )
    return res.modified_count > 0


async def _atomic_claim_for_signing(telegram_user_id: int, tx_id: str) -> dict | None:
    """Move AWAITING_CONFIRMATION|CREATED -> SIGNING atomically. Returns the
    updated doc, or None if another press already claimed it."""
    return await dbm.transactions.find_one_and_update(
        {"telegram_user_id": telegram_user_id, "tx_id": tx_id,
         "state": {"$in": [sm.CREATED, sm.AWAITING_CONFIRMATION]}},
        {"$set": {"state": sm.SIGNING, "updated_at": _now()}},
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )


async def _set_state(tx_id: str, state: str, **extra) -> None:
    upd = {"state": state, "updated_at": _now()}
    upd.update(extra)
    await dbm.transactions.update_one({"tx_id": tx_id}, {"$set": upd})


async def confirm_and_broadcast(telegram_user_id: int, tx_id: str) -> dict:
    """Idempotent execution. Safe under repeated Telegram button presses."""
    claimed = await _atomic_claim_for_signing(telegram_user_id, tx_id)
    if claimed is None:
        current = await get_transaction(telegram_user_id, tx_id)
        return current or {"state": sm.FAILED, "error": "transaction not found"}

    net = NETWORKS.get(claimed["network"])
    try:
        secret = await wallet_service.get_signing_secret(
            telegram_user_id, claimed["wallet_id"], claimed["family"]
        )
        await _set_state(tx_id, sm.BROADCASTING)
        amount = Decimal(claimed["amount"])

        if claimed["family"] == "evm":
            if claimed.get("token_address"):
                tx_hash = await evm_adapter.send_token(
                    claimed["network"], secret, claimed["token_address"],
                    int(claimed.get("decimals", 18)), claimed["to_address"], amount,
                )
            else:
                tx_hash = await evm_adapter.send_native(
                    claimed["network"], secret, claimed["to_address"], amount
                )
        elif claimed["family"] == "solana":
            tx_hash = await solana_adapter.send_native(
                secret, claimed["to_address"], amount
            )
        else:
            raise ValueError(f"send not supported for family {claimed['family']}")

        secret = ""  # scrub
        if tx_hash and not tx_hash.startswith("0x") and claimed["family"] == "evm":
            tx_hash = "0x" + tx_hash
        explorer = net.explorer_tx(tx_hash) if net else None
        await _set_state(tx_id, sm.BROADCASTED, tx_hash=tx_hash, explorer_url=explorer)
        logger.info("tx broadcast user=%s tx_id=%s net=%s", telegram_user_id, tx_id, claimed["network"])
        return await get_transaction(telegram_user_id, tx_id)
    except Exception as exc:  # noqa: BLE001
        msg = redact(str(exc))[:400]
        await _set_state(tx_id, sm.FAILED, error=msg)
        logger.warning("tx failed user=%s tx_id=%s err=%s", telegram_user_id, tx_id, msg)
        return await get_transaction(telegram_user_id, tx_id)


async def record_incoming(telegram_user_id: int, wallet_id: str, network: str,
                          asset: str, amount: str, tx_hash: str) -> None:
    net = NETWORKS.get(network)
    doc = {
        "tx_id": uuid.uuid4().hex,
        "telegram_user_id": telegram_user_id,
        "wallet_id": wallet_id,
        "network": network,
        "family": net.family.value if net else "",
        "asset": asset,
        "direction": "receive",
        "amount": str(amount),
        "state": sm.CONFIRMED,
        "idempotency_key": f"in:{tx_hash}",
        "tx_hash": tx_hash,
        "explorer_url": net.explorer_tx(tx_hash) if net else None,
        "created_at": _now(),
        "updated_at": _now(),
    }
    try:
        await dbm.transactions.insert_one(dict(doc))
    except DuplicateKeyError:
        pass


async def confirmable() -> list[dict]:
    cur = dbm.transactions.find(
        {"state": {"$in": [sm.BROADCASTED, sm.PENDING]}, "tx_hash": {"$ne": None}}, {"_id": 0}
    )
    return [d async for d in cur]


async def set_confirmed(tx_id: str, **receipt) -> None:
    """Mark confirmed and persist real receipt data (block, gas, fee) when known."""
    extra = {k: v for k, v in receipt.items() if v is not None}
    await _set_state(tx_id, sm.CONFIRMED, **extra)


async def set_failed(tx_id: str, err: str = "transaction reverted") -> None:
    await _set_state(tx_id, sm.FAILED, error=err)


async def record_detected_receive(telegram_user_id: int, wallet_id: str, network: str,
                                  asset: str, amount: str) -> None:
    net = NETWORKS.get(network)
    doc = {
        "tx_id": uuid.uuid4().hex,
        "telegram_user_id": telegram_user_id,
        "wallet_id": wallet_id,
        "network": network,
        "family": net.family.value if net else "",
        "asset": asset,
        "direction": "receive",
        "amount": str(amount),
        "state": sm.CONFIRMED,
        "idempotency_key": f"recv:{uuid.uuid4().hex}",
        "tx_hash": None,
        "explorer_url": None,
        "created_at": _now(),
        "updated_at": _now(),
    }
    await dbm.transactions.insert_one(dict(doc))


async def history(telegram_user_id: int, wallet_id: str | None = None,
                  limit: int = 10, skip: int = 0) -> list[dict]:
    q: dict = {"telegram_user_id": telegram_user_id}
    if wallet_id:
        q["wallet_id"] = wallet_id
    cur = dbm.transactions.find(q, {"_id": 0}).sort("created_at", -1).skip(skip).limit(limit)
    return [d async for d in cur]


async def history_count(telegram_user_id: int, wallet_id: str | None = None) -> int:
    q: dict = {"telegram_user_id": telegram_user_id}
    if wallet_id:
        q["wallet_id"] = wallet_id
    return await dbm.transactions.count_documents(q)
