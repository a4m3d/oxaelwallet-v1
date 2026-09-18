"""Tests for the reliability fixes: RPC fallback, graceful provider failure,
automatic token discovery fallback, unknown-token handling and chain isolation."""
import asyncio
from decimal import Decimal

import pytest

from chains import evm as EVM
from chains.evm import evm_adapter, RpcUnavailable
from assets import discovery, catalog
from wallets import portfolio as P
from chains.base import AssetBalance


# ---- RPC fallback: every chain has multiple endpoints to fall back to ----
def test_every_evm_chain_has_multiple_rpcs():
    for net in ("ethereum", "base", "arbitrum", "optimism", "polygon", "bnb", "avalanche"):
        urls = EVM._rpc_urls(net)
        assert len(urls) >= 2, f"{net} needs >=2 RPC endpoints for fallback"


def test_env_override_rpc_is_tried_first(monkeypatch):
    monkeypatch.setattr(EVM.settings, "rpc", lambda k, d: "https://my.custom.rpc" if k == "ETHEREUM" else "")
    urls = EVM._rpc_urls("ethereum")
    assert urls[0] == "https://my.custom.rpc"
    assert len(urls) > 1  # defaults still present as fallback


def test_rpc_fallback_tries_next_endpoint(monkeypatch):
    calls = []

    def fake_w3_for(url):
        calls.append(url)
        class _W3:
            def __init__(self, ok):
                self._ok = ok
        return _W3(url.endswith("good"))

    monkeypatch.setattr(EVM, "_ordered_urls", lambda net: ["https://bad", "https://good"])
    monkeypatch.setattr(EVM, "_w3_for", fake_w3_for)

    def fn(w3):
        if not w3._ok:
            raise ConnectionError("boom")
        return "OK"

    assert EVM._sync_run("ethereum", fn) == "OK"
    assert calls == ["https://bad", "https://good"]  # fell back to the working one


def test_rpc_unavailable_when_all_fail(monkeypatch):
    monkeypatch.setattr(EVM, "_ordered_urls", lambda net: ["https://a", "https://b"])
    monkeypatch.setattr(EVM, "_w3_for", lambda url: object())

    def fn(w3):
        raise ConnectionError("down")

    with pytest.raises(RpcUnavailable):
        EVM._sync_run("ethereum", fn)


# ---- token discovery: safe fallback without a key ----
def test_discovery_disabled_without_key(monkeypatch):
    monkeypatch.setattr(discovery.settings, "ETHERSCAN_API_KEY", "")
    assert discovery.supported("ethereum") is False

    async def run():
        assert await discovery.discover_token_contracts("ethereum", "0xabc") == []
    asyncio.run(run())


def test_discovery_only_for_known_chains(monkeypatch):
    monkeypatch.setattr(discovery.settings, "ETHERSCAN_API_KEY", "KEY")
    assert discovery.supported("ethereum") is True
    assert discovery.supported("bitcoin") is False  # not an EVM chainid


# ---- portfolio: one network down must NOT fail the whole wallet ----
def test_portfolio_partial_outage(monkeypatch):
    async def fake_get_balances(net, addr):
        if net == "ethereum":
            return [AssetBalance("ETH", "ethereum", Decimal("1.5"), 18)]
        raise RpcUnavailable("provider down")

    async def fake_disc(net, addr):
        return []

    async def fake_prices(ids):
        return {}

    monkeypatch.setattr(P.evm_adapter, "get_balances", fake_get_balances)
    monkeypatch.setattr(P, "discover_token_contracts", fake_disc)
    monkeypatch.setattr(P, "get_prices", fake_prices)

    async def run():
        bals, total, st = await P.get_portfolio({"name": "x", "addresses": {"evm": "0xabc"}})
        assert st["ethereum"] == "ok"
        assert any(v == "unavailable" for k, v in st.items() if k != "ethereum")
        assert any(b.symbol == "ETH" for b in bals)  # working net still shows
    asyncio.run(run())


def test_portfolio_no_fake_price_total(monkeypatch):
    async def fake_get_balances(net, addr):
        if net == "ethereum":
            # a token with no reliable price must not inflate the total
            ab = AssetBalance("PEPE", "ethereum", Decimal("1000"), 18,
                              token_address="0xdeadbeef", verified=False)
            return [ab]
        raise RpcUnavailable("down")
    monkeypatch.setattr(P.evm_adapter, "get_balances", fake_get_balances)
    monkeypatch.setattr(P, "discover_token_contracts", lambda n, a: _empty())
    monkeypatch.setattr(P, "get_prices", lambda ids: _empty_dict())

    async def run():
        bals, total, st = await P.get_portfolio({"name": "x", "addresses": {"evm": "0xabc"}})
        assert total is None  # no reliable price -> no fabricated total
        assert bals and bals[0].usd_value is None
    asyncio.run(run())


async def _empty():
    return []


async def _empty_dict():
    return {}


# ---- chain isolation: a token is only sendable where it is configured ----
def test_token_chain_isolation():
    assert catalog.token_by_symbol("ethereum", "USDT") is not None
    assert catalog.token_by_symbol("base", "USDT") is None       # not configured on Base
    eth_usdc = catalog.token_by_symbol("ethereum", "USDC")
    base_usdc = catalog.token_by_symbol("base", "USDC")
    assert eth_usdc["address"] != base_usdc["address"]           # distinct contracts per chain
