"""MongoDB connection and index setup for OXAEL WALLET.

The Motor client is resolved per running event loop. This keeps the app robust
under multiple workers / test loops (Motor clients are bound to the loop that
created them).
"""
import asyncio
import logging
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING
from config import settings

logger = logging.getLogger("oxael.db")

_clients: dict[int, AsyncIOMotorClient] = {}


def _get_db():
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.get_event_loop()
    key = id(loop)
    client = _clients.get(key)
    if client is None:
        client = AsyncIOMotorClient(settings.MONGO_URL)
        _clients[key] = client
    return client[settings.DB_NAME]


class _Collection:
    """Proxy that resolves the underlying collection on the active loop."""
    def __init__(self, name: str):
        self._name = name

    def __getattr__(self, item):
        return getattr(_get_db()[self._name], item)


users = _Collection("users")
wallets = _Collection("wallets")
transactions = _Collection("transactions")
address_book = _Collection("address_book")
assets = _Collection("assets")
network_capabilities = _Collection("network_capabilities")
swap_orders = _Collection("swap_orders")
email_verifications = _Collection("email_verifications")
email_events = _Collection("email_events")
audit_events = _Collection("audit_events")
sessions = _Collection("sessions")
balance_snapshots = _Collection("balance_snapshots")
track_cursor = _Collection("track_cursor")
wallet_events = _Collection("wallet_events")
token_metadata = _Collection("token_metadata")
notifications = _Collection("notifications")


async def ensure_indexes() -> None:
    await users.create_index([("telegram_user_id", ASCENDING)], unique=True)
    await wallets.create_index([("telegram_user_id", ASCENDING)])
    await wallets.create_index([("wallet_id", ASCENDING)], unique=True)
    await transactions.create_index([("telegram_user_id", ASCENDING)])
    await transactions.create_index([("wallet_id", ASCENDING)])
    await transactions.create_index(
        [("telegram_user_id", ASCENDING), ("idempotency_key", ASCENDING)], unique=True
    )
    await transactions.create_index([("tx_hash", ASCENDING)])
    await swap_orders.create_index([("order_id", ASCENDING)], unique=True)
    await swap_orders.create_index([("telegram_user_id", ASCENDING)])
    await address_book.create_index([("telegram_user_id", ASCENDING)])
    await email_verifications.create_index([("token_hash", ASCENDING)])
    await email_verifications.create_index([("telegram_user_id", ASCENDING)])
    await sessions.create_index([("telegram_user_id", ASCENDING)], unique=True)
    await track_cursor.create_index(
        [("wallet_id", ASCENDING), ("network", ASCENDING)], unique=True
    )
    logger.info("MongoDB indexes ensured")


def close() -> None:
    for c in _clients.values():
        c.close()
    _clients.clear()
