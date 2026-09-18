"""Provider-independent email service (optional secondary channel).

Telegram is the primary interface. Email is optional and dormant until
configured via env. Emails NEVER contain wallet secrets. Verification tokens are
stored only as SHA-256 hashes with an expiry and are never logged.
"""
from __future__ import annotations
import hashlib
import logging
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from email_validator import validate_email as _ev, EmailNotValidError

import database as dbm
from config import settings, BRAND_NAME, BRAND_TAGLINE

logger = logging.getLogger("oxael.email")

_TOKEN_TTL_MIN = 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_valid_email(email: str) -> bool:
    try:
        _ev(email, check_deliverability=False)
        return True
    except EmailNotValidError:
        return False


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------- provider abstraction ----------------
def _send_via_smtp(to: str, subject: str, html: str, text: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{settings.EMAIL_FROM_NAME} <{settings.EMAIL_FROM}>"
    msg["To"] = to
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))
    port = int(settings.SMTP_PORT or 587)
    with smtplib.SMTP(settings.SMTP_HOST, port, timeout=20) as s:
        s.starttls()
        if settings.SMTP_USERNAME:
            s.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        s.sendmail(settings.EMAIL_FROM, [to], msg.as_string())


def _deliver(to: str, subject: str, html: str, text: str) -> bool:
    provider = (settings.EMAIL_PROVIDER or "").lower()
    if not provider:
        logger.info("email skipped (no provider configured) subject=%s", subject)
        return False
    if provider == "smtp":
        _send_via_smtp(to, subject, html, text)
        return True
    logger.warning("unknown EMAIL_PROVIDER=%s", provider)
    return False


# ---------------- verification flow ----------------
async def start_verification(telegram_user_id: int, email: str) -> bool:
    if not is_valid_email(email):
        raise ValueError("invalid email format")
    token = secrets.token_urlsafe(32)
    await dbm.email_verifications.insert_one({
        "telegram_user_id": telegram_user_id,
        "email": email,
        "token_hash": _hash_token(token),
        "expires_at": (_now() + timedelta(minutes=_TOKEN_TTL_MIN)).isoformat(),
        "used": False,
        "created_at": _now().isoformat(),
    })
    await dbm.users.update_one(
        {"telegram_user_id": telegram_user_id},
        {"$set": {"email": email, "email_verified": False, "updated_at": _now().isoformat()}},
    )
    link = f"{settings.PUBLIC_BASE_URL}/api/email/verify?token={token}" if settings.PUBLIC_BASE_URL else None
    html, text = _verification_email(link, token)
    _deliver(email, f"Verify your {BRAND_NAME} email", html, text)
    return True


async def verify(token: str) -> dict | None:
    rec = await dbm.email_verifications.find_one({"token_hash": _hash_token(token), "used": False})
    if not rec:
        return None
    if datetime.fromisoformat(rec["expires_at"]) < _now():
        return None
    await dbm.email_verifications.update_one({"_id": rec["_id"]}, {"$set": {"used": True}})
    await dbm.users.update_one(
        {"telegram_user_id": rec["telegram_user_id"]},
        {"$set": {"email_verified": True, "email_verified_at": _now().isoformat()}},
    )
    await _maybe_send_welcome(rec["telegram_user_id"], rec["email"])
    return {"telegram_user_id": rec["telegram_user_id"], "email": rec["email"]}


async def _maybe_send_welcome(telegram_user_id: int, email: str) -> None:
    # Idempotent: atomically flip welcome_email_sent to prevent duplicates.
    res = await dbm.users.update_one(
        {"telegram_user_id": telegram_user_id, "welcome_email_sent": {"$ne": True}},
        {"$set": {"welcome_email_sent": True, "updated_at": _now().isoformat()}},
    )
    if res.modified_count == 0:
        return  # already sent
    html, text = _welcome_email()
    sent = _deliver(email, f"Welcome to {BRAND_NAME}", html, text)
    await dbm.email_events.insert_one({
        "telegram_user_id": telegram_user_id, "type": "welcome",
        "delivered": sent, "created_at": _now().isoformat(),
    })
    if not sent:
        # revert flag if delivery not actually attempted, so it can retry later
        await dbm.users.update_one(
            {"telegram_user_id": telegram_user_id},
            {"$set": {"welcome_email_sent": False}},
        )


async def remove_email(telegram_user_id: int) -> None:
    await dbm.users.update_one(
        {"telegram_user_id": telegram_user_id},
        {"$set": {"email": None, "email_verified": False, "email_verified_at": None,
                  "welcome_email_sent": False, "updated_at": _now().isoformat()}},
    )


# ---------------- templates (premium dark, mobile-friendly) ----------------
def _shell(inner: str) -> str:
    return f"""<!doctype html><html><body style="margin:0;background:#0a0b0f;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#e7ecf3;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0a0b0f;padding:32px 0;"><tr><td align="center">
<table width="480" cellpadding="0" cellspacing="0" style="max-width:480px;width:100%;background:#111219;border:1px solid #1e2130;border-radius:18px;overflow:hidden;">
<tr><td style="padding:34px 34px 8px 34px;">
<div style="font-size:13px;letter-spacing:5px;color:#5eead4;font-weight:600;">{BRAND_NAME}</div>
<div style="height:1px;background:linear-gradient(90deg,#5eead4,transparent);margin:14px 0 22px;"></div>
{inner}
</td></tr>
<tr><td style="padding:22px 34px 30px;border-top:1px solid #1e2130;color:#6b7280;font-size:12px;line-height:18px;">
{BRAND_TAGLINE}<br/>OXAEL will never ask you to share your recovery secrets, seed phrase or private keys.
</td></tr>
</table></td></tr></table></body></html>"""


def _verification_email(link: str | None, token: str) -> tuple[str, str]:
    action = (f'<a href="{link}" style="display:inline-block;background:#5eead4;color:#06231d;'
              f'text-decoration:none;padding:13px 26px;border-radius:10px;font-weight:700;">Verify email</a>'
              if link else '<span style="color:#9aa4b2;">Return to Telegram and enter the code shown there.</span>')
    html = _shell(f"""
<div style="font-size:22px;font-weight:700;margin-bottom:10px;">Confirm your email</div>
<div style="color:#9aa4b2;font-size:15px;line-height:23px;margin-bottom:24px;">
Verify this address to receive account confirmations from {BRAND_NAME}. This link expires in {_TOKEN_TTL_MIN} minutes.</div>
{action}
""")
    text = f"Confirm your {BRAND_NAME} email. " + (f"Verify: {link}" if link else "Enter the code from Telegram.")
    return html, text


def _welcome_email() -> tuple[str, str]:
    html = _shell(f"""
<div style="font-size:24px;font-weight:700;margin-bottom:12px;">Welcome to {BRAND_NAME}.</div>
<div style="color:#9aa4b2;font-size:15px;line-height:23px;margin-bottom:18px;">
Your wallet is ready. {BRAND_NAME} brings multi-network crypto management directly into Telegram —
one clean interface for holding, sending, receiving and swapping across chains.</div>
<div style="background:#0d1420;border:1px solid #1e2b3f;border-radius:12px;padding:16px 18px;color:#c9d3e0;font-size:14px;line-height:22px;">
Keep your wallet private. {BRAND_NAME} is custodial — signing material is encrypted on our servers —
but you should still treat access to your Telegram account as access to your wallet.</div>
""")
    text = (f"Welcome to {BRAND_NAME}. Your wallet is ready. Manage multi-network crypto directly in Telegram. "
            f"{BRAND_NAME} is custodial and will never ask for your recovery secrets.")
    return html, text
