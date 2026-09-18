"""Background watchers: transaction confirmations, incoming deposits, swap status.

Runs as asyncio tasks started from the app lifespan. All Telegram DMs use
chat_id == telegram_user_id (valid for private chats). Everything is best-effort
and wrapped in try/except so a single RPC hiccup never kills the loop.
"""
from __future__ import annotations
import asyncio
import logging
from decimal import Decimal

import database as dbm
from telegram.bot import bot
from transactions import service as TX
from assets.catalog import NETWORKS, evm_networks, tokens_for
from assets import discovery
from chains.evm import evm_adapter
from chains.solana import solana_adapter
from intents.near_intents import client as intents_client
from utils.format import fmt_amount

logger = logging.getLogger("oxael.watcher")

CONFIRM_INTERVAL = 25
RECEIVE_INTERVAL = 60
SWAP_INTERVAL = 30

_tasks: list[asyncio.Task] = []


async def _notify(uid: int, text: str, markup: dict | None = None):
    try:
        await bot.send_message(uid, text, markup)
    except Exception:  # noqa: BLE001
        logger.debug("notify failed uid=%s", uid)


# ---------------- confirmations ----------------
async def _confirm_loop():
    while True:
        await asyncio.sleep(CONFIRM_INTERVAL)
        try:
            for tx in await TX.confirmable():
                await _check_one(tx)
        except Exception:  # noqa: BLE001
            logger.exception("confirm loop error")


async def _check_one(tx: dict):
    net = NETWORKS.get(tx["network"])
    tx_hash = tx.get("tx_hash")
    uid = tx["telegram_user_id"]
    receipt: dict = {}
    try:
        if tx["family"] == "evm":
            receipt = await evm_adapter.get_receipt_details(tx["network"], tx_hash) or {}
            status = receipt.get("status")
            if status is None:
                return
            confirmed = status == 1
        elif tx["family"] == "solana":
            st = await solana_adapter.get_signature_status(tx_hash)
            if st is None:
                return
            if st == "failed":
                confirmed = False
            elif st in ("confirmed", "finalized"):
                confirmed = True
            else:
                return
        else:
            return
    except Exception:  # noqa: BLE001
        return

    explorer = net.explorer_tx(tx_hash) if net else None
    link = f'\n<a href="{explorer}">view on explorer ↗</a>' if explorer else ""
    if confirmed:
        await TX.set_confirmed(
            tx["tx_id"],
            block_number=receipt.get("block_number"),
            gas_used=receipt.get("gas_used"),
            gas_price=receipt.get("gas_price"),
            fee_native=receipt.get("fee_native"),
        )
        fee_line = ""
        if receipt.get("fee_native") and net:
            fee_line = f"\nNetwork fee: {fmt_amount(Decimal(receipt['fee_native']))} {net.symbol}"
        block_line = f"\nBlock: {receipt['block_number']}" if receipt.get("block_number") else ""
        await _notify(uid, f"✅ <b>Transaction confirmed.</b>\n\n"
                           f"{fmt_amount(Decimal(tx['amount']))} {tx['asset']} · {net.name if net else tx['network']}"
                           f"{fee_line}{block_line}{link}")
    else:
        await TX.set_failed(tx["tx_id"])
        await _notify(uid, f"⚠️ <b>Transaction failed on-chain.</b>\n\n"
                           f"The transaction was not confirmed (reverted).\n"
                           f"{fmt_amount(Decimal(tx['amount']))} {tx['asset']} · {net.name if net else tx['network']}{link}")


# ---------------- incoming deposit tracking ----------------
async def _receive_loop():
    while True:
        await asyncio.sleep(RECEIVE_INTERVAL)
        try:
            cur = dbm.wallets.find({"track_enabled": {"$ne": False}}, {"_id": 0})
            async for w in cur:
                await _scan_wallet(w)
        except Exception:  # noqa: BLE001
            logger.exception("receive loop error")


async def _scan_wallet(w: dict):
    uid = w["telegram_user_id"]
    wid = w["wallet_id"]
    accounts = w.get("accounts", {})
    if "evm" in accounts:
        addr = accounts["evm"]["address"]
        for net in evm_networks():
            handled = await _scan_evm_incoming(uid, wid, w, net.key, addr)
            if handled:
                continue
            # fallback (no explorer key): balance-snapshot detection
            await _check_asset(uid, wid, w, net.key, "evm", addr, net.symbol, None, 18)
            for tok in tokens_for(net.key):
                await _check_asset(uid, wid, w, net.key, "evm", addr,
                                   tok["symbol"], tok["address"], tok["decimals"])
    if "solana" in accounts:
        sol = NETWORKS.get("solana")
        await _check_asset(uid, wid, w, "solana", "solana",
                           accounts["solana"]["address"], sol.symbol, None, 9)


async def _scan_evm_incoming(uid: int, wid: str, w: dict, network: str, addr: str) -> bool:
    """Explorer-based incoming detection with real tx hash + sender.

    Seeds silently on the first scan of a (wallet, network) so pre-existing
    history is recorded for the feed but never spams notifications; afterwards
    only genuinely new deposits notify. Returns False if the explorer isn't
    available so the caller can fall back to balance snapshots.
    """
    if not discovery.supported(network):
        return False
    try:
        transfers = await discovery.get_incoming_transfers(network, addr)
    except Exception:  # noqa: BLE001
        return True  # explorer supported but transient failure; skip this cycle
    net = NETWORKS.get(network)
    cursor = await dbm.track_cursor.find_one({"wallet_id": wid, "network": network})
    last_ts = cursor["last_ts"] if cursor else None
    seed = last_ts is None
    max_ts = last_ts or 0
    for t in sorted(transfers, key=lambda x: x["ts"]):  # oldest first
        ts = t["ts"]
        if not seed and ts <= (last_ts or 0):
            continue
        inserted = await TX.record_detected_receive(
            uid, wid, network, t["symbol"] or (net.symbol if net else ""),
            t["amount"], tx_hash=t.get("hash"), from_address=t.get("from_addr"),
            token_address=t.get("token_address"),
        )
        max_ts = max(max_ts, ts)
        if inserted and not seed:
            link = ""
            if net and t.get("hash"):
                link = f'\n<a href="{net.explorer_tx(t["hash"])}">view on explorer ↗</a>'
            frm = t.get("from_addr") or ""
            await _notify(uid,
                f"🔔 <b>Incoming {t['symbol']}</b>\n\n"
                f"+{fmt_amount(Decimal(t['amount']))} {t['symbol']} on {net.name if net else network}\n"
                f"Wallet: <b>{w.get('name','')}</b>\n"
                f"From: <code>{frm[:10]}…{frm[-6:]}</code>{link}")
    await dbm.track_cursor.update_one(
        {"wallet_id": wid, "network": network},
        {"$set": {"telegram_user_id": uid, "last_ts": max_ts or int(_now_ts())}},
        upsert=True,
    )
    return True


def _now_ts() -> int:
    import time
    return int(time.time())


async def _check_asset(uid: int, wid: str, w: dict, network: str, family: str,
                       addr: str, symbol: str, token_address: str | None, decimals: int):
    net = NETWORKS.get(network)
    try:
        if token_address:
            bal = await evm_adapter.token_balance(
                network, {"address": token_address, "decimals": decimals}, addr)
        elif family == "evm":
            bal = await evm_adapter.native_balance(network, addr)
        else:
            bal = await solana_adapter.native_balance(addr)
    except Exception:  # noqa: BLE001
        return
    snap = await dbm.balance_snapshots.find_one(
        {"wallet_id": wid, "network": network, "symbol": symbol}
    )
    prev = Decimal(snap["amount"]) if snap else None
    await dbm.balance_snapshots.update_one(
        {"wallet_id": wid, "network": network, "symbol": symbol},
        {"$set": {"telegram_user_id": uid, "amount": str(bal)}},
        upsert=True,
    )
    if prev is not None and bal > prev:
        diff = bal - prev
        await TX.record_detected_receive(uid, wid, network, symbol, str(diff))
        await _notify(uid, f"🟢 <b>Incoming {symbol}</b>\n\n"
                           f"+{fmt_amount(diff)} {symbol} on {net.name if net else network}\n"
                           f"Wallet: <b>{w.get('name','')}</b>")


# ---------------- swap status ----------------
async def _swap_loop():
    while True:
        await asyncio.sleep(SWAP_INTERVAL)
        try:
            cur = dbm.swap_orders.find(
                {"state": {"$in": ["awaiting_deposit", "processing"]}, "deposit_address": {"$ne": None}},
                {"_id": 0},
            )
            async for o in cur:
                await _check_swap(o)
        except Exception:  # noqa: BLE001
            logger.exception("swap loop error")


async def _check_swap(o: dict):
    uid = o["telegram_user_id"]
    try:
        st = await intents_client.get_status(o["deposit_address"], o.get("deposit_memo"))
    except Exception:  # noqa: BLE001
        return
    status = (st or {}).get("status", "")
    if status in ("PROCESSING", "KNOWN_DEPOSIT_TX") and o["state"] != "processing":
        await dbm.swap_orders.update_one({"order_id": o["order_id"]}, {"$set": {"state": "processing"}})
        await _notify(uid, "🔄 <b>Swap processing.</b>\nYour deposit was detected and routing has begun.")
    elif status == "SUCCESS":
        await dbm.swap_orders.update_one({"order_id": o["order_id"]}, {"$set": {"state": "success"}})
        details = (st or {}).get("swapDetails", {}) or {}
        out = details.get("amountOutFormatted", "")
        await _notify(uid, f"✅ <b>Swap completed.</b>\nReceived ~<b>{out}</b> on {o.get('dest_chain','')}.")
    elif status == "REFUNDED":
        await dbm.swap_orders.update_one({"order_id": o["order_id"]}, {"$set": {"state": "refunded"}})
        await _notify(uid, "↩️ <b>Swap refunded.</b>\nYour deposit was returned to your wallet.")
    elif status == "FAILED":
        await dbm.swap_orders.update_one({"order_id": o["order_id"]}, {"$set": {"state": "failed"}})
        await _notify(uid, "⚠️ <b>Swap failed.</b>")


def start():
    if _tasks:
        return
    loop = asyncio.get_event_loop()
    _tasks.append(loop.create_task(_confirm_loop()))
    _tasks.append(loop.create_task(_receive_loop()))
    _tasks.append(loop.create_task(_swap_loop()))
    logger.info("watchers started")


def stop():
    for t in _tasks:
        t.cancel()
    _tasks.clear()
