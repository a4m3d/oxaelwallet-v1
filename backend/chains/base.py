"""Chain adapter interface and shared value types.

A `WalletKeys` bundles a public address with its (plaintext, in-memory only)
signing secret. Secrets are encrypted immediately by the wallet service and the
plaintext is dropped from memory as soon as practical.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal


@dataclass
class WalletKeys:
    address: str
    secret: str          # PLAINTEXT — never persist or log
    secret_type: str     # "private_key" | "mnemonic" | "keypair"


@dataclass
class AssetBalance:
    symbol: str
    network: str
    amount: Decimal
    decimals: int
    token_address: str | None = None
    usd_value: Decimal | None = None
    name: str | None = None
    verified: bool = True


@dataclass
class FeeEstimate:
    fee_native: Decimal
    symbol: str
    detail: dict


class ChainAdapter(ABC):
    family: str

    @abstractmethod
    def generate(self) -> WalletKeys: ...

    @abstractmethod
    def from_secret(self, secret: str) -> WalletKeys:
        """Import from a private key / seed. Raises ValueError if invalid."""

    @abstractmethod
    def validate_address(self, address: str) -> bool: ...
