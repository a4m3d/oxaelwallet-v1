"""Tests for the send-flow fix, ERC-20 send wiring, duplicate prevention,
wallet naming, tracking, capability flags and command-menu registration."""
import asyncio
import random
import pytest

from telegram import handlers, states
from telegram.bot import bot
from wallets import service as W
from assets import catalog


def _uid():
    return random.randint(10_000_000, 99_000_000)


def _cb(uid, data):
    return {"callback_query": {
        "id": "c", "from": {"id": uid, "first_name": "T"},
        "message": {"message_id": 10, "chat": {"id": uid, "type": "private"}, "text": "x"},
        "data": data,
    }}


@pytest.fixture
def captured(monkeypatch):
    sent = []

    async def fake_send(chat_id, text, markup=None, *a, **k):
        sent.append(text)
        return {"result": {"message_id": 10}}

    async def fake_edit(chat_id, mid, text, markup=None, *a, **k):
        sent.append(text)
        return {}

    async def noop(*a, **k):
        return {}

    monkeypatch.setattr(bot, "send_message", fake_send)
    monkeypatch.setattr(bot, "edit_message_text", fake_edit)
    monkeypatch.setattr(bot, "answer_callback_query", noop)
    monkeypatch.setattr(bot, "send_photo", noop)
    return sent


# ---- the reported bug: picking a network must advance, not loop back ----
def test_send_network_selection_advances_to_asset(captured):
    async def run():
        uid = _uid()
        await W.get_or_create_user(uid)
        w = await W.create_wallet(uid, "Main")
        wid = w["wallet_id"]
        await states.clear(uid)
        await handlers.process_update(_cb(uid, f"send|net|{wid}|ethereum"))
        joined = "\n".join(captured)
        assert "Which asset?" in joined          # advanced to asset picker
        assert "Select a network" not in joined   # NOT stuck on the network step
        await W.delete_wallet(uid, wid)
    asyncio.run(run())


def test_token_send_flow_sets_token_address(captured):
    async def run():
        uid = _uid()
        await W.get_or_create_user(uid)
        w = await W.create_wallet(uid, "Main")
        wid = w["wallet_id"]
        await handlers.process_update(_cb(uid, f"sendasset|{wid}|ethereum|USDC"))
        sess = await states.get(uid)
        assert sess["state"] == "send_await_address"
        assert sess["data"]["token_address"], "USDC contract must be set for a token send"
        assert sess["data"]["decimals"] == 6, "must use the token's real decimals"
        assert sess["data"]["asset"] == "USDC"
        await W.delete_wallet(uid, wid)
    asyncio.run(run())


def test_native_send_flow_has_no_token_address(captured):
    async def run():
        uid = _uid()
        await W.get_or_create_user(uid)
        w = await W.create_wallet(uid, "Main")
        wid = w["wallet_id"]
        await handlers.process_update(_cb(uid, f"sendasset|{wid}|ethereum|__native__"))
        sess = await states.get(uid)
        assert sess["state"] == "send_await_address"
        assert sess["data"]["token_address"] is None
        assert sess["data"]["decimals"] == 18
        await W.delete_wallet(uid, wid)
    asyncio.run(run())


# ---- duplicate wallet prevention ----
def test_duplicate_import_prevented_and_locatable():
    async def run():
        uid = _uid()
        await W.get_or_create_user(uid)
        pk = "0x" + "1" * 64
        w1 = await W.import_wallet(uid, "First", pk)
        with pytest.raises(ValueError) as e:
            await W.import_wallet(uid, "Second", pk)
        assert str(e.value) == "already_added"
        found = await W.find_wallet_by_address(uid, w1["addresses"]["evm"])
        assert found is not None and found["name"] == "First"
        await W.delete_wallet(uid, w1["wallet_id"])
    asyncio.run(run())


# ---- wallet naming ----
def test_wallet_naming_persisted():
    async def run():
        uid = _uid()
        await W.get_or_create_user(uid)
        w = await W.create_wallet(uid, "Trading Desk")
        assert w["name"] == "Trading Desk"
        again = await W.get_wallet(uid, w["wallet_id"])
        assert again["name"] == "Trading Desk"
        await W.rename_wallet(uid, w["wallet_id"], "Savings")
        assert (await W.get_wallet(uid, w["wallet_id"]))["name"] == "Savings"
        await W.delete_wallet(uid, w["wallet_id"])
    asyncio.run(run())


# ---- tracking ----
def test_tracking_default_on_and_toggle():
    async def run():
        uid = _uid()
        await W.get_or_create_user(uid)
        w = await W.create_wallet(uid, "Main")
        wid = w["wallet_id"]
        assert w["track_enabled"] is True
        await W.set_tracking(uid, wid, False)
        assert (await W.get_wallet(uid, wid))["track_enabled"] is False
        await W.set_tracking(uid, wid, True)
        assert (await W.get_wallet(uid, wid))["track_enabled"] is True
        await W.delete_wallet(uid, wid)
    asyncio.run(run())


# ---- capability registry ----
def test_token_send_and_tracking_capabilities():
    eth = catalog.get_network("ethereum")
    assert eth.capabilities.token_send_supported is True
    assert eth.capabilities.tracking_supported is True
    sol = catalog.get_network("solana")
    assert sol.capabilities.token_send_supported is False   # no SPL send implemented
    assert sol.capabilities.tracking_supported is True
    btc = catalog.get_network("bitcoin")
    assert btc.capabilities.token_send_supported is False


# ---- command menu ----
def test_command_menu_registers_all_commands():
    from server import BOT_COMMANDS
    cmds = {c["command"] for c in BOT_COMMANDS}
    for c in ["start", "wallet", "wallets", "create", "import", "balance",
              "send", "receive", "swap", "history", "track", "settings", "help"]:
        assert c in cmds, f"/{c} missing from command menu"


# ---- token catalog breadth (USDC/USDT/DAI/WETH, per-token decimals) ----
def test_token_catalog_has_stablecoins_and_weth():
    eth = {t["symbol"]: t for t in catalog.tokens_for("ethereum")}
    for sym in ("USDC", "USDT", "DAI", "WETH"):
        assert sym in eth, f"{sym} missing on ethereum"
    assert eth["USDC"]["decimals"] == 6
    assert eth["USDT"]["decimals"] == 6
    assert eth["DAI"]["decimals"] == 18   # decimals are NOT assumed uniform
    assert eth["WETH"]["decimals"] == 18
    # every EVM chain has at least USDC + DAI configured
    for net in ("base", "arbitrum", "optimism", "polygon", "bnb", "avalanche"):
        syms = {t["symbol"] for t in catalog.tokens_for(net)}
        assert "USDC" in syms and "DAI" in syms, f"{net} missing core tokens"


# ---- swap: choose a source ASSET (pay USDC, not just native) ----
def test_swap_source_asset_selection(captured, monkeypatch):
    async def fake_networks():
        return ["eth", "sol", "base", "arb"]
    monkeypatch.setattr(handlers.intents_client, "networks", fake_networks)

    async def run():
        uid = _uid()
        await W.get_or_create_user(uid)
        w = await W.create_wallet(uid, "Main")
        wid = w["wallet_id"]
        # pick source network -> asset picker
        await handlers.process_update(_cb(uid, f"swfrom|net|{wid}|ethereum"))
        assert any("Which asset will you pay with?" in t for t in captured)
        # pick USDC as the source asset -> dest picker, state carries token info
        await handlers.process_update(_cb(uid, f"swasset|{wid}|ethereum|USDC"))
        sess = await states.get(uid)
        assert sess["state"] == "swap_dest"
        assert sess["data"]["from_symbol"] == "USDC"
        assert sess["data"]["from_token_address"]
        assert sess["data"]["from_decimals"] == 6
        await W.delete_wallet(uid, wid)
    asyncio.run(run())


# ---- transaction state machine additions ----
def test_state_machine_has_estimating_and_replaced():
    from transactions import state_machine as sm
    assert sm.ESTIMATING in sm.ALL_STATES
    assert sm.REPLACED in sm.ALL_STATES
    assert sm.can_transition(sm.BROADCASTED, sm.REPLACED) is True
    assert sm.is_terminal(sm.REPLACED) is True


# ---- real receipt data available for confirmation persistence ----
def test_evm_exposes_receipt_details():
    from chains.evm import evm_adapter
    assert hasattr(evm_adapter, "get_receipt_details")
