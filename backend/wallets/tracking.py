"""Track ANY address (not just the user's own wallets).

External tracked addresses are watched for incoming deposits exactly like owned
wallets — the background watcher records real deposits (tx hash + sender) and
notifies once. Read-only: we never hold keys for external addresses.
"""
from __future__ import annotations
import uuid
from datetime import datetime, timezone

import database as dbm
from chains.evm import evm_adapter
from chains.solana import solana_adapter


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def detect_family(address: str) -> str | None:
    """Return 'evm' | 'solana' | None for a pasted address."""
    address = (address or "").strip()
    if evm_adapter.validate_address(address):
        return "evm"
    try:
        if solana_adapter.validate_address(address):
            return "solana"
    except Exception:  # noqa: BLE001
        pass
    return None


async def add_tracked(telegram_user_id: int, address: str, label: str | None) -> dict:
    address = address.strip()
    family = detect_family(address)
    if not family:
        raise ValueError("That doesn't look like a valid EVM (0x…) or Solana address.")
    existing = await dbm.tracked_wallets.find_one(
        {"telegram_user_id": telegram_user_id, "address": {"$regex": f"^{address}$", "$options": "i"}}
    )
    if existing:
        raise ValueError("already_tracked")
    doc = {
        "tracked_id": uuid.uuid4().hex,
        "telegram_user_id": telegram_user_id,
        "address": address,
        "family": family,
        "label": (label or "Watched address").strip()[:40],
        "track_enabled": True,
        "created_at": _now(),
    }
    await dbm.tracked_wallets.insert_one(dict(doc))
    doc.pop("_id", None)
    return doc


async def list_tracked(telegram_user_id: int) -> list[dict]:
    cur = dbm.tracked_wallets.find({"telegram_user_id": telegram_user_id}, {"_id": 0}).sort("created_at", -1)
    return [d async for d in cur]


async def remove_tracked(telegram_user_id: int, tracked_id: str) -> bool:
    res = await dbm.tracked_wallets.delete_one(
        {"telegram_user_id": telegram_user_id, "tracked_id": tracked_id})
    return res.deleted_count > 0


async def set_tracked_enabled(telegram_user_id: int, tracked_id: str, enabled: bool) -> bool:
    res = await dbm.tracked_wallets.update_one(
        {"telegram_user_id": telegram_user_id, "tracked_id": tracked_id},
        {"$set": {"track_enabled": enabled}})
    return res.modified_count > 0
