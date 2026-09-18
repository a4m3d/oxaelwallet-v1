"""Telegram update handlers — routes commands, callbacks and flow inputs.

Navigation edits the active message so the bot feels like a single app.
No secret ever touches callback_data, logs, or outbound messages.
"""
from __future__ import annotations
import html
import logging
import uuid
from decimal import Decimal, InvalidOperation

from config import BRAND_NAME, BRAND_TAGLINE, settings as _settings


def _referral() -> str:
    return _settings.NEAR_INTENTS_REFERRAL or "oxael"
from telegram.bot import bot
from telegram import keyboards as K
from telegram import states
from assets.catalog import NETWORKS, get_network, networks_with, tokens_for, token_by_symbol
from assets.discovery import discover_token_contracts
from assets.capabilities import Family
from wallets import service as W
from wallets import tracking as TR
from wallets.portfolio import get_portfolio
from wallets.import_service import classify_secret
from transactions import service as TX
from transactions import state_machine as SM
from chains.evm import evm_adapter
from chains.solana import solana_adapter
from utils.qr import make_qr_png
from utils.format import shorten_address, fmt_amount, fmt_usd, rel_time
from intents.near_intents import client as intents_client, NearIntentsError
from services import email_service
import database as dbm

logger = logging.getLogger("oxael.handlers")

DIV = "━━━━━━━━━━━━━━━━━━"
HEAD = f"⬡  <b>{BRAND_NAME}</b>"


def esc(s: str) -> str:
    return html.escape(s or "")


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ---------------- low-level send/edit ----------------
async def _send(chat_id: int, text: str, markup: dict | None = None):
    return await bot.send_message(chat_id, text, markup)


async def _edit(chat_id: int, mid: int, text: str, markup: dict | None = None):
    return await bot.edit_message_text(chat_id, mid, text, markup)


async def _screen(chat_id: int, mid: int | None, text: str, markup: dict | None = None):
    """Edit the active message if mid is given, else send a fresh one."""
    if mid:
        return await _edit(chat_id, mid, text, markup)
    return await _send(chat_id, text, markup)


# =================================================================
# ENTRY POINT
# =================================================================
async def process_update(update: dict) -> None:
    try:
        if "message" in update:
            await _on_message(update["message"])
        elif "callback_query" in update:
            await _on_callback(update["callback_query"])
    except Exception:  # noqa: BLE001
        logger.exception("error processing update")


# =================================================================
# MESSAGES
# =================================================================
async def _on_message(msg: dict) -> None:
    chat_id = msg["chat"]["id"]
    frm = msg.get("from", {})
    uid = frm.get("id")
    text = (msg.get("text") or "").strip()
    if uid is None:
        return
    await W.get_or_create_user(uid, frm.get("username"))

    if text.startswith("/start"):
        await states.clear(uid)
        await _show_start(chat_id, uid)
        return
    if text.startswith("/help"):
        await _send(chat_id, _help_text(), K.back())
        return
    if text.startswith("/wallets"):
        await _cmd_wallets(chat_id, uid, None)
        return
    if text.startswith("/send"):
        await states.clear(uid); await _send_pick_network(chat_id, uid, None)
        return
    if text.startswith("/receive"):
        await states.clear(uid); await _recv_pick_network(chat_id, uid, None, None)
        return
    if text.startswith("/swap"):
        await states.clear(uid); await _swap_intro(chat_id, uid, None)
        return
    if text.startswith("/history"):
        await states.clear(uid); await _show_history(chat_id, uid, None, None, 0)
        return
    if text.startswith("/settings"):
        await states.clear(uid); await _show_settings(chat_id, uid, None)
        return
    if text.startswith("/wallet") and not text.startswith("/wallets"):
        await states.clear(uid); await _show_home(chat_id, uid, None)
        return
    if text.startswith("/balance"):
        await states.clear(uid); await _show_home(chat_id, uid, None)
        return
    if text.startswith("/create"):
        await states.clear(uid); await _prompt_create_name(chat_id, uid, None)
        return
    if text.startswith("/import"):
        await states.clear(uid); await _prompt_import_name(chat_id, uid, None)
        return
    if text.startswith("/track"):
        await states.clear(uid); await _show_track(chat_id, uid, None)
        return
    if text.startswith("/tokens"):
        await states.clear(uid); await _show_tokens(chat_id, uid, None)
        return

    # flow input
    sess = await states.get(uid)
    state = (sess or {}).get("state")
    data = (sess or {}).get("data", {})
    if state == "import_await_secret":
        await _flow_import_secret(chat_id, uid, msg, text)
    elif state == "import_await_name":
        await _flow_import_name(chat_id, uid, text)
    elif state == "create_await_name":
        await _flow_create_name(chat_id, uid, text)
    elif state == "send_await_address":
        await _flow_send_address(chat_id, uid, data, text)
    elif state == "send_await_amount":
        await _flow_send_amount(chat_id, uid, data, text)
    elif state == "rename_await_name":
        await _flow_rename(chat_id, uid, data, text)
    elif state == "swap_await_amount":
        await _flow_swap_amount(chat_id, uid, data, text)
    elif state == "email_await_address":
        await _flow_email(chat_id, uid, text)
    elif state == "ab_await_label":
        await _flow_ab_label(chat_id, uid, data, text)
    elif state == "ab_await_address":
        await _flow_ab_address(chat_id, uid, data, text)
    elif state == "track_await_address":
        await _flow_track_add(chat_id, uid, text)
    else:
        await _show_start(chat_id, uid)


# =================================================================
# CALLBACKS
# =================================================================
async def _on_callback(cq: dict) -> None:
    data = cq.get("data", "")
    cq_id = cq["id"]
    msg = cq.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    mid = msg.get("message_id")
    uid = cq.get("from", {}).get("id")
    await bot.answer_callback_query(cq_id)
    if chat_id is None or uid is None:
        return
    await W.get_or_create_user(uid, cq.get("from", {}).get("username"))

    parts = data.split("|")
    head = parts[0]

    routes = {
        "home": lambda: _show_home(chat_id, uid, mid),
        "seconboard": lambda: _edit(chat_id, mid, _security_onboard_text(), K.security_ack()),
        "onbcreate": lambda: _edit(chat_id, mid, _security_onboard_text(), K.security_ack()),
        "create": lambda: _prompt_create_name(chat_id, uid, mid),
        "createskip": lambda: _do_create(chat_id, uid, mid, None),
        "import": lambda: _prompt_import_name(chat_id, uid, mid),
        "importskip": lambda: _prompt_import(chat_id, uid, mid, None),
        "wallets": lambda: _cmd_wallets(chat_id, uid, mid),
        "send": lambda: _send_pick_network(chat_id, uid, mid),
        "recv": lambda: _recv_pick_network(chat_id, uid, mid, None),
        "swap": lambda: _swap_intro(chat_id, uid, mid),
        "hist": lambda: _show_history(chat_id, uid, mid, None, 0),
        "tokens": lambda: _show_tokens(chat_id, uid, mid),
        "track": lambda: _show_track(chat_id, uid, mid),
        "settings": lambda: _show_settings(chat_id, uid, mid),
        "security": lambda: _edit(chat_id, mid, _security_text(), K.back("settings")),
        "networks": lambda: _show_networks(chat_id, mid),
        "help": lambda: _edit(chat_id, mid, _help_text(), K.back("settings")),
        "email": lambda: _show_email(chat_id, uid, mid),
        "emailadd": lambda: _prompt_email(chat_id, uid, mid),
        "emailrm": lambda: _remove_email(chat_id, uid, mid),
        "abook": lambda: _show_abook(chat_id, uid, mid),
        "abadd": lambda: _ab_pick_network(chat_id, uid, mid),
    }
    # Only dispatch the no-arg route when the callback has no parameters.
    # Heads like "send", "recv", "hist" also appear as parameterised callbacks
    # (e.g. "send|net|..."); those must fall through to the handlers below.
    if head in routes and len(parts) == 1:
        await routes[head]()
        return

    # parameterised
    if head == "w":
        await _wallet_detail(chat_id, uid, mid, parts[1])
    elif head == "wren":
        await _prompt_rename(chat_id, uid, mid, parts[1])
    elif head == "wprim":
        await W.set_primary(uid, parts[1]); await _wallet_detail(chat_id, uid, mid, parts[1])
    elif head == "wdel":
        await _edit(chat_id, mid, "🗑 <b>Delete wallet?</b>\n\nThis removes the wallet from OXAEL. If you have no backup of its keys, funds become unrecoverable.", K.confirm_delete(parts[1]))
    elif head == "wdelok":
        await W.delete_wallet(uid, parts[1]); await _cmd_wallets(chat_id, uid, mid)
    elif head == "send" and parts[1] == "net":
        await _send_set_network(chat_id, uid, parts[2], parts[3])
    elif head == "sendasset":
        await _send_set_asset(chat_id, uid, parts[1], parts[2], parts[3])
    elif head == "sendc":
        await _send_set_token_contract(chat_id, uid, parts[1], parts[2], parts[3])
    elif head == "recv" and parts[1] == "w":
        await _recv_pick_network(chat_id, uid, mid, parts[2])
    elif head == "recv" and parts[1] == "net":
        await _recv_show(chat_id, uid, parts[2], parts[3])
    elif head == "sendgo":
        await _send_execute(chat_id, uid, mid, parts[1])
    elif head == "sendx":
        await TX.cancel(uid, parts[1]); await _show_home(chat_id, uid, mid)
    elif head == "hist" and parts[1] == "w":
        await _show_history(chat_id, uid, mid, parts[2], 0)
    elif head == "histp":
        await _show_history(chat_id, uid, mid, parts[1] or None, int(parts[2]))
    elif head == "swfrom" and parts[1] == "net":
        await _swap_pick_source_asset(chat_id, uid, mid, parts[2], parts[3])
    elif head == "swasset":
        await _swap_set_source_asset(chat_id, uid, mid, parts[1], parts[2], parts[3])
    elif head == "swto":
        await _swap_prompt_amount(chat_id, uid, mid, parts[1])
    elif head == "swgo":
        await _swap_create(chat_id, uid, mid, parts[1])
    elif head == "swpay":
        await _swap_pay(chat_id, uid, mid, parts[1])
    elif head == "swstat":
        await _swap_status(chat_id, uid, mid, parts[1])
    elif head == "abdel":
        await dbm.address_book.delete_one({"telegram_user_id": uid, "entry_id": parts[1]})
        await _show_abook(chat_id, uid, mid)
    elif head == "trk":
        await W.set_tracking(uid, parts[1], parts[2] == "on")
        await _show_track(chat_id, uid, mid)
    elif head == "trackdep":
        await _show_track_deposits(chat_id, uid, mid, int(parts[1]) if len(parts) > 1 else 0)
    elif head == "trackadd":
        await _prompt_track_add(chat_id, uid, mid)
    elif head == "trackrm":
        await TR.remove_tracked(uid, parts[1])
        await _show_track(chat_id, uid, mid)
    elif head == "ab" and parts[1] == "net":
        await _ab_set_network(chat_id, uid, parts[2])
    else:
        await _show_home(chat_id, uid, mid)


# =================================================================
# START / HOME
# =================================================================
async def _show_start(chat_id: int, uid: int) -> None:
    wallets = await W.list_wallets(uid)
    if not wallets:
        text = (
            f"{HEAD}\n<code>{BRAND_TAGLINE.lower()}</code>\n\n"
            "A crypto wallet that lives inside Telegram.\n\n"
            "◈  Multi-network — every major EVM chain &amp; Solana\n"
            "◈  Cross-chain swaps via NEAR Intents\n"
            "◈  Fast, private, self-contained\n\n"
            "Create a new wallet or import an existing one to begin."
        )
        await _send(chat_id, text, K.onboarding())
    else:
        await _show_home(chat_id, uid, None)


async def _show_home(chat_id: int, uid: int, mid: int | None) -> None:
    wallet = await W.get_primary_wallet(uid)
    if not wallet:
        await _show_start(chat_id, uid)
        return
    loading = f"{HEAD}\n{DIV}\n💼 <b>{esc(wallet['name'])}</b>\n\n⟳ Loading balances…"
    if mid:
        await _edit(chat_id, mid, loading, None)
        target_mid = mid
    else:
        res = await _send(chat_id, loading, None)
        target_mid = (res or {}).get("result", {}).get("message_id")

    balances, total, statuses = await get_portfolio(wallet)
    lines = [f"{HEAD}\n{DIV}", f"💼 <b>{esc(wallet['name'])}</b>", ""]
    if total is not None:
        lines.append(f"💰 <b>Portfolio</b>\n<b>{fmt_usd(total)}</b>\n")
    else:
        lines.append("💰 <b>Portfolio</b>\n<b>—</b>  <i>pricing unavailable</i>\n")
    if not balances:
        lines.append("No assets yet.\nTap ↓ <b>Receive</b> to fund this wallet.")
    else:
        for b in balances[:10]:
            net = NETWORKS.get(b.network)
            usd = f"  ≈ {fmt_usd(b.usd_value)}" if b.usd_value is not None else "  ·  <i>no price</i>"
            sym = esc(b.symbol) + ("" if b.verified else " ⚠️")
            lines.append(f"<b>{fmt_amount(b.amount)} {sym}</b>{usd}\n<i>{net.name if net else b.network}</i>")
        extra = len(balances) - 10
        if extra > 0:
            lines.append(f"\n<i>+{extra} more — tap 🪙 Tokens</i>")
    down = [NETWORKS[k].name for k, v in statuses.items() if v == "unavailable" and k in NETWORKS]
    if down:
        lines.append(f"\n⚠️ <i>Temporarily unavailable: {', '.join(down)}</i>")
    if wallet["addresses"].get("evm"):
        lines.append(f"\n{DIV}\nEVM  <code>{shorten_address(wallet['addresses']['evm'])}</code>")
    text = "\n".join(lines)
    if target_mid:
        await _edit(chat_id, target_mid, text, K.main_menu())
    else:
        await _send(chat_id, text, K.main_menu())


async def _show_tokens(chat_id: int, uid: int, mid: int | None) -> None:
    wallet = await W.get_primary_wallet(uid)
    if not wallet:
        await _show_start(chat_id, uid); return
    if mid:
        await _edit(chat_id, mid, "🪙 <b>Tokens</b>\n\n⏳ Discovering your tokens…", None)
    balances, total, statuses = await get_portfolio(wallet)
    tokens = [b for b in balances if b.token_address]
    lines = [f"🪙 <b>Tokens</b>\n{DIV}", f"💼 <b>{esc(wallet['name'])}</b>\n"]
    if not tokens:
        lines.append("No ERC-20 tokens held on the supported networks yet.")
    else:
        for b in tokens[:20]:
            net = NETWORKS.get(b.network)
            title = esc(b.name or b.symbol)
            badge = "✓" if b.verified else "⚠️ unverified"
            if b.usd_value is not None:
                val = f"{fmt_amount(b.amount)} {esc(b.symbol)}  ≈ {fmt_usd(b.usd_value)}"
            else:
                val = f"{fmt_amount(b.amount)} {esc(b.symbol)}  ·  <i>price unavailable</i>"
            addr_line = f"\n<code>{shorten_address(b.token_address, 6, 4)}</code>" if (b.name or b.symbol) == "Unknown" else ""
            lines.append(f"<b>{title}</b>  <i>{badge}</i>\n{val}\n<i>{net.name if net else b.network}</i>{addr_line}")
    down = [NETWORKS[k].name for k, v in statuses.items() if v == "unavailable" and k in NETWORKS]
    if down:
        lines.append(f"\n⚠️ <i>Temporarily unavailable: {', '.join(down)}</i>")
    rows = [[K.btn("↻ Refresh", "tokens")], [K.btn("🏠 Home", "home")]]
    await _screen(chat_id, mid, "\n".join(lines), K.kb(rows))


# =================================================================
# CREATE / IMPORT
# =================================================================
async def _prompt_create_name(chat_id: int, uid: int, mid: int) -> None:
    await states.set_state(uid, chat_id, "create_await_name", {})
    await _screen(chat_id, mid, "✦ <b>Name your wallet</b>\n\nSend a name (e.g. “Main”, “Trading”), or skip to use a default.",
                  K.kb([[K.btn("Skip", "createskip")], [K.btn("‹ Back", "home")]]))


async def _flow_create_name(chat_id: int, uid: int, text: str) -> None:
    name = text[:40].strip()
    await states.clear(uid)
    await _do_create(chat_id, uid, None, name or None)


async def _do_create(chat_id: int, uid: int, mid: int | None, name: str | None) -> None:
    await _screen(chat_id, mid, "✦ <b>Creating your wallet…</b>\n\nGenerating secure keys for EVM &amp; Solana.", None)
    count = len(await W.list_wallets(uid))
    if not name:
        name = "Main Wallet" if count == 0 else f"Wallet {count + 1}"
    w = await W.create_wallet(uid, name)
    await W.mark_onboarded(uid)
    text = (
        f"{HEAD}\n{DIV}\n✅ <b>Your wallet is ready.</b>\n\n"
        f"<b>{esc(w['name'])}</b>\n\n"
        f"EVM  <code>{w['addresses'].get('evm','')}</code>\n"
        f"SOL  <code>{shorten_address(w['addresses'].get('solana',''))}</code>\n\n"
        "The same EVM address works across every EVM network. Fund it, then send, receive or swap."
    )
    await _send(chat_id, text, K.kb([[K.btn("Open wallet →", "home")]]))


async def _prompt_import_name(chat_id: int, uid: int, mid: int) -> None:
    await states.set_state(uid, chat_id, "import_await_name", {})
    await _screen(chat_id, mid, "↗ <b>Import Wallet</b>\n\nFirst, send a name for this wallet, or skip to use a default.",
                  K.kb([[K.btn("Skip", "importskip")], [K.btn("‹ Back", "home")]]))


async def _flow_import_name(chat_id: int, uid: int, text: str) -> None:
    name = text[:40].strip()
    await _prompt_import(chat_id, uid, None, name or None)


async def _prompt_import(chat_id: int, uid: int, mid: int | None, name: str | None) -> None:
    await states.set_state(uid, chat_id, "import_await_secret", {"name": name})
    text = (
        "↗ <b>Import Wallet</b>\n\n"
        "Send one of the following in a single message:\n\n"
        "◈  An EVM private key (0x…64 hex)\n"
        "◈  A 12/24-word seed phrase\n"
        "◈  A Solana keypair (base58)\n\n"
        "🔒 Your secret is encrypted immediately and never stored or shown in plaintext. "
        "Delete your message afterwards for privacy."
    )
    await _screen(chat_id, mid, text, K.back("home"))


async def _find_existing(uid: int, secret: str) -> dict | None:
    """Derive the address from a submitted secret and return the wallet already
    holding it (for the duplicate-wallet message). Never logs the secret."""
    for adapter in (evm_adapter, solana_adapter):
        try:
            keys = adapter.from_secret(secret)
        except ValueError:
            continue
        found = await W.find_wallet_by_address(uid, keys.address)
        keys.secret = ""
        if found:
            return found
    return None


async def _flow_import_secret(chat_id: int, uid: int, msg: dict, text: str) -> None:
    sess = await states.get(uid)
    chosen_name = ((sess or {}).get("data") or {}).get("name")
    kind = classify_secret(text)
    if kind == "unknown":
        await _send(chat_id, "That doesn't look like a supported secret. Send an EVM private key, a seed phrase, or a Solana keypair.", K.back("home"))
        return
    await states.clear(uid)
    try:
        count = len(await W.list_wallets(uid))
        name = chosen_name or ("Imported Wallet" if count == 0 else f"Imported {count + 1}")
        w = await W.import_wallet(uid, name, text)
    except ValueError as e:
        if str(e) == "already_added":
            existing = await _find_existing(uid, text)
            name = esc(existing["name"]) if existing else "this wallet"
            fam = existing["families"][0] if existing else "evm"
            addr = existing["addresses"][fam] if existing else ""
            msg = (
                "⚠️ <b>Wallet already added</b>\n\n"
                "This wallet is already in your wallet list.\n\n"
                f"Wallet: <b>{name}</b>\n"
                f"Address: <code>{shorten_address(addr, 8, 6)}</code>"
            )
            await _send(chat_id, msg, K.kb([
                [K.btn("👛 View Wallets", "wallets")],
                [K.btn("✖ Cancel", "home")],
            ]))
            return
        await _send(chat_id, f"⚠️ {esc(str(e))}", K.back("home"))
        return
    await W.mark_onboarded(uid)
    fam = w["families"][0]
    addr = w["addresses"][fam]
    text_out = (
        f"{HEAD}\n{DIV}\n✅ <b>Wallet imported.</b>\n\n"
        f"<b>{esc(w['name'])}</b>\n{fam.upper()}  <code>{addr}</code>\n\n"
        "For your privacy, delete the message that contained your secret."
    )
    await _send(chat_id, text_out, K.kb([[K.btn("Open wallet →", "home")]]))


# =================================================================
# WALLETS
# =================================================================
async def _cmd_wallets(chat_id: int, uid: int, mid: int | None) -> None:
    wallets = await W.list_wallets(uid)
    if not wallets:
        await _show_start(chat_id, uid)
        return
    text = f"👛 <b>Your Wallets</b>\n{DIV}\n\nSelect a wallet to manage it, or add a new one."
    if mid:
        await _edit(chat_id, mid, text, K.wallets_menu(wallets))
    else:
        await _send(chat_id, text, K.wallets_menu(wallets))


async def _wallet_detail(chat_id: int, uid: int, mid: int, wallet_id: str) -> None:
    w = await W.get_wallet(uid, wallet_id)
    if not w:
        await _cmd_wallets(chat_id, uid, mid)
        return
    lines = [f"👛 <b>{esc(w['name'])}</b>", f"{'★ primary · ' if w['is_primary'] else ''}{w['source']}", ""]
    for fam, addr in w["addresses"].items():
        lines.append(f"{fam.upper()}  <code>{addr}</code>")
    await _edit(chat_id, mid, "\n".join(lines), K.wallet_actions(wallet_id, w["is_primary"]))


async def _prompt_rename(chat_id: int, uid: int, mid: int, wallet_id: str) -> None:
    await states.set_state(uid, chat_id, "rename_await_name", {"wallet_id": wallet_id})
    await _edit(chat_id, mid, "✏️ <b>Rename wallet</b>\n\nSend the new name.", K.back(f"w|{wallet_id}"))


async def _flow_rename(chat_id: int, uid: int, data: dict, text: str) -> None:
    name = text[:40].strip()
    await states.clear(uid)
    if name:
        await W.rename_wallet(uid, data["wallet_id"], name)
    await _send(chat_id, f"✅ Renamed to <b>{esc(name)}</b>.", K.kb([[K.btn("‹ Wallets", "wallets")]]))


# =================================================================
# SEND
# =================================================================
async def _send_pick_network(chat_id: int, uid: int, mid: int | None) -> None:
    w = await W.get_primary_wallet(uid)
    if not w:
        await _show_start(chat_id, uid); return
    text = f"📤 <b>Send</b>\n{DIV}\nFrom <b>{esc(w['name'])}</b>\n\nSelect a network."
    kb = K.networks_for("send", "direct_send_supported", w["wallet_id"], {Family.EVM, Family.SOLANA})
    await _screen(chat_id, mid, text, kb)


async def _send_set_network(chat_id: int, uid: int, wallet_id: str, netkey: str) -> None:
    net = get_network(netkey)
    fam = net.family.value
    addr = await W.get_address(uid, wallet_id, fam)
    if not addr:
        await _send(chat_id, "This wallet has no account for that network.", K.back("home")); return
    if fam == "evm":
        # let the user pick native, a curated token, or any discovered token held
        rows = [[K.btn(f"{net.symbol} (native)", f"sendasset|{wallet_id}|{netkey}|__native__")]]
        catalog_syms = set()
        catalog_addrs = set()
        for t in tokens_for(netkey):
            rows.append([K.btn(t["symbol"], f"sendasset|{wallet_id}|{netkey}|{t['symbol']}")])
            catalog_syms.add(t["symbol"].upper())
            catalog_addrs.add(t["address"].lower())
        # discovered held tokens (verified on-chain), not already in the catalog
        try:
            for tok in await discover_token_contracts(netkey, addr):
                if tok["address"].lower() in catalog_addrs:
                    continue
                bal = await evm_adapter.token_balance(netkey, tok, addr)
                if bal > 0:
                    label = f"{tok.get('symbol') or 'Token'} ({fmt_amount(bal)})"
                    rows.append([K.btn(label[:32], f"sendc|{wallet_id}|{netkey}|{tok['address']}")])
                if len(rows) >= 12:
                    break
        except Exception:  # noqa: BLE001
            pass
        rows.append([K.btn("‹ Back", "send")])
        await _send(chat_id, f"📤 <b>Send</b> · {net.name}\n\nWhich asset?", K.kb(rows))
    else:
        await _begin_send_address(chat_id, uid, wallet_id, netkey, fam, net.symbol, None, net.decimals, addr)


async def _send_set_asset(chat_id: int, uid: int, wallet_id: str, netkey: str, symbol: str) -> None:
    net = get_network(netkey)
    fam = net.family.value
    addr = await W.get_address(uid, wallet_id, fam)
    if not addr:
        await _send(chat_id, "This wallet has no account for that network.", K.back("home")); return
    if symbol == "__native__":
        await _begin_send_address(chat_id, uid, wallet_id, netkey, fam, net.symbol, None, net.decimals, addr)
    else:
        tok = token_by_symbol(netkey, symbol)
        if not tok:
            await _send(chat_id, "Unsupported asset.", K.back("home")); return
        await _begin_send_address(chat_id, uid, wallet_id, netkey, fam, tok["symbol"], tok["address"], tok["decimals"], addr)


async def _send_set_token_contract(chat_id: int, uid: int, wallet_id: str, netkey: str, contract: str) -> None:
    """Begin a send for a discovered token identified by its contract address.
    Metadata (symbol/decimals) is read on-chain (never assumed)."""
    net = get_network(netkey)
    fam = net.family.value
    addr = await W.get_address(uid, wallet_id, fam)
    if not addr:
        await _send(chat_id, "This wallet has no account for that network.", K.back("home")); return
    try:
        meta = await evm_adapter.token_metadata(netkey, contract)
    except Exception:  # noqa: BLE001
        await _send(chat_id, "⚠️ Couldn't read that token's details right now. Try again.", K.back("home")); return
    symbol = meta.get("symbol") or "TOKEN"
    decimals = int(meta.get("decimals", 18))
    await _begin_send_address(chat_id, uid, wallet_id, netkey, fam, symbol, meta["address"], decimals, addr)


async def _begin_send_address(chat_id, uid, wallet_id, netkey, fam, symbol, token_address, decimals, addr) -> None:
    net = get_network(netkey)
    await states.set_state(uid, chat_id, "send_await_address", {
        "wallet_id": wallet_id, "network": netkey, "family": fam, "asset": symbol,
        "token_address": token_address, "decimals": decimals, "from": addr,
    })
    await _send(chat_id, f"📤 <b>Send {symbol}</b> · {net.name}\n\nPaste the destination address.", K.back("home"))


async def _flow_send_address(chat_id: int, uid: int, data: dict, text: str) -> None:
    to = text.strip()
    fam = data["family"]
    ok = evm_adapter.validate_address(to) if fam == "evm" else solana_adapter.validate_address(to)
    if not ok:
        await _send(chat_id, "That address doesn't match the selected network. Try again.")
        return
    data["to"] = to
    await states.set_state(uid, chat_id, "send_await_amount", data)
    net = get_network(data["network"])
    sym = data["asset"]
    bal = await _asset_balance(data)
    await _send(chat_id, f"Amount to send in <b>{sym}</b>\n\nAvailable: <b>{fmt_amount(bal)} {sym}</b>", None)


async def _asset_balance(data: dict) -> Decimal:
    try:
        if data.get("token_address"):
            tok = {"address": data["token_address"], "decimals": data["decimals"]}
            return await evm_adapter.token_balance(data["network"], tok, data["from"])
        return await _native_balance(data["family"], data["network"], data["from"])
    except Exception:  # noqa: BLE001
        return Decimal(0)


async def _flow_send_amount(chat_id: int, uid: int, data: dict, text: str) -> None:
    try:
        amount = Decimal(text.strip())
        if amount <= 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        await _send(chat_id, "Enter a valid amount, e.g. 0.05")
        return
    fam = data["family"]
    net = get_network(data["network"])
    sym = data["asset"]
    is_token = bool(data.get("token_address"))
    if fam == "evm":
        fee = (await evm_adapter.estimate_token_fee(data["network"]) if is_token
               else await evm_adapter.estimate_native_fee(data["network"])).fee_native
        native_bal = await _native_balance(fam, data["network"], data["from"])
    else:
        fee = (await solana_adapter.estimate_native_fee()).fee_native
        native_bal = await _native_balance(fam, data["network"], data["from"])

    asset_bal = await _asset_balance(data)
    if is_token:
        if amount > asset_bal:
            await states.clear(uid)
            await _send(chat_id, f"⚠️ <b>Insufficient {sym}</b>\n\n"
                                 f"You have: <b>{fmt_amount(asset_bal)} {sym}</b>\n"
                                 f"You tried to send: <b>{fmt_amount(amount)} {sym}</b>", K.back("home")); return
        if fee > native_bal:
            await states.clear(uid)
            await _send(chat_id, f"⚠️ <b>Insufficient gas</b>\n\n"
                                 f"You have: <b>{fmt_amount(native_bal)} {net.symbol}</b>\n"
                                 f"Estimated required: <b>{fmt_amount(fee)} {net.symbol}</b>\n\n"
                                 f"Add {net.symbol} to this wallet to cover the network fee for sending {sym}.",
                        K.back("home")); return
    else:
        if amount + fee > asset_bal:
            await states.clear(uid)
            await _send(chat_id, f"⚠️ <b>Insufficient balance</b>\n\n"
                                 f"You have: <b>{fmt_amount(asset_bal)} {net.symbol}</b>\n"
                                 f"Needed (amount + fee): <b>{fmt_amount(amount + fee)} {net.symbol}</b>\n"
                                 f"Estimated network fee: ~{fmt_amount(fee)} {net.symbol}", K.back("home")); return

    session_nonce = uuid.uuid4().hex
    tx = await TX.create_pending_send(
        uid, data["wallet_id"], data["network"], sym, data["to"], str(amount),
        fam, data["from"], session_nonce, data.get("token_address"), int(data.get("decimals", 18)),
    )
    await states.clear(uid)
    text_out = (
        f"{DIV}\n       <b>CONFIRM SEND</b>\n{DIV}\n\n"
        f"Asset      <b>{sym}</b>\n"
        f"Network    {net.name}\n"
        f"Amount     <b>{fmt_amount(amount)}</b>\n"
        f"To         <code>{shorten_address(data['to'], 8, 6)}</code>\n"
        f"Network fee ~{fmt_amount(fee)} {net.symbol}\n\n"
        "This action is irreversible."
    )
    await _send(chat_id, text_out, K.send_review(tx["tx_id"]))


async def _send_execute(chat_id: int, uid: int, mid: int, tx_id: str) -> None:
    await _edit(chat_id, mid, "⚡ <b>Broadcasting…</b>\nSigning and submitting your transaction.", None)
    tx = await TX.confirm_and_broadcast(uid, tx_id)
    if tx.get("state") == SM.BROADCASTED:
        net = get_network(tx["network"])
        text = (
            f"✅ <b>Transaction submitted.</b>\n\n"
            f"{fmt_amount(Decimal(tx['amount']))} {tx['asset']} · {net.name}\n"
            f"<code>{shorten_address(tx.get('tx_hash',''), 10, 8)}</code>\n\n"
            "Your transaction is on the network. We're watching it."
        )
        await _edit(chat_id, mid, text, K.result_actions(tx.get("explorer_url")))
    else:
        err = esc(tx.get("error") or "The transaction could not be broadcast.")
        await _edit(chat_id, mid, f"⚠️ <b>Send failed.</b>\n\n{err}", K.back("home"))


async def _native_balance(fam: str, network: str, address: str) -> Decimal:
    try:
        if fam == "evm":
            return await evm_adapter.native_balance(network, address)
        return await solana_adapter.native_balance(address)
    except Exception:  # noqa: BLE001
        return Decimal(0)


# =================================================================
# RECEIVE
# =================================================================
async def _recv_pick_network(chat_id: int, uid: int, mid: int, wallet_id: str | None) -> None:
    if not wallet_id:
        w = await W.get_primary_wallet(uid)
        if not w:
            await _show_start(chat_id, uid); return
        wallet_id = w["wallet_id"]
    text = f"📥 <b>Receive</b>\n{DIV}\nSelect the network to receive on."
    kb = K.networks_for("recv", "receive_supported", wallet_id, {Family.EVM, Family.SOLANA})
    await _screen(chat_id, mid, text, kb)


async def _recv_show(chat_id: int, uid: int, wallet_id: str, netkey: str) -> None:
    net = get_network(netkey)
    fam = net.family.value
    addr = await W.get_address(uid, wallet_id, fam)
    if not addr:
        await _send(chat_id, "No account for that network.", K.back("home")); return
    caption = (
        f"📥 <b>Receive {net.symbol}</b> · {net.name}\n\n"
        f"<code>{addr}</code>\n\n"
        f"⚠️ Send only <b>{net.name}</b> assets to this address. "
        "Assets from other networks may be lost."
    )
    png = make_qr_png(addr)
    markup = K.result_actions(net.explorer_address(addr))
    await bot.send_photo(chat_id, png, caption, markup)


# =================================================================
# HISTORY
# =================================================================
async def _show_history(chat_id: int, uid: int, mid: int, wallet_id: str | None, skip: int) -> None:
    limit = 6
    items = await TX.history(uid, wallet_id, limit, skip)
    total = await TX.history_count(uid, wallet_id)
    lines = [f"📜 <b>History</b>\n{DIV}"]
    if not items:
        lines.append("\nNo transactions yet.\nYour sends, receives and swaps will appear here.")
    else:
        for t in items:
            net = NETWORKS.get(t["network"])
            arrow = "🔴 Sent" if t["direction"] == "send" else ("🟢 Received" if t["direction"] == "receive" else "🔄 Swap")
            sign = "-" if t["direction"] == "send" else "+"
            status = _state_label(t["state"])
            when = rel_time(t.get("created_at", ""))
            line = f"\n{arrow}  <b>{sign}{fmt_amount(Decimal(t['amount']))} {t['asset']}</b>\n{net.name if net else t['network']} · {when} · {status}"
            if t.get("explorer_url"):
                line += f"\n<a href=\"{t['explorer_url']}\">view on explorer ↗</a>"
            lines.append(line)
    rows = []
    nav = []
    if skip > 0:
        nav.append(K.btn("‹ Prev", f"histp|{wallet_id or ''}|{max(0, skip - limit)}"))
    if skip + limit < total:
        nav.append(K.btn("Next ›", f"histp|{wallet_id or ''}|{skip + limit}"))
    if nav:
        rows.append(nav)
    if wallet_id:
        rows.append([K.btn("‹ Back", f"w|{wallet_id}"), K.btn("🏠 Home", "home")])
    else:
        rows.append([K.btn("🏠 Home", "home")])
    await _screen(chat_id, mid, "\n".join(lines), K.kb(rows))


def _state_label(state: str) -> str:
    return {
        SM.CONFIRMED: "Confirmed", SM.BROADCASTED: "Broadcast", SM.PENDING: "Pending",
        SM.FAILED: "Failed", SM.CANCELLED: "Cancelled", SM.EXPIRED: "Expired",
        SM.AWAITING_CONFIRMATION: "Awaiting", SM.SIGNING: "Signing", SM.BROADCASTING: "Broadcasting",
    }.get(state, state)


# =================================================================
# TRACK — wallet activity / incoming crypto notifications
# =================================================================
async def _show_track(chat_id: int, uid: int, mid: int | None) -> None:
    wallets = await W.list_wallets(uid)
    if not wallets:
        await _show_start(chat_id, uid); return
    deps = await TX.deposits(uid, limit=6)
    lines = [
        f"🔔 <b>Track</b>\n{DIV}",
        "\nOXAEL watches your wallets on every supported chain and alerts you the "
        "moment a <b>deposit</b> arrives — native coins or tokens, from any chain.\n",
    ]
    if deps:
        lines.append("<b>Recent deposits</b>")
        for d in deps:
            net = NETWORKS.get(d["network"])
            when = _rel_time(d.get("created_at"))
            frm = d.get("from_address")
            frm_line = f" · from <code>{shorten_address(frm, 6, 4)}</code>" if frm else ""
            lines.append(f"🟢 +{fmt_amount(Decimal(d['amount']))} {esc(d['asset'])} · "
                         f"{net.name if net else d['network']} · {when}{frm_line}")
        lines.append("")
    else:
        lines.append("<i>No deposits detected yet — they'll appear here automatically.</i>\n")
    lines.append("<b>Tracked wallets</b>")
    rows = []
    for w in wallets:
        on = w.get("track_enabled", True)
        lines.append(f"{'🔔' if on else '🔕'} <b>{esc(w['name'])}</b> — {'tracking' if on else 'paused'}")
        toggle = "off" if on else "on"
        label = f"🔕 Pause {w['name'][:14]}" if on else f"🔔 Track {w['name'][:14]}"
        rows.append([K.btn(label, f"trk|{w['wallet_id']}|{toggle}")])
    external = await TR.list_tracked(uid)
    if external:
        lines.append("\n<b>Watched addresses</b>")
        for e in external:
            net_lbl = "EVM" if e["family"] == "evm" else "Solana"
            lines.append(f"👁 <b>{esc(e['label'])}</b> · {net_lbl}\n<code>{shorten_address(e['address'])}</code>")
            rows.append([K.btn(f"🗑 {e['label'][:16]}", f"trackrm|{e['tracked_id']}")])
    rows.append([K.btn("➕ Track an address", "trackadd")])
    rows.append([K.btn("📥 All deposits", "trackdep|0"), K.btn("↻ Refresh", "track")])
    rows.append([K.btn("🏠 Home", "home")])
    await _screen(chat_id, mid, "\n".join(lines), K.kb(rows))


async def _prompt_track_add(chat_id: int, uid: int, mid: int | None) -> None:
    await states.set_state(uid, chat_id, "track_await_address", {})
    await _screen(chat_id, mid,
        "👁 <b>Track an address</b>\n\n"
        "Paste any <b>EVM (0x…)</b> or <b>Solana</b> address you want to watch. "
        "You'll be notified whenever it receives a deposit.\n\n"
        "Optionally add a label after a space, e.g.\n"
        "<code>0xabc… Exchange cold wallet</code>",
        K.back("track"))


async def _flow_track_add(chat_id: int, uid: int, text: str) -> None:
    await states.clear(uid)
    parts = text.strip().split(None, 1)
    address = parts[0]
    label = parts[1] if len(parts) > 1 else None
    try:
        entry = await TR.add_tracked(uid, address, label)
    except ValueError as e:
        if str(e) == "already_tracked":
            await _send(chat_id, "👁 You're already tracking that address.",
                        K.kb([[K.btn("‹ Back to Track", "track")]]))
            return
        await _send(chat_id, f"⚠️ {esc(str(e))}", K.back("track"))
        return
    net_lbl = "EVM" if entry["family"] == "evm" else "Solana"
    await _send(chat_id,
        f"✅ <b>Now tracking</b>\n\n<b>{esc(entry['label'])}</b> · {net_lbl}\n"
        f"<code>{shorten_address(entry['address'], 8, 6)}</code>\n\n"
        "You'll get a deposit alert the moment crypto arrives.",
        K.kb([[K.btn("👁 Open Track", "track")], [K.btn("🏠 Home", "home")]]))


async def _show_track_deposits(chat_id: int, uid: int, mid: int | None, skip: int) -> None:
    PAGE = 8
    total = await TX.deposits_count(uid)
    deps = await TX.deposits(uid, limit=PAGE, skip=skip)
    lines = [f"📥 <b>Deposits</b>\n{DIV}"]
    if not deps:
        lines.append("\nNo deposits detected yet.")
    else:
        for d in deps:
            net = NETWORKS.get(d["network"])
            when = _rel_time(d.get("created_at"))
            frm = d.get("from_address")
            frm_line = f"\nfrom <code>{shorten_address(frm, 8, 6)}</code>" if frm else ""
            link = f'  <a href="{d["explorer_url"]}">↗</a>' if d.get("explorer_url") else ""
            lines.append(f"\n🟢 <b>+{fmt_amount(Decimal(d['amount']))} {esc(d['asset'])}</b>{link}\n"
                         f"{net.name if net else d['network']} · {when}{frm_line}")
    rows = []
    nav = []
    if skip > 0:
        nav.append(K.btn("‹ Prev", f"trackdep|{max(0, skip - PAGE)}"))
    if skip + PAGE < total:
        nav.append(K.btn("Next ›", f"trackdep|{skip + PAGE}"))
    if nav:
        rows.append(nav)
    rows.append([K.btn("‹ Back", "track"), K.btn("🏠 Home", "home")])
    await _screen(chat_id, mid, "\n".join(lines), K.kb(rows))


def _rel_time(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        secs = (datetime.now(timezone.utc) - dt).total_seconds()
    except Exception:  # noqa: BLE001
        return ""
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"


# =================================================================
# SWAP (NEAR Intents / 1Click)
# =================================================================
async def _swap_intro(chat_id: int, uid: int, mid: int | None) -> None:
    if not intents_client.configured:
        await _screen(chat_id, mid, "🔄 <b>Swap</b>\n\nCross-chain swaps are not configured on this deployment.", K.back("home"))
        return
    w = await W.get_primary_wallet(uid)
    if not w:
        await _show_start(chat_id, uid); return
    text = (
        f"🔄 <b>Swap</b>\n{DIV}\n"
        "Cross-chain swaps are powered by <b>NEAR Intents</b>. "
        "Choose the network you'll pay from."
    )
    kb = K.networks_for("swfrom", "direct_send_supported", w["wallet_id"], {Family.EVM, Family.SOLANA})
    await _screen(chat_id, mid, text, kb)


async def _swap_pick_source_asset(chat_id: int, uid: int, mid: int, wallet_id: str, netkey: str) -> None:
    net = get_network(netkey)
    fam = net.family.value
    await states.set_state(uid, chat_id, "swap_src_asset", {"wallet_id": wallet_id, "from": netkey})
    if fam == "evm":
        rows = [[K.btn(f"{net.symbol} (native)", f"swasset|{wallet_id}|{netkey}|__native__")]]
        for t in tokens_for(netkey):
            rows.append([K.btn(t["symbol"], f"swasset|{wallet_id}|{netkey}|{t['symbol']}")])
        rows.append([K.btn("‹ Back", "swap")])
        await _screen(chat_id, mid, f"🔄 <b>Swap</b> · {net.name}\n\nWhich asset will you pay with?", K.kb(rows))
    else:
        await _swap_set_source_asset(chat_id, uid, mid, wallet_id, netkey, "__native__")


async def _swap_set_source_asset(chat_id: int, uid: int, mid: int, wallet_id: str, netkey: str, symbol: str) -> None:
    net = get_network(netkey)
    if symbol == "__native__":
        from_symbol, token_address, decimals = net.symbol, None, net.decimals
    else:
        tok = token_by_symbol(netkey, symbol)
        if not tok:
            await _send(chat_id, "Unsupported asset.", K.back("home")); return
        from_symbol, token_address, decimals = tok["symbol"], tok["address"], tok["decimals"]
    await states.set_state(uid, chat_id, "swap_dest", {
        "wallet_id": wallet_id, "from": netkey, "from_symbol": from_symbol,
        "from_token_address": token_address, "from_decimals": decimals,
    })
    await _swap_pick_dest(chat_id, uid, mid)


async def _swap_pick_dest(chat_id: int, uid: int, mid: int) -> None:
    sess = await states.get(uid)
    data = (sess or {}).get("data", {})
    try:
        chains = await intents_client.networks()
    except NearIntentsError as e:
        await _edit(chat_id, mid, f"🔄 <b>Swap</b>\n\nCouldn't reach the routing service.\n<i>{esc(str(e))}</i>", K.back("home"))
        return
    src = get_network(data.get("from", ""))
    from_symbol = data.get("from_symbol", src.symbol if src else "")
    # Only offer destinations the wallet can actually receive on, and not the
    # same chain we're paying from.
    receivable = [c for c in chains if c in _NEAR_TO_KEY and _NEAR_TO_KEY[c] != data.get("from")]
    rows, row = [], []
    for code in receivable:
        dnet = get_network(_NEAR_TO_KEY[code])
        row.append(K.btn(dnet.name, f"swto|{code}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([K.btn("‹ Back", "swap")])
    text = (
        f"🔄 <b>Swap</b> · pay <b>{esc(from_symbol)}</b> on {src.name}\n{DIV}\n"
        f"{len(chains)} networks reachable via NEAR Intents; showing the ones you can "
        f"receive on with this wallet.\n\nSelect a destination network."
    )
    await _edit(chat_id, mid, text, K.kb(rows))


async def _swap_prompt_amount(chat_id: int, uid: int, mid: int, dest_chain: str) -> None:
    sess = await states.get(uid)
    data = (sess or {}).get("data", {})
    dest_net = get_network(_NEAR_TO_KEY.get(dest_chain, dest_chain))
    data["dest_chain"] = dest_chain
    data["dest_name"] = dest_net.name if dest_net else dest_chain
    await states.set_state(uid, chat_id, "swap_await_amount", data)
    src = get_network(data.get("from", ""))
    from_symbol = data.get("from_symbol", src.symbol)
    await _edit(chat_id, mid, f"🔄 <b>Swap</b> · {esc(from_symbol)} → {esc(data['dest_name'])}\n\nEnter the amount of {esc(from_symbol)} to swap.", K.back("home"))


async def _flow_swap_amount(chat_id: int, uid: int, data: dict, text: str) -> None:
    try:
        amount = Decimal(text.strip())
        if amount <= 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        await _send(chat_id, "Enter a valid amount, e.g. 0.1")
        return
    await states.clear(uid)
    src = get_network(data["from"])
    from_symbol = data.get("from_symbol", src.symbol)
    dest_code = data["dest_chain"]
    dest_key = _NEAR_TO_KEY.get(dest_code, dest_code)
    dest_net = get_network(dest_key)
    dest_fam = dest_net.family.value if dest_net else None
    dest_name = data.get("dest_name", dest_net.name if dest_net else dest_code)
    try:
        tokens = await intents_client.get_tokens()
        origin = _match_asset(tokens, _near_code(data["from"]), from_symbol)
        # prefer the destination chain's native asset, else any asset on it
        dest = (_match_asset(tokens, dest_code, dest_net.symbol if dest_net else None)
                or _match_asset(tokens, dest_code, None))
        if not origin or not dest:
            await _send(chat_id, "🔄 Couldn't map those assets in the routing catalog. Try a different pair.", K.back("home"))
            return
        # refund goes back to the SOURCE-chain address; recipient is the
        # user's own address on the DESTINATION chain.
        refund_addr = await W.get_address(uid, data["wallet_id"], src.family.value)
        recipient_addr = await W.get_address(uid, data["wallet_id"], dest_fam) if dest_fam else None
        if not recipient_addr:
            await _send(chat_id, "🔄 This wallet can't receive on that destination network yet. Pick another destination.", K.back("home"))
            return
        from datetime import datetime, timedelta, timezone
        deadline = (datetime.now(timezone.utc) + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload = {
            "originAsset": origin.get("assetId") or origin.get("id"),
            "destinationAsset": dest.get("assetId") or dest.get("id"),
            "amount": str(int(amount * Decimal(10 ** int(origin.get("decimals", 18))))),
            "refundTo": refund_addr, "refundType": "ORIGIN_CHAIN",
            "recipient": recipient_addr, "recipientType": "DESTINATION_CHAIN",
            "swapType": "EXACT_INPUT", "slippageTolerance": 100,
            "depositType": "ORIGIN_CHAIN", "depositMode": "SIMPLE",
            "deadline": deadline, "referral": _referral(),
        }
        quote = await intents_client.request_quote(payload, dry=True)
    except NearIntentsError as e:
        await _send(chat_id, f"🔄 Swap quote failed.\n<i>{esc(str(e))}</i>", K.back("home"))
        return
    q = quote.get("quote", quote)
    out = q.get("amountOutFormatted") or q.get("amountOut") or "?"
    min_out = q.get("minAmountOutFormatted") or q.get("minAmountOut")
    deadline = q.get("deadline") or q.get("timeEstimate") or ""
    dest_sym = esc(dest.get("symbol", ""))
    order_id = uuid.uuid4().hex
    await dbm.swap_orders.insert_one({
        "order_id": order_id, "telegram_user_id": uid, "wallet_id": data["wallet_id"],
        "payload": payload, "from": data["from"], "dest_chain": dest_code, "dest_name": dest_name,
        "from_symbol": from_symbol, "from_token_address": data.get("from_token_address"),
        "from_decimals": int(data.get("from_decimals", src.decimals)),
        "amount": str(amount), "state": "quoted", "created_at": _iso_now(),
    })
    lines = [
        f"{DIV}\n     <b>SWAP QUOTE</b>\n{DIV}\n",
        f"You pay     <b>{fmt_amount(amount)} {esc(from_symbol)}</b>",
        f"From        {src.name}",
        f"You receive ~<b>{out} {dest_sym}</b>",
        f"To          {esc(dest_name)}",
    ]
    if min_out:
        lines.append(f"Min received ~{esc(str(min_out))} {dest_sym}")
    if deadline:
        lines.append(f"Quote expiry {esc(str(deadline))}")
    lines.append("\nQuotes move with the market. Confirm to lock a deposit address.")
    text_out = "\n".join(lines)
    await _send(chat_id, text_out, K.kb([[K.btn("✅ Confirm swap", f"swgo|{order_id}")], [K.btn("❌ Cancel", "home")]]))


async def _swap_create(chat_id: int, uid: int, mid: int, order_id: str) -> None:
    order = await dbm.swap_orders.find_one({"telegram_user_id": uid, "order_id": order_id}, {"_id": 0})
    if not order:
        await _edit(chat_id, mid, "That swap could not be found.", K.back("home")); return
    await _edit(chat_id, mid, "🔄 <b>Locking quote…</b>", None)
    try:
        quote = await intents_client.request_quote(order["payload"], dry=False)
    except NearIntentsError as e:
        await _edit(chat_id, mid, f"That quote expired before the swap was submitted. Get a fresh quote.\n<i>{esc(str(e))}</i>", K.back("home"))
        return
    q = quote.get("quote", quote)
    deposit = q.get("depositAddress")
    memo = q.get("depositMemo")
    await dbm.swap_orders.update_one({"order_id": order_id}, {"$set": {"state": "awaiting_deposit", "deposit_address": deposit, "deposit_memo": memo}})
    src = get_network(order["from"])
    from_symbol = order.get("from_symbol", src.symbol)
    text = (
        f"{DIV}\n  <b>⏳ AWAITING DEPOSIT</b>\n{DIV}\n\n"
        f"Send exactly <b>{fmt_amount(Decimal(order['amount']))} {esc(from_symbol)}</b> on {src.name} to:\n\n"
        f"<code>{deposit}</code>\n"
        + (f"\nMemo/Tag: <code>{memo}</code>\n" if memo else "")
        + "\nTap below to pay straight from your wallet, or send manually. Routing begins automatically once received."
    )
    rows = []
    if src.family in (Family.EVM, Family.SOLANA):
        rows.append([K.btn("⚡ Pay from wallet", f"swpay|{order_id}")])
    rows.append([K.btn("↻ Check status", f"swstat|{order_id}")])
    rows.append([K.btn("🏠 Home", "home")])
    await _edit(chat_id, mid, text, K.kb(rows))


async def _swap_pay(chat_id: int, uid: int, mid: int, order_id: str) -> None:
    order = await dbm.swap_orders.find_one({"telegram_user_id": uid, "order_id": order_id}, {"_id": 0})
    if not order or not order.get("deposit_address"):
        await _edit(chat_id, mid, "No deposit address for this swap.", K.back("home")); return
    src = get_network(order["from"])
    fam = src.family.value
    addr = await W.get_address(uid, order["wallet_id"], fam)
    if not addr:
        await _edit(chat_id, mid, "No account for the source network.", K.back("home")); return
    amount = Decimal(order["amount"])
    token_address = order.get("from_token_address")
    decimals = int(order.get("from_decimals", src.decimals))
    from_symbol = order.get("from_symbol", src.symbol)

    # revalidate balance + gas before spending
    try:
        if fam == "evm":
            native_bal = await evm_adapter.native_balance(order["from"], addr)
            fee = (await (evm_adapter.estimate_token_fee(order["from"]) if token_address
                         else evm_adapter.estimate_native_fee(order["from"]))).fee_native
            if token_address:
                tok_bal = await evm_adapter.token_balance(
                    order["from"], {"address": token_address, "decimals": decimals}, addr)
                if amount > tok_bal:
                    await _edit(chat_id, mid, f"⚠️ <b>Insufficient {esc(from_symbol)}</b>\n\n"
                                              f"You have: <b>{fmt_amount(tok_bal)} {esc(from_symbol)}</b>\n"
                                              f"Deposit needs: <b>{fmt_amount(amount)} {esc(from_symbol)}</b>",
                                K.back("home")); return
                if fee > native_bal:
                    await _edit(chat_id, mid, f"⚠️ <b>Insufficient gas</b>\n\n"
                                              f"You have: <b>{fmt_amount(native_bal)} {src.symbol}</b>\n"
                                              f"Estimated required: <b>{fmt_amount(fee)} {src.symbol}</b>",
                                K.back("home")); return
            elif amount + fee > native_bal:
                await _edit(chat_id, mid, f"⚠️ <b>Insufficient balance</b>\n\n"
                                          f"You have: <b>{fmt_amount(native_bal)} {src.symbol}</b>\n"
                                          f"Needed (amount + fee): <b>{fmt_amount(amount + fee)} {src.symbol}</b>",
                            K.back("home")); return
        else:
            native_bal = await solana_adapter.native_balance(addr)
            fee = (await solana_adapter.estimate_native_fee()).fee_native
            if amount + fee > native_bal:
                await _edit(chat_id, mid, f"⚠️ <b>Insufficient balance</b>\n\n"
                                          f"You have: <b>{fmt_amount(native_bal)} {src.symbol}</b>",
                            K.back("home")); return
    except Exception:  # noqa: BLE001
        pass  # best-effort pre-check; broadcast still guards on-chain

    await _edit(chat_id, mid, "⚡ <b>Paying deposit from wallet…</b>", None)
    # idempotent: order_id is the session nonce, so re-taps won't double-pay
    tx = await TX.create_pending_send(
        uid, order["wallet_id"], order["from"], from_symbol, order["deposit_address"],
        str(amount), fam, addr, f"swap:{order_id}", token_address, decimals,
    )
    result = await TX.confirm_and_broadcast(uid, tx["tx_id"])
    if result.get("state") == SM.BROADCASTED:
        await dbm.swap_orders.update_one({"order_id": order_id}, {"$set": {"state": "processing", "deposit_tx": result.get("tx_hash")}})
        # notify 1Click so routing starts immediately (best-effort)
        try:
            await intents_client.submit_deposit(result.get("tx_hash"), order["deposit_address"], order.get("deposit_memo"))
        except Exception:  # noqa: BLE001
            pass
        text = (
            f"✅ <b>Deposit sent.</b>\n\n{fmt_amount(amount)} {esc(from_symbol)} → routing\n"
            f"<code>{shorten_address(result.get('tx_hash',''), 10, 8)}</code>\n\n"
            "We'll notify you as the swap progresses."
        )
        await _edit(chat_id, mid, text, K.kb([[K.btn("↻ Check status", f"swstat|{order_id}")], [K.btn("🏠 Home", "home")]]))
    else:
        err = esc(result.get("error") or "Could not broadcast the deposit.")
        await _edit(chat_id, mid, f"⚠️ <b>Payment failed.</b>\n\n{err}", K.kb([[K.btn("↻ Retry", f"swpay|{order_id}")], [K.btn("🏠 Home", "home")]]))


async def _swap_status(chat_id: int, uid: int, mid: int, order_id: str) -> None:
    order = await dbm.swap_orders.find_one({"telegram_user_id": uid, "order_id": order_id}, {"_id": 0})
    if not order or not order.get("deposit_address"):
        await _edit(chat_id, mid, "No deposit tracked for this swap yet.", K.back("home")); return
    try:
        st = await intents_client.get_status(order["deposit_address"], order.get("deposit_memo"))
    except NearIntentsError as e:
        await _edit(chat_id, mid, f"Couldn't fetch status.\n<i>{esc(str(e))}</i>", K.kb([[K.btn("↻ Retry", f"swstat|{order_id}")], [K.btn("🏠 Home", "home")]]))
        return
    status = (st or {}).get("status", "UNKNOWN")
    label = {
        "PENDING_DEPOSIT": "⏳ Awaiting deposit", "KNOWN_DEPOSIT_TX": "⚡ Deposit detected",
        "PROCESSING": "🔄 Routing", "SUCCESS": "✅ Completed", "REFUNDED": "↩️ Refunded",
        "FAILED": "⚠️ Failed",
    }.get(status, status)
    await _edit(chat_id, mid, f"🔄 <b>Swap status</b>\n{DIV}\n\n{label}", K.kb([[K.btn("↻ Refresh", f"swstat|{order_id}")], [K.btn("🏠 Home", "home")]]))


_NEAR_CODE = {
    "ethereum": "eth", "base": "base", "arbitrum": "arb", "optimism": "op",
    "polygon": "pol", "bnb": "bsc", "avalanche": "avax", "solana": "sol",
}

# Reverse map: 1Click chain code -> our network key. Only destinations we can
# actually receive on (i.e. this wallet holds an address for them) are offered.
_NEAR_TO_KEY = {v: k for k, v in _NEAR_CODE.items()}


def _near_code(network_key: str) -> str:
    return _NEAR_CODE.get(network_key, network_key)


def _match_asset(tokens: list[dict], chain: str, symbol: str | None) -> dict | None:
    chain = chain.lower()
    for t in tokens:
        tchain = (t.get("blockchain") or t.get("chain") or "").lower()
        tsym = (t.get("symbol") or "").upper()
        if chain == tchain or chain in tchain or tchain in chain:
            if symbol is None or tsym == symbol.upper():
                return t
    return None


# =================================================================
# SETTINGS / EMAIL / NETWORKS / ADDRESS BOOK
# =================================================================
async def _show_settings(chat_id: int, uid: int, mid: int) -> None:
    user = await dbm.users.find_one({"telegram_user_id": uid}, {"_id": 0})
    has_email = bool(user and user.get("email"))
    text = f"⚙️ <b>Settings</b>\n{DIV}\n\nManage wallets, security, networks and more."
    await _screen(chat_id, mid, text, K.settings_menu(has_email))


async def _show_networks(chat_id: int, mid: int) -> None:
    native = [n for n in NETWORKS.values() if n.capabilities.direct_send_supported]
    routed = [n for n in NETWORKS.values() if n.capabilities.near_intents_supported and not n.capabilities.direct_send_supported]
    lines = [f"🌐 <b>Supported Networks</b>\n{DIV}", "\n<b>Native wallet</b> — generate, hold, send, receive:"]
    lines.append("  " + ", ".join(n.name for n in native))
    lines.append("\n<b>Swap routing</b> — via NEAR Intents:")
    lines.append("  " + ", ".join(n.name for n in routed))
    lines.append("\n<i>The live swap catalog is discovered dynamically at swap time.</i>")
    await _edit(chat_id, mid, "\n".join(lines), K.back("settings"))


async def _show_email(chat_id: int, uid: int, mid: int) -> None:
    user = await dbm.users.find_one({"telegram_user_id": uid}, {"_id": 0})
    email = user.get("email") if user else None
    verified = user.get("email_verified") if user else False
    if email:
        status = "✅ verified" if verified else "⏳ pending verification"
        text = f"📧 <b>Email</b>\n{DIV}\n\n{esc(email)}  ·  {status}\n\nEmail is an optional channel for account confirmations. It never contains wallet secrets."
        kb = K.kb([[K.btn("Change email", "emailadd")], [K.btn("Remove email", "emailrm")], [K.btn("‹ Settings", "settings")]])
    else:
        text = f"📧 <b>Email</b>\n{DIV}\n\nAdd an optional email for account confirmations. It's never required to use OXAEL and never carries wallet secrets."
        kb = K.kb([[K.btn("Add email", "emailadd")], [K.btn("‹ Settings", "settings")]])
    await _edit(chat_id, mid, text, kb)


async def _prompt_email(chat_id: int, uid: int, mid: int) -> None:
    await states.set_state(uid, chat_id, "email_await_address", {})
    await _edit(chat_id, mid, "📧 Send the email address you'd like to add.", K.back("settings"))


async def _flow_email(chat_id: int, uid: int, text: str) -> None:
    await states.clear(uid)
    email = text.strip()
    if not email_service.is_valid_email(email):
        await _send(chat_id, "That doesn't look like a valid email. Try again from Settings → Email.", K.back("settings"))
        return
    try:
        await email_service.start_verification(uid, email)
    except ValueError:
        await _send(chat_id, "Invalid email format.", K.back("settings")); return
    from config import settings as _s
    if _s.email_configured:
        msg = f"📧 Verification sent to <b>{esc(email)}</b>. Check your inbox to confirm."
    else:
        msg = f"📧 Saved <b>{esc(email)}</b>. Email delivery isn't configured on this deployment yet, so no message was sent."
    await _send(chat_id, msg, K.kb([[K.btn("‹ Settings", "settings")]]))


async def _remove_email(chat_id: int, uid: int, mid: int) -> None:
    await email_service.remove_email(uid)
    await _show_email(chat_id, uid, mid)


async def _show_abook(chat_id: int, uid: int, mid: int) -> None:
    cur = dbm.address_book.find({"telegram_user_id": uid}, {"_id": 0})
    entries = [e async for e in cur]
    lines = [f"📝 <b>Address Book</b>\n{DIV}"]
    rows = []
    if not entries:
        lines.append("\nNo saved addresses yet.")
    else:
        for e in entries:
            net = NETWORKS.get(e["network"])
            lines.append(f"\n<b>{esc(e['label'])}</b> · {net.name if net else e['network']}\n<code>{shorten_address(e['address'], 8, 6)}</code>")
            rows.append([K.btn(f"🗑 {e['label'][:20]}", f"abdel|{e['entry_id']}")])
    rows.append([K.btn("➕ Add address", "abadd")])
    rows.append([K.btn("‹ Settings", "settings")])
    await _edit(chat_id, mid, "\n".join(lines), K.kb(rows))


async def _ab_pick_network(chat_id: int, uid: int, mid: int) -> None:
    rows, row = [], []
    for net in networks_with("receive_supported"):
        if net.family not in {Family.EVM, Family.SOLANA}:
            continue
        row.append(K.btn(net.name, f"ab|net|{net.key}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([K.btn("‹ Back", "abook")])
    await _edit(chat_id, mid, "📝 <b>Add address</b>\n\nSelect the network.", K.kb(rows))


async def _ab_set_network(chat_id: int, uid: int, netkey: str) -> None:
    await states.set_state(uid, chat_id, "ab_await_label", {"network": netkey})
    await _send(chat_id, "Send a label for this address (e.g. “Exchange”).")


async def _flow_ab_label(chat_id: int, uid: int, data: dict, text: str) -> None:
    data["label"] = text[:30].strip()
    await states.set_state(uid, chat_id, "ab_await_address", data)
    net = get_network(data["network"])
    await _send(chat_id, f"Now send the {net.name} address to save.")


async def _flow_ab_address(chat_id: int, uid: int, data: dict, text: str) -> None:
    addr = text.strip()
    net = get_network(data["network"])
    fam = net.family.value
    ok = evm_adapter.validate_address(addr) if fam == "evm" else solana_adapter.validate_address(addr)
    if not ok:
        await _send(chat_id, "That address doesn't match the selected network. Send it again.")
        return
    await states.clear(uid)
    await dbm.address_book.insert_one({
        "entry_id": uuid.uuid4().hex, "telegram_user_id": uid,
        "label": data["label"], "address": addr, "network": data["network"],
    })
    await _send(chat_id, f"✅ Saved <b>{esc(data['label'])}</b>.", K.kb([[K.btn("📝 Address Book", "abook")]]))


# =================================================================
# STATIC TEXT
# =================================================================
def _security_onboard_text() -> str:
    return (
        f"🔐 <b>How OXAEL keeps you safe</b>\n{DIV}\n\n"
        "OXAEL is a <b>custodial</b> wallet. Your signing keys are encrypted with "
        "AES-256-GCM and only decrypted for the instant needed to sign a transaction you approve.\n\n"
        "◈  We never message or display your private keys\n"
        "◈  We never ask for your recovery secrets\n"
        "◈  Access to your Telegram is access to your wallet\n\n"
        "Keep your Telegram account secured with 2FA."
    )


def _security_text() -> str:
    return (
        f"🔐 <b>Security</b>\n{DIV}\n\n"
        "◈  Keys encrypted at rest (AES-256-GCM, versioned)\n"
        "◈  Secrets never logged, exported, or sent in chat\n"
        "◈  Every action scoped to your Telegram account\n"
        "◈  Explicit confirmation required before any send\n\n"
        "OXAEL is custodial: signing material is decrypted server-side only to sign transactions you approve. "
        "Secure your Telegram account with a password/2FA."
    )


def _help_text() -> str:
    return (
        f"📖 <b>Help</b>\n{DIV}\n\n"
        "<b>Commands</b>\n"
        "/start — start OXAEL Wallet\n"
        "/wallet — open your active wallet\n"
        "/wallets — manage wallets\n"
        "/create — create a new wallet\n"
        "/import — import an existing wallet\n"
        "/balance — view balances\n"
        "/send — send crypto (native &amp; tokens)\n"
        "/receive — address + QR\n"
        "/swap — cross-chain swap via NEAR Intents\n"
        "/history — transaction history\n"
        "/track — track wallet activity\n"
        "/settings — settings &amp; security\n"
        "/help — this screen\n\n"
        "◈  <b>Send</b> — native coins &amp; ERC-20 tokens (USDC/USDT) on every EVM chain, plus Solana\n"
        "◈  <b>Swap</b> — cross-chain routing with one-tap pay from your wallet\n"
        "◈  <b>Track</b> — get notified when crypto arrives\n\n"
        f"<i>{BRAND_NAME} · {BRAND_TAGLINE}</i>"
    )
