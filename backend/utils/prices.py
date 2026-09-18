"""Best-effort USD pricing via CoinGecko. Never fabricates a price.

If pricing is unavailable, callers omit USD valuation entirely.
"""
from __future__ import annotations
import time
from decimal import Decimal

import httpx

_CACHE: dict[str, tuple[float, Decimal]] = {}
_TTL = 120  # seconds
_URL = "https://api.coingecko.com/api/v3/simple/price"


async def get_prices(coingecko_ids: list[str]) -> dict[str, Decimal]:
    ids = sorted({c for c in coingecko_ids if c})
    if not ids:
        return {}
    now = time.time()
    result: dict[str, Decimal] = {}
    missing = []
    for cid in ids:
        cached = _CACHE.get(cid)
        if cached and now - cached[0] < _TTL:
            result[cid] = cached[1]
        else:
            missing.append(cid)
    if missing:
        try:
            async with httpx.AsyncClient(timeout=12) as c:
                r = await c.get(_URL, params={"ids": ",".join(missing), "vs_currencies": "usd"})
                r.raise_for_status()
                data = r.json()
            for cid, obj in data.items():
                if "usd" in obj:
                    price = Decimal(str(obj["usd"]))
                    _CACHE[cid] = (now, price)
                    result[cid] = price
        except Exception:  # noqa: BLE001
            pass  # pricing unavailable — omit USD valuation
    return result
