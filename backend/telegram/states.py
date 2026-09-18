"""Per-user conversation state stored in MongoDB (never holds plaintext secrets)."""
from __future__ import annotations
from datetime import datetime, timezone
import database as dbm


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def get(telegram_user_id: int) -> dict | None:
    return await dbm.sessions.find_one({"telegram_user_id": telegram_user_id}, {"_id": 0})


async def set_state(telegram_user_id: int, chat_id: int, state: str, data: dict | None = None) -> None:
    await dbm.sessions.update_one(
        {"telegram_user_id": telegram_user_id},
        {"$set": {"chat_id": chat_id, "state": state, "data": data or {}, "updated_at": _now()}},
        upsert=True,
    )


async def clear(telegram_user_id: int) -> None:
    await dbm.sessions.update_one(
        {"telegram_user_id": telegram_user_id},
        {"$set": {"state": None, "data": {}, "updated_at": _now()}},
        upsert=True,
    )
