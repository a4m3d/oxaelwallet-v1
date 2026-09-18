"""EVM chain adapter — one key operates across all supported EVM networks.

The same private key yields the same address on Ethereum, Base, Arbitrum,
Optimism, Polygon, BNB Chain and Avalanche. That single account acts as the
"gas account" for every EVM network (the OXAEL one-click EVM wallet).
"""
from __future__ import annotations
import asyncio
import re
from collections import defaultdict
from decimal import Decimal

from eth_account import Account
from web3 import Web3

from config import settings
from chains.base import ChainAdapter, WalletKeys, AssetBalance, FeeEstimate
from assets.catalog import NETWORKS, tokens_for

Account.enable_unaudited_hdwallet_features()

_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_PK_RE = re.compile(r"^(0x)?[0-9a-fA-F]{64}$")

_DEFAULT_RPC = {
    "ethereum": "https://eth.llamarpc.com",
    "base": "https://mainnet.base.org",
    "arbitrum": "https://arb1.arbitrum.io/rpc",
    "optimism": "https://mainnet.optimism.io",
    "polygon": "https://polygon-rpc.com",
    "bnb": "https://bsc-dataseed.binance.org",
    "avalanche": "https://api.avax.network/ext/bc/C/rpc",
}

_ERC20_ABI = [
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}],
     "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
    {"constant": False, "inputs": [{"name": "_to", "type": "address"}, {"name": "_value", "type": "uint256"}],
     "name": "transfer", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
]

_w3_cache: dict[str, Web3] = {}
_nonce_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _rpc_url(network_key: str) -> str:
    env_key = network_key.upper()
    return settings.rpc(env_key, _DEFAULT_RPC.get(network_key, ""))


def _w3(network_key: str) -> Web3:
    if network_key not in _w3_cache:
        url = _rpc_url(network_key)
        if not url:
            raise ValueError(f"No RPC configured for {network_key}")
        _w3_cache[network_key] = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 20}))
    return _w3_cache[network_key]


class EVMAdapter(ChainAdapter):
    family = "evm"

    def generate(self) -> WalletKeys:
        acct = Account.create()
        return WalletKeys(address=acct.address, secret=acct.key.hex(), secret_type="private_key")

    def from_secret(self, secret: str) -> WalletKeys:
        secret = secret.strip()
        if _PK_RE.match(secret):
            acct = Account.from_key(secret if secret.startswith("0x") else "0x" + secret)
            return WalletKeys(address=acct.address, secret=acct.key.hex(), secret_type="private_key")
        words = secret.split()
        if len(words) in (12, 15, 18, 21, 24):
            acct = Account.from_mnemonic(secret)
            # store the derived private key (not the mnemonic) for signing
            return WalletKeys(address=acct.address, secret=acct.key.hex(), secret_type="private_key")
        raise ValueError("Not a valid EVM private key or seed phrase")

    def validate_address(self, address: str) -> bool:
        return bool(_ADDR_RE.match(address or ""))

    # ---- balances ----
    async def native_balance(self, network_key: str, address: str) -> Decimal:
        def _call():
            w3 = _w3(network_key)
            wei = w3.eth.get_balance(Web3.to_checksum_address(address))
            return Decimal(wei) / Decimal(10 ** 18)
        return await asyncio.to_thread(_call)

    async def token_balance(self, network_key: str, token: dict, address: str) -> Decimal:
        def _call():
            w3 = _w3(network_key)
            c = w3.eth.contract(address=Web3.to_checksum_address(token["address"]), abi=_ERC20_ABI)
            raw = c.functions.balanceOf(Web3.to_checksum_address(address)).call()
            return Decimal(raw) / Decimal(10 ** token["decimals"])
        return await asyncio.to_thread(_call)

    async def get_balances(self, network_key: str, address: str) -> list[AssetBalance]:
        net = NETWORKS[network_key]
        out: list[AssetBalance] = []
        native = await self.native_balance(network_key, address)
        out.append(AssetBalance(net.symbol, network_key, native, 18))
        for tok in tokens_for(network_key):
            try:
                amt = await self.token_balance(network_key, tok, address)
            except Exception:  # noqa: BLE001
                continue
            if amt > 0:
                out.append(AssetBalance(tok["symbol"], network_key, amt, tok["decimals"], tok["address"]))
        return out

    # ---- fees ----
    async def estimate_native_fee(self, network_key: str) -> FeeEstimate:
        def _call():
            w3 = _w3(network_key)
            gas_price = w3.eth.gas_price
            gas_limit = 21000
            fee = Decimal(gas_price * gas_limit) / Decimal(10 ** 18)
            return gas_price, gas_limit, fee
        gas_price, gas_limit, fee = await asyncio.to_thread(_call)
        net = NETWORKS[network_key]
        return FeeEstimate(fee, net.symbol, {"gas_price": gas_price, "gas_limit": gas_limit})

    async def estimate_token_fee(self, network_key: str) -> FeeEstimate:
        def _call():
            w3 = _w3(network_key)
            gas_price = w3.eth.gas_price
            gas_limit = 90000
            fee = Decimal(gas_price * gas_limit) / Decimal(10 ** 18)
            return gas_price, gas_limit, fee
        gas_price, gas_limit, fee = await asyncio.to_thread(_call)
        net = NETWORKS[network_key]
        return FeeEstimate(fee, net.symbol, {"gas_price": gas_price, "gas_limit": gas_limit})

    # ---- send (native) with per-address nonce coordination ----
    async def send_native(self, network_key: str, private_key: str, to_address: str,
                          amount: Decimal) -> str:
        acct = Account.from_key(private_key if private_key.startswith("0x") else "0x" + private_key)
        lock_key = f"{network_key}:{acct.address.lower()}"

        async with _nonce_locks[lock_key]:
            def _build_and_send():
                w3 = _w3(network_key)
                net = NETWORKS[network_key]
                nonce = w3.eth.get_transaction_count(acct.address, "pending")
                gas_price = w3.eth.gas_price
                value = int(amount * Decimal(10 ** 18))
                tx = {
                    "nonce": nonce,
                    "to": Web3.to_checksum_address(to_address),
                    "value": value,
                    "gas": 21000,
                    "gasPrice": gas_price,
                    "chainId": net.chain_id,
                }
                signed = w3.eth.account.sign_transaction(tx, acct.key)
                tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                return tx_hash.hex()
            return await asyncio.to_thread(_build_and_send)

    async def send_token(self, network_key: str, private_key: str, token_address: str,
                        decimals: int, to_address: str, amount: Decimal) -> str:
        acct = Account.from_key(private_key if private_key.startswith("0x") else "0x" + private_key)
        lock_key = f"{network_key}:{acct.address.lower()}"

        async with _nonce_locks[lock_key]:
            def _build_and_send():
                w3 = _w3(network_key)
                net = NETWORKS[network_key]
                contract = w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=_ERC20_ABI)
                value = int(amount * Decimal(10 ** decimals))
                to = Web3.to_checksum_address(to_address)
                nonce = w3.eth.get_transaction_count(acct.address, "pending")
                gas_price = w3.eth.gas_price
                fn = contract.functions.transfer(to, value)
                try:
                    gas_limit = int(fn.estimate_gas({"from": acct.address}) * 1.2)
                except Exception:  # noqa: BLE001
                    gas_limit = 90000
                tx = fn.build_transaction({
                    "nonce": nonce, "gas": gas_limit, "gasPrice": gas_price, "chainId": net.chain_id,
                })
                signed = w3.eth.account.sign_transaction(tx, acct.key)
                tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                return tx_hash.hex()
            return await asyncio.to_thread(_build_and_send)

    async def get_receipt_status(self, network_key: str, tx_hash: str) -> int | None:
        """Return 1 (success), 0 (revert), or None (still pending / unknown)."""
        def _call():
            w3 = _w3(network_key)
            try:
                r = w3.eth.get_transaction_receipt(tx_hash)
            except Exception:  # noqa: BLE001
                return None
            if r is None:
                return None
            return int(r.get("status", 1))
        return await asyncio.to_thread(_call)


evm_adapter = EVMAdapter()
