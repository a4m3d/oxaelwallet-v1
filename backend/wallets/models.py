"""Wallet domain models (documents are stored as plain dicts, _id projected out)."""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


class Account(BaseModel):
    address: str
    enc: str                 # encrypted signing secret (never returned to clients)
    secret_type: str         # private_key | keypair


class WalletPublic(BaseModel):
    """Safe projection of a wallet — never contains secrets."""
    wallet_id: str
    name: str
    source: str
    is_primary: bool
    families: list[str]
    addresses: dict[str, str]


class User(BaseModel):
    telegram_user_id: int
    username: Optional[str] = None
    email: Optional[str] = None
    email_verified: bool = False
    email_verified_at: Optional[str] = None
    welcome_email_sent: bool = False
    onboarded: bool = False
    created_at: str
    updated_at: str
