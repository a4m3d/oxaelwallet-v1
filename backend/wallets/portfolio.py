"""Portfolio aggregation across a wallet's EVM networks and Solana.

Reliability & honesty:
- Native + configured-token balances come from the RPC-fallback EVM adapter.
- Additional held tokens are discovered via the explorer (Etherscan V2) and then
  verified on-chain (balanceOf) before display — nothing is shown unverified-as-held.
- Each network reports a status ("ok" | "unavailable") so the UI can show which
  networks loaded and which had a provider outage, instead of failing the wallet.
- USD value is only applied where a reliable price exists; discovered/unknown
  tokens without pricing are shown without a fabricated value.
"""
from __future__ import annotations
import asyncio
from decimal import Decimal

from assets.catalog import NETWORKS, evm_networks, tokens_for
from assets.discovery import discover_token_contracts
from chains.evm import evm_adapter
from chains.solana import solana_adapter
from chains.base import AssetBalance
from utils.prices import get_prices


async def _verify_token(network_key: str, address: str, tok: dict) -> AssetBalance | None:
    try:
        amt = await evm_adapter.token_balance(network_key, tok, address)
    except Exception:  # noqa: BLE001  (reverts / non-standard tokens)
        return None
    if amt <= 0:
        return None
    return AssetBalance(
        symbol=(tok.get("symbol") or "Unknown"),
        network=network_key,
        amount=amt,
        decimals=int(tok.get("decimals", 18)),
        token_address=tok["address"],
        name=(tok.get("name") or None),
        verified=False,  # discovered, not from our curated catalog
    )


async def _evm_network_assets(network_key: str, address: str) -> tuple[list[AssetBalance], str]:
    """(balances, status) for one EVM network. status in {'ok','unavailable'}."""
    try:
        bals = await evm_adapter.get_balances(network_key, address)  # native + catalog tokens
        status = "ok"
    except Exception:  # noqa: BLE001  (RpcUnavailable etc.)
        return [], "unavailable"

    have = {(b.token_address or "").lower() for b in bals if b.token_address}
    catalog_addrs = {t["address"].lower() for t in tokens_for(network_key)}
    candidates = [
        tok for tok in await discover_token_contracts(network_key, address)
        if tok["address"].lower() not in have and tok["address"].lower() not in catalog_addrs
    ][:25]
    if candidates:
        verified = await asyncio.gather(*[_verify_token(network_key, address, t) for t in candidates])
        bals.extend([b for b in verified if b is not None])
    return bals, status


async def get_portfolio(wallet: dict) -> tuple[list[AssetBalance], Decimal | None, dict]:
    addresses = wallet.get("addresses", {})
    balances: list[AssetBalance] = []
    statuses: dict[str, str] = {}

    evm_addr = addresses.get("evm")
    tasks = []
    nets = list(evm_networks()) if evm_addr else []
    for net in nets:
        tasks.append(_evm_network_assets(net.key, evm_addr))
    if "solana" in addresses:
        async def _sol():
            try:
                return await solana_adapter.get_balances(addresses["solana"]), "ok"
            except Exception:  # noqa: BLE001
                return [], "unavailable"
        tasks.append(_sol())

    results = await asyncio.gather(*tasks) if tasks else []
    keys = [n.key for n in nets] + (["solana"] if "solana" in addresses else [])
    for key, (bals, status) in zip(keys, results):
        statuses[key] = status
        balances.extend(bals)

    # keep only assets actually held
    balances = [b for b in balances if b.amount > 0]

    # ---- pricing (only where a reliable coingecko id exists; never fabricated) ----
    def _cg_id(b: AssetBalance) -> str | None:
        net = NETWORKS.get(b.network)
        if b.token_address:
            for t in tokens_for(b.network):
                if t["address"].lower() == b.token_address.lower():
                    return t["coingecko_id"]
            return None
        return net.coingecko_id if net else None

    cg_ids = {cid for cid in (_cg_id(b) for b in balances) if cid}
    prices = await get_prices(list(cg_ids))

    total = Decimal(0)
    have_price = False
    for b in balances:
        cid = _cg_id(b)
        if cid and cid in prices:
            b.usd_value = b.amount * prices[cid]
            total += b.usd_value
            have_price = True

    balances.sort(key=lambda x: (x.usd_value or Decimal(0)), reverse=True)
    return balances, (total if have_price else None), statuses
