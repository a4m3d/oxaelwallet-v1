"""Portfolio aggregation across a wallet's EVM networks and Solana."""
from __future__ import annotations
import asyncio
from decimal import Decimal

from assets.catalog import NETWORKS, evm_networks
from assets.capabilities import Family
from chains.evm import evm_adapter
from chains.solana import solana_adapter
from chains.base import AssetBalance
from utils.prices import get_prices


async def _safe(coro):
    try:
        return await coro
    except Exception:  # noqa: BLE001
        return None


async def get_portfolio(wallet: dict) -> tuple[list[AssetBalance], Decimal | None]:
    addresses = wallet.get("addresses", {})
    tasks = []
    if "evm" in addresses:
        addr = addresses["evm"]
        for net in evm_networks():
            tasks.append(("evm", net.key, _safe(evm_adapter.get_balances(net.key, addr))))
    if "solana" in addresses:
        tasks.append(("solana", "solana", _safe(solana_adapter.get_balances(addresses["solana"]))))

    results = await asyncio.gather(*[t[2] for t in tasks])
    balances: list[AssetBalance] = []
    for res in results:
        if res:
            for b in res:
                if b.amount > 0:
                    balances.append(b)

    # price
    cg_ids = set()
    for b in balances:
        net = NETWORKS.get(b.network)
        if b.token_address:
            from assets.catalog import tokens_for
            for t in tokens_for(b.network):
                if t["symbol"] == b.symbol:
                    cg_ids.add(t["coingecko_id"])
        elif net and net.coingecko_id:
            cg_ids.add(net.coingecko_id)
    prices = await get_prices(list(cg_ids))

    total = Decimal(0)
    have_price = False
    for b in balances:
        net = NETWORKS.get(b.network)
        cid = None
        if b.token_address:
            from assets.catalog import tokens_for
            for t in tokens_for(b.network):
                if t["symbol"] == b.symbol:
                    cid = t["coingecko_id"]
        elif net:
            cid = net.coingecko_id
        if cid and cid in prices:
            b.usd_value = (b.amount * prices[cid])
            total += b.usd_value
            have_price = True

    balances.sort(key=lambda x: (x.usd_value or Decimal(0)), reverse=True)
    return balances, (total if have_price else None)
