"""NEAR Intents / 1Click cross-chain routing client.

Wraps the 1Click REST API (https://1click.chaindefuser.com):
  GET  /v0/tokens        dynamic asset & network catalog
  POST /v0/quote         request a swap quote (dry=true to simulate)
  GET  /v0/status        poll swap execution status by depositAddress

Credentials come from env (NEAR_INTENTS_BASE, NEAR_INTENTS_JWT). Nothing is
hard-coded, and no fixed network count is assumed — the catalog is discovered
dynamically. Errors are handled safely and never leak the JWT.
"""
from __future__ import annotations
import logging
from typing import Any

import httpx

from config import settings

logger = logging.getLogger("oxael.intents")


class NearIntentsError(Exception):
    pass


class NearIntentsClient:
    def __init__(self, base: str | None = None, jwt: str | None = None):
        self.base = (base if base is not None else settings.NEAR_INTENTS_BASE).rstrip("/")
        self.jwt = jwt if jwt is not None else settings.NEAR_INTENTS_JWT

    @property
    def configured(self) -> bool:
        return bool(self.base)

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if settings.NEAR_INTENTS_API_KEY:
            h["X-API-Key"] = settings.NEAR_INTENTS_API_KEY
        if self.jwt:
            h["Authorization"] = f"Bearer {self.jwt}"
        return h

    async def _request(self, method: str, path: str, *, params=None, json=None) -> Any:
        if not self.configured:
            raise NearIntentsError("NEAR Intents base URL is not configured")
        url = f"{self.base}{path}"
        try:
            async with httpx.AsyncClient(timeout=25) as c:
                r = await c.request(method, url, params=params, json=json, headers=self._headers())
        except httpx.HTTPError as exc:
            raise NearIntentsError(f"network error contacting 1Click: {exc.__class__.__name__}") from exc
        if r.status_code == 401:
            raise NearIntentsError("1Click rejected credentials (check NEAR_INTENTS_JWT)")
        if r.status_code >= 400:
            raise NearIntentsError(f"1Click error {r.status_code}")
        if not r.content:
            return None
        return r.json()

    async def get_tokens(self) -> list[dict]:
        """Dynamic asset/network discovery. Returns the live supported catalog."""
        data = await self._request("GET", "/v0/tokens")
        if isinstance(data, dict) and "tokens" in data:
            return data["tokens"]
        return data or []

    async def request_quote(self, payload: dict, dry: bool = True) -> dict:
        body = dict(payload)
        body.setdefault("dry", dry)
        return await self._request("POST", "/v0/quote", json=body)

    async def submit_deposit(self, tx_hash: str, deposit_address: str,
                             deposit_memo: str | None = None) -> dict | None:
        """Notify 1Click that the deposit tx was broadcast so routing starts
        immediately (best-effort; polling still works if this is unavailable)."""
        body: dict = {"txHash": tx_hash, "depositAddress": deposit_address}
        if deposit_memo:
            body["depositMemo"] = deposit_memo
        try:
            return await self._request("POST", "/v0/deposit/submit", json=body)
        except NearIntentsError as e:  # non-fatal
            logger.info("deposit submit skipped: %s", e)
            return None

    async def get_status(self, deposit_address: str, deposit_memo: str | None = None) -> dict:
        params = {"depositAddress": deposit_address}
        if deposit_memo:
            params["depositMemo"] = deposit_memo
        return await self._request("GET", "/v0/status", params=params)

    async def networks(self) -> list[str]:
        """Distinct blockchains available via routing (no fixed count)."""
        tokens = await self.get_tokens()
        seen = []
        for t in tokens:
            chain = t.get("blockchain") or t.get("chain")
            if chain and chain not in seen:
                seen.append(chain)
        return seen


client = NearIntentsClient()
