"""Wallet service. EVERY query is scoped by telegram_user_id (no IDOR)."""
from __future__ import annotations
import logging
import uuid
from datetime import datetime, timezone

import database as dbm
from security.encryption import encrypt, decrypt
from chains.evm import evm_adapter
from chains.solana import solana_adapter
from chains.base import WalletKeys

logger = logging.getLogger("oxael.wallets")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public(doc: dict) -> dict:
    return {
        "wallet_id": doc["wallet_id"],
        "name": doc["name"],
        "source": doc["source"],
        "is_primary": doc.get("is_primary", False),
        "track_enabled": doc.get("track_enabled", True),
        "families": list(doc.get("accounts", {}).keys()),
        "addresses": {fam: acc["address"] for fam, acc in doc.get("accounts", {}).items()},
        "created_at": doc.get("created_at"),
    }


async def get_or_create_user(telegram_user_id: int, username: str | None = None) -> dict:
    doc = await dbm.users.find_one({"telegram_user_id": telegram_user_id}, {"_id": 0})
    if doc:
        return doc
    now = _now()
    doc = {
        "telegram_user_id": telegram_user_id,
        "username": username,
        "email": None,
        "email_verified": False,
        "email_verified_at": None,
        "welcome_email_sent": False,
        "onboarded": False,
        "created_at": now,
        "updated_at": now,
    }
    await dbm.users.insert_one(dict(doc))
    return doc


async def mark_onboarded(telegram_user_id: int) -> None:
    await dbm.users.update_one(
        {"telegram_user_id": telegram_user_id},
        {"$set": {"onboarded": True, "updated_at": _now()}},
    )


def _account_from_keys(keys: WalletKeys) -> dict:
    return {"address": keys.address, "enc": encrypt(keys.secret), "secret_type": keys.secret_type}


async def address_exists(telegram_user_id: int, address: str) -> bool:
    addr = (address or "").lower()
    cur = dbm.wallets.find({"telegram_user_id": telegram_user_id}, {"_id": 0, "accounts": 1})
    async for doc in cur:
        for acc in doc.get("accounts", {}).values():
            if acc.get("address", "").lower() == addr:
                return True
    return False


async def find_wallet_by_address(telegram_user_id: int, address: str) -> dict | None:
    addr = (address or "").lower()
    cur = dbm.wallets.find({"telegram_user_id": telegram_user_id}, {"_id": 0})
    async for doc in cur:
        for acc in doc.get("accounts", {}).values():
            if acc.get("address", "").lower() == addr:
                return _public(doc)
    return None


async def create_wallet(telegram_user_id: int, name: str) -> dict:
    """One-click multi-chain wallet: fresh EVM (all EVM nets) + Solana accounts."""
    evm_keys = evm_adapter.generate()
    sol_keys = solana_adapter.generate()
    accounts = {"evm": _account_from_keys(evm_keys), "solana": _account_from_keys(sol_keys)}
    # scrub plaintext refs
    evm_keys.secret = sol_keys.secret = ""

    existing = await dbm.wallets.count_documents({"telegram_user_id": telegram_user_id})
    doc = {
        "wallet_id": uuid.uuid4().hex,
        "telegram_user_id": telegram_user_id,
        "name": name,
        "source": "generated",
        "is_primary": existing == 0,
        "track_enabled": True,
        "accounts": accounts,
        "created_at": _now(),
        "updated_at": _now(),
    }
    await dbm.wallets.insert_one(dict(doc))
    logger.info("wallet created user=%s wallet_id=%s", telegram_user_id, doc["wallet_id"])
    return _public(doc)


async def import_wallet(telegram_user_id: int, name: str, secret: str) -> dict:
    """Import from a private key / seed (EVM) or base58 keypair (Solana)."""
    accounts: dict[str, dict] = {}
    family = None
    try:
        keys = evm_adapter.from_secret(secret)
        derived_addr = keys.address
        accounts["evm"] = _account_from_keys(keys)
        keys.secret = ""
        family = "evm"
    except ValueError:
        try:
            keys = solana_adapter.from_secret(secret)
            derived_addr = keys.address
            accounts["solana"] = _account_from_keys(keys)
            keys.secret = ""
            family = "solana"
        except ValueError:
            raise ValueError("Unrecognised secret. Provide an EVM private key / seed phrase or a Solana keypair.")

    if await address_exists(telegram_user_id, derived_addr):
        raise ValueError("already_added")

    existing = await dbm.wallets.count_documents({"telegram_user_id": telegram_user_id})
    doc = {
        "wallet_id": uuid.uuid4().hex,
        "telegram_user_id": telegram_user_id,
        "name": name,
        "source": "imported",
        "is_primary": existing == 0,
        "track_enabled": True,
        "accounts": accounts,
        "created_at": _now(),
        "updated_at": _now(),
    }
    await dbm.wallets.insert_one(dict(doc))
    logger.info("wallet imported user=%s wallet_id=%s family=%s", telegram_user_id, doc["wallet_id"], family)
    return _public(doc)


async def list_wallets(telegram_user_id: int) -> list[dict]:
    cur = dbm.wallets.find({"telegram_user_id": telegram_user_id}, {"_id": 0}).sort("created_at", 1)
    return [_public(d) async for d in cur]


async def _raw_wallet(telegram_user_id: int, wallet_id: str) -> dict | None:
    return await dbm.wallets.find_one(
        {"telegram_user_id": telegram_user_id, "wallet_id": wallet_id}, {"_id": 0}
    )


async def get_wallet(telegram_user_id: int, wallet_id: str) -> dict | None:
    doc = await _raw_wallet(telegram_user_id, wallet_id)
    return _public(doc) if doc else None


async def get_primary_wallet(telegram_user_id: int) -> dict | None:
    doc = await dbm.wallets.find_one(
        {"telegram_user_id": telegram_user_id, "is_primary": True}, {"_id": 0}
    )
    if not doc:
        doc = await dbm.wallets.find_one({"telegram_user_id": telegram_user_id}, {"_id": 0})
    return _public(doc) if doc else None


async def rename_wallet(telegram_user_id: int, wallet_id: str, name: str) -> bool:
    res = await dbm.wallets.update_one(
        {"telegram_user_id": telegram_user_id, "wallet_id": wallet_id},
        {"$set": {"name": name, "updated_at": _now()}},
    )
    return res.modified_count > 0


async def set_tracking(telegram_user_id: int, wallet_id: str, enabled: bool) -> bool:
    res = await dbm.wallets.update_one(
        {"telegram_user_id": telegram_user_id, "wallet_id": wallet_id},
        {"$set": {"track_enabled": enabled, "updated_at": _now()}},
    )
    return res.modified_count > 0


async def set_primary(telegram_user_id: int, wallet_id: str) -> bool:
    owned = await _raw_wallet(telegram_user_id, wallet_id)
    if not owned:
        return False
    await dbm.wallets.update_many(
        {"telegram_user_id": telegram_user_id}, {"$set": {"is_primary": False}}
    )
    await dbm.wallets.update_one(
        {"telegram_user_id": telegram_user_id, "wallet_id": wallet_id},
        {"$set": {"is_primary": True, "updated_at": _now()}},
    )
    return True


async def delete_wallet(telegram_user_id: int, wallet_id: str) -> bool:
    target = await _raw_wallet(telegram_user_id, wallet_id)
    if not target:
        return False
    await dbm.wallets.delete_one({"telegram_user_id": telegram_user_id, "wallet_id": wallet_id})
    if target.get("is_primary"):
        nxt = await dbm.wallets.find_one({"telegram_user_id": telegram_user_id}, {"_id": 0})
        if nxt:
            await dbm.wallets.update_one(
                {"wallet_id": nxt["wallet_id"]}, {"$set": {"is_primary": True}}
            )
    return True


async def get_signing_secret(telegram_user_id: int, wallet_id: str, family: str) -> str:
    """Decrypt signing material for a transaction. Result is used and discarded;
    never logged, returned via API, or sent through Telegram."""
    doc = await _raw_wallet(telegram_user_id, wallet_id)
    if not doc:
        raise PermissionError("wallet not found for user")
    acc = doc.get("accounts", {}).get(family)
    if not acc:
        raise ValueError(f"wallet has no {family} account")
    return decrypt(acc["enc"])


async def get_address(telegram_user_id: int, wallet_id: str, family: str) -> str | None:
    doc = await _raw_wallet(telegram_user_id, wallet_id)
    if not doc:
        return None
    acc = doc.get("accounts", {}).get(family)
    return acc["address"] if acc else None
