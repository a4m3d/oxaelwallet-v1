"""OXAEL WALLET — FastAPI entrypoint (Telegram webhook + health).

Telegram is the only user interface. This service exposes just what's needed:
the webhook, a health probe, and an optional email verification callback.
No wallet data, secrets, or decrypted material is ever exposed via HTTP.
"""
import asyncio
import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, APIRouter, Request, Header, HTTPException
from fastapi.responses import HTMLResponse
from starlette.middleware.cors import CORSMiddleware

from config import settings, BRAND_NAME
import database as dbm
from security import redaction
from telegram.bot import bot
from telegram import handlers
from services import email_service
from services import watcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
redaction.install(extra_secrets=[
    settings.TELEGRAM_TOKEN, settings.WALLET_ENCRYPTION_KEY,
    settings.NEAR_INTENTS_JWT, settings.SMTP_PASSWORD, settings.MONGO_URL,
])
logger = logging.getLogger("oxael.server")

BOT_COMMANDS = [
    {"command": "start", "description": "Start OXAEL Wallet"},
    {"command": "wallet", "description": "Open your active wallet"},
    {"command": "wallets", "description": "Manage your wallets"},
    {"command": "create", "description": "Create a new wallet"},
    {"command": "import", "description": "Import an existing wallet"},
    {"command": "balance", "description": "View balances"},
    {"command": "send", "description": "Send crypto"},
    {"command": "receive", "description": "Receive crypto (address + QR)"},
    {"command": "swap", "description": "Cross-chain swap"},
    {"command": "history", "description": "Transaction history"},
    {"command": "track", "description": "Track wallet activity"},
    {"command": "settings", "description": "Settings & security"},
    {"command": "help", "description": "How OXAEL works"},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    await dbm.ensure_indexes()
    await dbm.balance_snapshots.create_index(
        [("wallet_id", 1), ("network", 1), ("symbol", 1)], unique=True
    )
    if settings.telegram_configured and settings.PUBLIC_BASE_URL:
        url = f"{settings.PUBLIC_BASE_URL}/api/telegram/webhook"
        res = await bot.set_webhook(url, settings.TELEGRAM_WEBHOOK_SECRET)
        logger.info("webhook set ok=%s", bool(res and res.get("ok")))
        await bot.set_my_commands(BOT_COMMANDS)
    else:
        logger.info("webhook not set (PUBLIC_BASE_URL or TELEGRAM_TOKEN missing) — dev mode")
    watcher.start()
    yield
    watcher.stop()
    dbm.close()


app = FastAPI(title=f"{BRAND_NAME} API", lifespan=lifespan)
api = APIRouter(prefix="/api")


@api.get("/health")
async def health():
    return {"status": "ok", "service": "oxael-wallet"}


@api.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    expected = settings.TELEGRAM_WEBHOOK_SECRET
    provided = x_telegram_bot_api_secret_token or ""
    if not expected or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="forbidden")
    update = await request.json()
    asyncio.create_task(handlers.process_update(update))
    return {"ok": True}


@api.get("/email/verify", response_class=HTMLResponse)
async def email_verify(token: str):
    result = await email_service.verify(token)
    ok = result is not None
    msg = "Your email is verified." if ok else "This verification link is invalid or has expired."
    return HTMLResponse(
        f"""<!doctype html><html><body style="margin:0;background:#0a0b0f;color:#e7ecf3;
        font-family:-apple-system,Segoe UI,Roboto,sans-serif;display:flex;height:100vh;
        align-items:center;justify-content:center;">
        <div style="text-align:center;max-width:360px;padding:28px;background:#111219;
        border:1px solid #1e2130;border-radius:16px;">
        <div style="letter-spacing:5px;color:#5eead4;font-size:12px;font-weight:700;">{BRAND_NAME}</div>
        <h2 style="margin:16px 0 8px;">{'✓ Verified' if ok else 'Link expired'}</h2>
        <p style="color:#9aa4b2;">{msg}</p></div></body></html>""",
        status_code=200 if ok else 400,
    )


app.include_router(api)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=settings.CORS_ORIGINS.split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
