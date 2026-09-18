"""Inline keyboard builders. Callback data is compact and NEVER holds secrets."""
from __future__ import annotations
from assets.catalog import NETWORKS, networks_with
from assets.capabilities import Family


def btn(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}


def url_btn(text: str, url: str) -> dict:
    return {"text": text, "url": url}


def kb(rows: list[list[dict]]) -> dict:
    return {"inline_keyboard": rows}


def onboarding() -> dict:
    return kb([
        [btn("✦  Create Wallet", "onbcreate")],
        [btn("↗  Import Wallet", "import")],
        [btn("How OXAEL keeps you safe", "seconboard")],
    ])


def security_ack() -> dict:
    return kb([
        [btn("✓  I understand — create my wallet", "create")],
        [btn("‹ Back", "home")],
    ])


def main_menu() -> dict:
    return kb([
        [btn("↑  Send", "send"), btn("↓  Receive", "recv")],
        [btn("⇄  Swap", "swap"), btn("🪙  Tokens", "tokens")],
        [btn("◷  Activity", "hist"), btn("🔔  Track", "track")],
        [btn("👛  Wallets", "wallets"), btn("⚙️  Settings", "settings")],
        [btn("↻  Refresh", "home")],
    ])


def back(to: str = "home") -> dict:
    return kb([[btn("‹ Back", to)]])


def wallets_menu(wallets: list[dict]) -> dict:
    rows = []
    for w in wallets:
        star = "★ " if w["is_primary"] else ""
        rows.append([btn(f"{star}{w['name']}", f"w|{w['wallet_id']}")])
    rows.append([btn("➕  Create Wallet", "create"), btn("↗  Import", "import")])
    rows.append([btn("‹ Back", "home")])
    return kb(rows)


def wallet_actions(wallet_id: str, is_primary: bool) -> dict:
    rows = [
        [btn("📥 Receive", f"recv|w|{wallet_id}"), btn("📜 History", f"hist|w|{wallet_id}")],
        [btn("✏️ Rename", f"wren|{wallet_id}")],
    ]
    if not is_primary:
        rows.append([btn("★ Make Primary", f"wprim|{wallet_id}")])
    rows.append([btn("🗑 Delete", f"wdel|{wallet_id}")])
    rows.append([btn("‹ Wallets", "wallets")])
    return kb(rows)


def confirm_delete(wallet_id: str) -> dict:
    return kb([
        [btn("🗑 Yes, delete", f"wdelok|{wallet_id}")],
        [btn("‹ Cancel", f"w|{wallet_id}")],
    ])


def networks_for(action: str, cap_attr: str, wallet_id: str, families: set[Family] | None = None) -> dict:
    rows = []
    row: list[dict] = []
    for net in networks_with(cap_attr):
        if families and net.family not in families:
            continue
        row.append(btn(net.name, f"{action}|net|{wallet_id}|{net.key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([btn("‹ Back", "home")])
    return kb(rows)


def send_review(tx_id: str) -> dict:
    return kb([
        [btn("✅  Confirm Send", f"sendgo|{tx_id}")],
        [btn("❌  Cancel", f"sendx|{tx_id}")],
    ])


def settings_menu(has_email: bool) -> dict:
    email_label = "📧  Email" + ("  ·  set" if has_email else "")
    return kb([
        [btn("👛  Wallet Management", "wallets")],
        [btn(email_label, "email")],
        [btn("🔐  Security", "security"), btn("🌐  Networks", "networks")],
        [btn("📝  Address Book", "abook"), btn("📖  Help", "help")],
        [btn("‹ Back", "home")],
    ])


def result_actions(explorer_url: str | None) -> dict:
    rows = []
    if explorer_url:
        rows.append([url_btn("🔍 View on explorer", explorer_url)])
    rows.append([btn("🏠 Home", "home")])
    return kb(rows)
