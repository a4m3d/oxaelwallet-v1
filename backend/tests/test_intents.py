import asyncio
from intents.near_intents import NearIntentsClient


_SAMPLE = [
    {"assetId": "nep141:eth.omft.near", "symbol": "ETH", "blockchain": "eth", "decimals": 18},
    {"assetId": "nep141:base.omft.near", "symbol": "ETH", "blockchain": "base", "decimals": 18},
    {"assetId": "nep141:sol.omft.near", "symbol": "SOL", "blockchain": "sol", "decimals": 9},
    {"assetId": "nep141:btc.omft.near", "symbol": "BTC", "blockchain": "btc", "decimals": 8},
]


def test_configured_flag():
    c = NearIntentsClient(base="https://1click.chaindefuser.com", jwt="")
    assert c.configured is True
    c2 = NearIntentsClient(base="", jwt="")
    assert c2.configured is False


def test_dynamic_network_discovery(monkeypatch):
    c = NearIntentsClient(base="https://x", jwt="j")

    async def fake_request(method, path, **kw):
        return {"tokens": _SAMPLE}

    monkeypatch.setattr(c, "_request", fake_request)

    async def run():
        tokens = await c.get_tokens()
        assert len(tokens) == 4
        nets = await c.networks()
        # discovered dynamically, no hardcoded count
        assert set(nets) == {"eth", "base", "sol", "btc"}
    asyncio.run(run())


def test_auth_header_present_only_with_jwt():
    with_jwt = NearIntentsClient(base="https://x", jwt="secret")._headers()
    without = NearIntentsClient(base="https://x", jwt="")._headers()
    assert with_jwt.get("Authorization") == "Bearer secret"
    assert "Authorization" not in without
