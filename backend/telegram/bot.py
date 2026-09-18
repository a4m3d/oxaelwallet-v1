"""Thin async Telegram Bot API client (httpx). HTML parse mode throughout."""
from __future__ import annotations
import logging
import httpx

from config import settings

logger = logging.getLogger("oxael.bot")


class TelegramBot:
    def __init__(self, token: str):
        self.token = token
        self.api = f"https://api.telegram.org/bot{token}"

    @property
    def configured(self) -> bool:
        return bool(self.token)

    async def _post(self, method: str, payload: dict) -> dict | None:
        if not self.configured:
            logger.warning("telegram not configured; skipping %s", method)
            return None
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{self.api}/{method}", json=payload)
            data = r.json()
            if not data.get("ok"):
                logger.warning("telegram %s failed: %s", method, data.get("description"))
            return data

    async def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None,
                           disable_preview: bool = True) -> dict | None:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                   "disable_web_page_preview": disable_preview}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return await self._post("sendMessage", payload)

    async def edit_message_text(self, chat_id: int, message_id: int, text: str,
                                reply_markup: dict | None = None) -> dict | None:
        payload = {"chat_id": chat_id, "message_id": message_id, "text": text,
                   "parse_mode": "HTML", "disable_web_page_preview": True}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return await self._post("editMessageText", payload)

    async def answer_callback_query(self, callback_query_id: str, text: str | None = None,
                                    show_alert: bool = False) -> dict | None:
        payload = {"callback_query_id": callback_query_id, "show_alert": show_alert}
        if text:
            payload["text"] = text
        return await self._post("answerCallbackQuery", payload)

    async def send_photo(self, chat_id: int, photo: bytes, caption: str = "",
                         reply_markup: dict | None = None) -> dict | None:
        if not self.configured:
            return None
        data = {"chat_id": str(chat_id), "caption": caption, "parse_mode": "HTML"}
        if reply_markup:
            import json
            data["reply_markup"] = json.dumps(reply_markup)
        files = {"photo": ("qr.png", photo, "image/png")}
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{self.api}/sendPhoto", data=data, files=files)
            return r.json()

    async def set_webhook(self, url: str, secret: str) -> dict | None:
        return await self._post("setWebhook", {
            "url": url,
            "secret_token": secret,
            "allowed_updates": ["message", "callback_query"],
            "drop_pending_updates": True,
        })

    async def delete_webhook(self) -> dict | None:
        return await self._post("deleteWebhook", {"drop_pending_updates": False})

    async def get_me(self) -> dict | None:
        return await self._post("getMe", {})

    async def set_my_commands(self, commands: list[dict]) -> dict | None:
        return await self._post("setMyCommands", {"commands": commands})


bot = TelegramBot(settings.TELEGRAM_TOKEN)
