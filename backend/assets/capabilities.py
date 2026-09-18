"""Capability registry.

Every network/asset declares exactly what it supports. The Telegram UI reads
these flags and ONLY offers functionality that is genuinely implemented. This
prevents faking support for a chain just because it exists somewhere upstream.
"""
from dataclasses import dataclass, field, asdict
from enum import Enum


class Family(str, Enum):
    EVM = "evm"
    SOLANA = "solana"
    UTXO = "utxo"
    TRON = "tron"
    XRP = "xrp"
    NEAR = "near"
    TON = "ton"
    STELLAR = "stellar"
    CARDANO = "cardano"
    OTHER = "other"


@dataclass
class Capabilities:
    wallet_generation_supported: bool = False
    wallet_import_supported: bool = False
    direct_signing_supported: bool = False
    direct_send_supported: bool = False
    token_send_supported: bool = False
    balance_supported: bool = False
    receive_supported: bool = False
    transaction_history_supported: bool = False
    tracking_supported: bool = False
    near_intents_supported: bool = False
    swap_supported: bool = False


@dataclass
class Network:
    key: str          # stable internal id, e.g. "ethereum"
    name: str         # display name
    family: Family
    symbol: str       # native asset symbol
    decimals: int = 18
    chain_id: int | None = None     # EVM chain id
    explorer: str | None = None     # base explorer url (tx path appended)
    coingecko_id: str | None = None
    capabilities: Capabilities = field(default_factory=Capabilities)

    def explorer_tx(self, tx_hash: str) -> str | None:
        if not self.explorer or not tx_hash:
            return None
        if self.family == Family.EVM:
            return f"{self.explorer}/tx/{tx_hash}"
        if self.family == Family.SOLANA:
            return f"{self.explorer}/tx/{tx_hash}"
        return f"{self.explorer}/tx/{tx_hash}"

    def explorer_address(self, address: str) -> str | None:
        if not self.explorer or not address:
            return None
        if self.family == Family.SOLANA:
            return f"{self.explorer}/account/{address}"
        return f"{self.explorer}/address/{address}"

    def to_public_dict(self) -> dict:
        d = asdict(self)
        d["family"] = self.family.value
        return d
