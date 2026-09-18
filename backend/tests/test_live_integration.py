"""Live HTTP integration tests against the running service (public URL)."""
import os
import random
import time
import re
import asyncio
import pytest
import requests
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")

BASE_URL = os.environ["PUBLIC_BASE_URL"].rstrip("/")
SECRET = os.environ["TELEGRAM_WEBHOOK_SECRET"]
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]


def test_health():
    r = requests.get(f"{BASE_URL}/api/health", timeout=15)
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


def test_webhook_missing_header():
    r = requests.post(f"{BASE_URL}/api/telegram/webhook",
                      json={"update_id": 1}, timeout=15)
    assert r.status_code == 403


def test_webhook_wrong_header():
    r = requests.post(f"{BASE_URL}/api/telegram/webhook",
                      json={"update_id": 1},
                      headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
                      timeout=15)
    assert r.status_code == 403


def test_webhook_correct_header_empty_update():
    r = requests.post(f"{BASE_URL}/api/telegram/webhook",
                      json={"update_id": 1},
                      headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
                      timeout=15)
    assert r.status_code == 200
    assert r.json() == {"ok": True}


TEST_UID = random.randint(10_000_000_000, 99_000_000_000)


def _run_async(coro):
    """Run a coroutine on a fresh event loop (order-independent under xdist)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _post(update):
    return requests.post(
        f"{BASE_URL}/api/telegram/webhook",
        json=update,
        headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
        timeout=30,
    )


def test_full_start_and_create_flow():
    # /start
    start_update = {
        "update_id": 1001,
        "message": {
            "message_id": 1,
            "date": int(time.time()),
            "chat": {"id": TEST_UID, "type": "private"},
            "from": {"id": TEST_UID, "is_bot": False, "first_name": "Tester"},
            "text": "/start",
        },
    }
    r = _post(start_update)
    assert r.status_code == 200, r.text
    time.sleep(1.5)  # updates are processed asynchronously; sequential user actions

    # callback: create -> now prompts for a wallet name first
    cb_update = {
        "update_id": 1002,
        "callback_query": {
            "id": "cb1",
            "from": {"id": TEST_UID, "is_bot": False, "first_name": "Tester"},
            "message": {
                "message_id": 2,
                "date": int(time.time()),
                "chat": {"id": TEST_UID, "type": "private"},
                "text": "menu",
            },
            "chat_instance": "x",
            "data": "create",
        },
    }
    r = _post(cb_update)
    assert r.status_code == 200, r.text
    time.sleep(1.5)  # let the "create" handler set the name-prompt state first

    # message: supply the wallet name -> wallet is created
    name_update = {
        "update_id": 1003,
        "message": {
            "message_id": 3,
            "date": int(time.time()),
            "chat": {"id": TEST_UID, "type": "private"},
            "from": {"id": TEST_UID, "is_bot": False, "first_name": "Tester"},
            "text": "Test Wallet",
        },
    }
    r = _post(name_update)
    assert r.status_code == 200, r.text

    # allow async DB writes
    time.sleep(3)

    async def check_db():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        user = await db.users.find_one({"telegram_user_id": TEST_UID})
        wallet = await db.wallets.find_one({"telegram_user_id": TEST_UID})
        client.close()
        return user, wallet

    user, wallet = _run_async(check_db())
    assert user is not None, "user not persisted"
    assert wallet is not None, "wallet not persisted"

    accounts = wallet.get("accounts", {})
    assert "evm" in accounts, f"missing evm account: {accounts.keys()}"
    assert "solana" in accounts, f"missing solana account: {accounts.keys()}"

    for name, acc in accounts.items():
        assert "enc" in acc, f"{name} missing enc field"
        assert acc["enc"].startswith("v1:"), f"{name} enc not versioned: {acc['enc'][:10]}"
        # Ensure no plaintext key fields
        for forbidden in ("private_key", "privateKey", "secret", "mnemonic", "seed"):
            assert forbidden not in acc, f"{name} leaked {forbidden}"


def test_secret_redaction_in_logs():
    # The app reads the bot token from TELEGRAM_TOKEN.
    tg_token = os.environ.get("TELEGRAM_TOKEN", "")
    if not tg_token:
        with open("/app/backend/.env") as f:
            for line in f:
                if line.startswith("TELEGRAM_TOKEN"):
                    tg_token = line.split("=", 1)[1].strip().strip('"')
                    break
    if not tg_token or len(tg_token) < 10:
        pytest.skip("no telegram token configured")

    import glob
    leaked = []
    for path in glob.glob("/var/log/supervisor/backend*.log"):
        try:
            with open(path, "r", errors="ignore") as f:
                content = f.read()
            if tg_token in content:
                leaked.append(path)
        except Exception:
            pass
    assert not leaked, f"Telegram token leaked in logs: {leaked}"


@pytest.fixture(scope="module", autouse=True)
def _cleanup():
    yield
    async def rm():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        await db.users.delete_many({"telegram_user_id": TEST_UID})
        await db.wallets.delete_many({"telegram_user_id": TEST_UID})
        await db.transactions.delete_many({"telegram_user_id": TEST_UID})
        client.close()
    try:
        _run_async(rm())
    except Exception:
        pass
