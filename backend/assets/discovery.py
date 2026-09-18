"""Automatic ERC-20 token discovery via the Etherscan V2 unified API.

A single ETHERSCAN_API_KEY works across all EVM chains (chainid parameter).
We list the address's ERC-20 transfer events to learn which token contracts it
has touched (with their symbol/name/decimals from the event), then callers verify
the *current* balance on-chain via RPC balanceOf. This never invents balances and
degrades gracefully: if no key is set or the API fails, discovery returns [] and
the wallet falls back to the configured token catalog.
"""
from __future__ import annotations
import logging
import time

import httpx

from config import settings

logger = logging.getLogger("oxael.discovery")

_BASE = "https://api.etherscan.io/v2/api"
_TTL = 90  # seconds
_MAX_CONTRACTS = 40
_CACHE: dict[tuple[str, str], tuple[float, list[dict]]] = {}

# our network key -> Etherscan V2 chainid
_CHAIN_ID = {
    "ethereum": 1, "base": 8453, "arbitrum": 42161, "optimism": 10,
    "polygon": 137, "bnb": 56, "avalanche": 43114,
}


def supported(network_key: str) -> bool:
    return bool(settings.ETHERSCAN_API_KEY) and network_key in _CHAIN_ID


async def discover_token_contracts(network_key: str, address: str) -> list[dict]:
    """Return candidate tokens [{address, symbol, name, decimals}] the wallet has
    interacted with. Empty list if unsupported/unavailable (safe fallback)."""
    if not supported(network_key) or not address:
        return []
    key = (network_key, address.lower())
    now = time.time()
    cached = _CACHE.get(key)
    if cached and now - cached[0] < _TTL:
        return cached[1]

    params = {
        "chainid": _CHAIN_ID[network_key],
        "module": "account",
        "action": "tokentx",
        "address": address,
        "page": 1,
        "offset": 200,
        "sort": "desc",
        "apikey": settings.ETHERSCAN_API_KEY,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as h:
            r = await h.get(_BASE, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("discovery failed net=%s status=%s", network_key, type(e).__name__)
        return []

    result = data.get("result")
    tokens: dict[str, dict] = {}
    if isinstance(result, list):
        for t in result:
            ca = (t.get("contractAddress") or "").lower()
            if not ca or ca in tokens:
                continue
            try:
                dec = int(t.get("tokenDecimal") or 18)
            except (TypeError, ValueError):
                dec = 18
            tokens[ca] = {
                "address": ca,
                "symbol": (t.get("tokenSymbol") or "")[:16],
                "name": (t.get("tokenName") or "")[:40],
                "decimals": dec,
            }
            if len(tokens) >= _MAX_CONTRACTS:
                break
    out = list(tokens.values())
    _CACHE[key] = (now, out)
    logger.info("discovery net=%s address_touched_tokens=%d", network_key, len(out))
    return out


async def get_incoming_transfers(network_key: str, address: str) -> list[dict]:
    """Recent INCOMING transfers (native + ERC-20) to `address` on this chain.

    Returns [{hash, from_addr, symbol, name, amount, decimals, ts, token_address}]
    newest-first. Empty if unsupported/unavailable (safe fallback). Read-only.
    """
    if not supported(network_key) or not address:
        return []
    cid = _CHAIN_ID[network_key]
    addr = address.lower()
    out: list[dict] = []

    async def _call(action: str) -> list:
        params = {
            "chainid": cid, "module": "account", "action": action,
            "address": address, "page": 1, "offset": 25, "sort": "desc",
            "apikey": settings.ETHERSCAN_API_KEY,
        }
        try:
            async with httpx.AsyncClient(timeout=15) as h:
                r = await h.get(_BASE, params=params)
                r.raise_for_status()
                data = r.json()
        except Exception as e:  # noqa: BLE001
            logger.warning("incoming scan failed net=%s action=%s err=%s",
                           network_key, action, type(e).__name__)
            return []
        res = data.get("result")
        return res if isinstance(res, list) else []

    # native coin transfers
    for t in await _call("txlist"):
        try:
            if (t.get("to") or "").lower() != addr:
                continue
            val = int(t.get("value") or 0)
            if val <= 0 or (t.get("isError") not in (None, "0")):
                continue
            out.append({
                "hash": t.get("hash"), "from_addr": t.get("from"),
                "symbol": _NATIVE_SYMBOL.get(network_key, ""), "name": "",
                "amount": str(_to_decimal(val, 18)), "decimals": 18,
                "ts": int(t.get("timeStamp") or 0), "token_address": None,
            })
        except Exception:  # noqa: BLE001
            continue

    # ERC-20 transfers
    for t in await _call("tokentx"):
        try:
            if (t.get("to") or "").lower() != addr:
                continue
            dec = int(t.get("tokenDecimal") or 18)
            val = int(t.get("value") or 0)
            if val <= 0:
                continue
            out.append({
                "hash": t.get("hash"), "from_addr": t.get("from"),
                "symbol": (t.get("tokenSymbol") or "TOKEN")[:16],
                "name": (t.get("tokenName") or "")[:40],
                "amount": str(_to_decimal(val, dec)), "decimals": dec,
                "ts": int(t.get("timeStamp") or 0),
                "token_address": (t.get("contractAddress") or "").lower() or None,
            })
        except Exception:  # noqa: BLE001
            continue

    out.sort(key=lambda x: x["ts"], reverse=True)
    return out[:30]


_NATIVE_SYMBOL = {
    "ethereum": "ETH", "base": "ETH", "arbitrum": "ETH", "optimism": "ETH",
    "polygon": "POL", "bnb": "BNB", "avalanche": "AVAX",
}


def _to_decimal(raw: int, decimals: int):
    from decimal import Decimal
    return Decimal(raw) / Decimal(10 ** int(decimals))
