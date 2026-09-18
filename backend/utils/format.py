"""Display formatting helpers for the Telegram UI."""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN


def shorten_address(addr: str, head: int = 6, tail: int = 4) -> str:
    if not addr or len(addr) <= head + tail + 1:
        return addr
    return f"{addr[:head]}…{addr[-tail:]}"


def fmt_amount(value, max_dp: int = 6) -> str:
    try:
        d = Decimal(str(value))
    except Exception:  # noqa: BLE001
        return str(value)
    if d == 0:
        return "0"
    q = Decimal(1).scaleb(-max_dp)
    d = d.quantize(q, rounding=ROUND_DOWN)
    s = format(d.normalize(), "f")
    return s


def fmt_usd(value) -> str:
    try:
        d = Decimal(str(value))
    except Exception:  # noqa: BLE001
        return ""
    return f"${d.quantize(Decimal('0.01')):,}"


def rel_time(iso_ts: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_ts)
    except Exception:  # noqa: BLE001
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    delta = now - dt
    secs = int(delta.total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    days = secs // 86400
    if days == 1:
        return "yesterday"
    if days < 30:
        return f"{days}d ago"
    return dt.strftime("%b %d, %Y")
