from fastapi.testclient import TestClient

import server
from config import settings


def test_health_and_webhook_auth():
    with TestClient(server.app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

        # missing/invalid secret => forbidden
        bad = client.post("/api/telegram/webhook", json={"update_id": 1})
        assert bad.status_code == 403

        # valid secret => accepted; empty update is a no-op
        good = client.post(
            "/api/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": settings.TELEGRAM_WEBHOOK_SECRET},
            json={"update_id": 1},
        )
        assert good.status_code == 200
        assert good.json() == {"ok": True}


def test_health_exposes_no_secrets():
    with TestClient(server.app) as client:
        body = client.get("/api/health").text
        # An unset secret is the empty string, which is trivially a substring of
        # any text; only assert leakage for secrets that are actually configured.
        if settings.TELEGRAM_TOKEN:
            assert settings.TELEGRAM_TOKEN not in body
        if settings.WALLET_ENCRYPTION_KEY:
            assert settings.WALLET_ENCRYPTION_KEY not in body
