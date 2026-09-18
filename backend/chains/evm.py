"""EVM chain adapter — one key operates across all supported EVM networks.

The same private key yields the same address on Ethereum, Base, Arbitrum,
Optimism, Polygon, BNB Chain and Avalanche. That single account acts as the
"gas account" for every EVM network (the OXAEL one-click EVM wallet).

Reliability: every read/write tries a list of public RPC endpoints per chain
(plus an optional RPC_<NETWORK> env override, tried first) and falls back to the
next endpoint on failure, so one flaky provider never breaks the wallet. A
distinct ``RpcUnavailable`` error lets callers separate "provider down" from a
genuine zero balance.
"""
from __future__ import annotations
import asyncio
import logging
import re
from collections import defaultdict
from decimal import Decimal

from eth_account import Account
from web3 import Web3
from web3.exceptions import ContractLogicError
try:
    from web3.exceptions import Web3RPCError  # web3 v7+/v8
except Exception:  # noqa: BLE001
    class Web3RPCError(Exception):  # pragma: no cover - fallback
        pass

from config import settings
from chains.base import ChainAdapter, WalletKeys, AssetBalance, FeeEstimate
from assets.catalog import NETWORKS, tokens_for

Account.enable_unaudited_hdwallet_features()
logger = logging.getLogger("oxael.evm")

_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_PK_RE = re.compile(r"^(0x)?[0-9a-fA-F]{64}$")


class RpcUnavailable(Exception):
    """Raised when every RPC endpoint for a network failed (provider down)."""


# Multiple reliable public endpoints per chain (first that works wins).
# publicnode.com endpoints are no-auth and generally very reliable.
_DEFAULT_RPCS = {
    "ethereum": [
        "https://ethereum-rpc.publicnode.com",
        "https://eth.drpc.org",
        "https://rpc.ankr.com/eth",
        "https://cloudflare-eth.com",
    ],
    "base": [
        "https://base-rpc.publicnode.com",
        "https://mainnet.base.org",
        "https://base.drpc.org",
    ],
    "arbitrum": [
        "https://arbitrum-one-rpc.publicnode.com",
        "https://arb1.arbitrum.io/rpc",
        "https://arbitrum.drpc.org",
    ],
    "optimism": [
        "https://optimism-rpc.publicnode.com",
        "https://mainnet.optimism.io",
        "https://optimism.drpc.org",
    ],
    "polygon": [
        "https://polygon-bor-rpc.publicnode.com",
        "https://polygon.drpc.org",
        "https://rpc.ankr.com/polygon",
    ],
    "bnb": [
        "https://bsc-rpc.publicnode.com",
        "https://bsc-dataseed.binance.org",
        "https://binance.llamarpc.com",
    ],
    "avalanche": [
        "https://avalanche-c-chain-rpc.publicnode.com",
        "https://api.avax.network/ext/bc/C/rpc",
        "https://avalanche.drpc.org",
    ],
}

_ERC20_ABI = [
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}],
     "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals",
     "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "symbol",
     "outputs": [{"name": "", "type": "string"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "name",
     "outputs": [{"name": "", "type": "string"}], "type": "function"},
    {"constant": False, "inputs": [{"name": "_to", "type": "address"}, {"name": "_value", "type": "uint256"}],
     "name": "transfer", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
]

_w3_cache: dict[str, Web3] = {}
_working_url: dict[str, str] = {}          # last known-good endpoint per chain
_chain_ok: dict[str, bool] = {}            # f"{net}:{url}" -> eth_chainId validated
_nonce_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _rpc_urls(network_key: str) -> list[str]:
    urls: list[str] = []
    override = settings.rpc(network_key.upper(), "")
    if override:
        urls.append(override)
    for u in _DEFAULT_RPCS.get(network_key, []):
        if u not in urls:
            urls.append(u)
    return urls


def _w3_for(url: str) -> Web3:
    if url not in _w3_cache:
        _w3_cache[url] = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 15}))
    return _w3_cache[url]


def _ordered_urls(network_key: str) -> list[str]:
    urls = _rpc_urls(network_key)
    if not urls:
        return []
    good = _working_url.get(network_key)
    if good and good in urls:
        urls = [good] + [u for u in urls if u != good]
    return urls


def _check_chain(w3: Web3, network_key: str, url: str) -> None:
    """Validate the endpoint really serves the expected chain (eth_chainId).
    Cached per (network,url). Raises to skip a mismatched/broken endpoint."""
    ck = f"{network_key}:{url}"
    if ck in _chain_ok:
        if not _chain_ok[ck]:
            raise RpcUnavailable("endpoint failed chainId validation")
        return
    cid = w3.eth.chain_id  # connection errors propagate -> try next endpoint
    expected = NETWORKS[network_key].chain_id
    ok = expected is None or int(cid) == int(expected)
    _chain_ok[ck] = ok
    if not ok:
        logger.warning("rpc chain mismatch net=%s url=%s got=%s expected=%s",
                       network_key, url, cid, expected)
        raise RpcUnavailable("wrong chain id")


def _sync_run(network_key: str, fn, retry_rpc_error: bool = True):
    urls = _ordered_urls(network_key)
    if not urls:
        raise RpcUnavailable(f"no RPC configured for {network_key}")
    last: Exception | None = None
    for url in urls:
        try:
            w3 = _w3_for(url)
            _check_chain(w3, network_key, url)
            res = fn(w3)
            _working_url[network_key] = url
            return res
        except ContractLogicError:
            # deterministic (e.g. a token that reverts) — do NOT try other
            # endpoints; let the caller decide (portfolio skips such tokens).
            raise
        except Web3RPCError:
            # A node-processed JSON-RPC error. For sends this is the real reason
            # (insufficient funds / nonce / already-known) — surface it and NEVER
            # retry (retrying could double-broadcast). For reads, a single node
            # may be flaky, so fall through to the next endpoint.
            if not retry_rpc_error:
                raise
            last = _last_exc()
            logger.warning("rpc node error net=%s endpoint=%s", network_key, url)
            continue
        except Exception as e:  # noqa: BLE001
            last = e
            logger.warning("rpc call failed net=%s endpoint=%s err=%s",
                           network_key, url, type(e).__name__)
            continue
    raise RpcUnavailable(f"all RPC endpoints failed for {network_key}: {type(last).__name__ if last else '??'}")


def _last_exc():
    import sys
    return sys.exc_info()[1]


async def _run(network_key: str, fn, retry_rpc_error: bool = True):
    return await asyncio.to_thread(_sync_run, network_key, fn, retry_rpc_error)


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
            return WalletKeys(address=acct.address, secret=acct.key.hex(), secret_type="private_key")
        raise ValueError("Not a valid EVM private key or seed phrase")

    def validate_address(self, address: str) -> bool:
        return bool(_ADDR_RE.match(address or ""))

    # ---- balances ----
    async def native_balance(self, network_key: str, address: str) -> Decimal:
        def fn(w3):
            wei = w3.eth.get_balance(Web3.to_checksum_address(address))
            return Decimal(wei) / Decimal(10 ** 18)
        return await _run(network_key, fn)

    async def token_balance(self, network_key: str, token: dict, address: str) -> Decimal:
        def fn(w3):
            c = w3.eth.contract(address=Web3.to_checksum_address(token["address"]), abi=_ERC20_ABI)
            raw = c.functions.balanceOf(Web3.to_checksum_address(address)).call()
            return Decimal(raw) / Decimal(10 ** int(token["decimals"]))
        return await _run(network_key, fn)

    async def token_metadata(self, network_key: str, contract: str) -> dict:
        """Read symbol/name/decimals on-chain. Any missing field is left blank
        (so unknown/spam tokens never crash discovery)."""
        def fn(w3):
            c = w3.eth.contract(address=Web3.to_checksum_address(contract), abi=_ERC20_ABI)
            out = {"address": Web3.to_checksum_address(contract), "symbol": "", "name": "", "decimals": 18}
            try:
                out["decimals"] = int(c.functions.decimals().call())
            except Exception:  # noqa: BLE001
                pass
            try:
                out["symbol"] = str(c.functions.symbol().call())
            except Exception:  # noqa: BLE001
                pass
            try:
                out["name"] = str(c.functions.name().call())
            except Exception:  # noqa: BLE001
                pass
            return out
        return await _run(network_key, fn)

    async def get_balances(self, network_key: str, address: str) -> list[AssetBalance]:
        """Native + configured-token balances. Raises RpcUnavailable if the
        native read fails on every endpoint (so callers can flag the network)."""
        net = NETWORKS[network_key]
        out: list[AssetBalance] = []
        native = await self.native_balance(network_key, address)  # may raise RpcUnavailable
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
        def fn(w3):
            gas_price = w3.eth.gas_price
            gas_limit = 21000
            fee = Decimal(gas_price * gas_limit) / Decimal(10 ** 18)
            return gas_price, gas_limit, fee
        gas_price, gas_limit, fee = await _run(network_key, fn)
        net = NETWORKS[network_key]
        return FeeEstimate(fee, net.symbol, {"gas_price": gas_price, "gas_limit": gas_limit})

    async def estimate_token_fee(self, network_key: str) -> FeeEstimate:
        def fn(w3):
            gas_price = w3.eth.gas_price
            gas_limit = 90000
            fee = Decimal(gas_price * gas_limit) / Decimal(10 ** 18)
            return gas_price, gas_limit, fee
        gas_price, gas_limit, fee = await _run(network_key, fn)
        net = NETWORKS[network_key]
        return FeeEstimate(fee, net.symbol, {"gas_price": gas_price, "gas_limit": gas_limit})

    # ---- send (native) with per-address nonce coordination ----
    async def send_native(self, network_key: str, private_key: str, to_address: str,
                          amount: Decimal) -> str:
        acct = Account.from_key(private_key if private_key.startswith("0x") else "0x" + private_key)
        lock_key = f"{network_key}:{acct.address.lower()}"
        async with _nonce_locks[lock_key]:
            def fn(w3):
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
            return await _run(network_key, fn, retry_rpc_error=False)

    async def send_token(self, network_key: str, private_key: str, token_address: str,
                        decimals: int, to_address: str, amount: Decimal) -> str:
        acct = Account.from_key(private_key if private_key.startswith("0x") else "0x" + private_key)
        lock_key = f"{network_key}:{acct.address.lower()}"
        async with _nonce_locks[lock_key]:
            def fn(w3):
                net = NETWORKS[network_key]
                contract = w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=_ERC20_ABI)
                value = int(amount * Decimal(10 ** int(decimals)))
                to = Web3.to_checksum_address(to_address)
                nonce = w3.eth.get_transaction_count(acct.address, "pending")
                gas_price = w3.eth.gas_price
                fnc = contract.functions.transfer(to, value)
                try:
                    gas_limit = int(fnc.estimate_gas({"from": acct.address}) * 1.2)
                except Exception:  # noqa: BLE001
                    gas_limit = 90000
                tx = fnc.build_transaction({
                    "nonce": nonce, "gas": gas_limit, "gasPrice": gas_price, "chainId": net.chain_id,
                })
                signed = w3.eth.account.sign_transaction(tx, acct.key)
                tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                return tx_hash.hex()
            return await _run(network_key, fn, retry_rpc_error=False)

    async def get_receipt_status(self, network_key: str, tx_hash: str) -> int | None:
        """Return 1 (success), 0 (revert), or None (still pending / unknown)."""
        details = await self.get_receipt_details(network_key, tx_hash)
        return details.get("status") if details else None

    async def get_receipt_details(self, network_key: str, tx_hash: str) -> dict | None:
        """Real receipt data, or None if pending/unknown.
        {status, block_number, gas_used, gas_price(wei), fee_native(str)}."""
        def fn(w3):
            try:
                r = w3.eth.get_transaction_receipt(tx_hash)
            except Exception:  # noqa: BLE001
                return None
            if r is None:
                return None
            gas_used = int(r.get("gasUsed", 0))
            gas_price = int(r.get("effectiveGasPrice", 0))
            fee = Decimal(gas_used * gas_price) / Decimal(10 ** 18)
            return {
                "status": int(r.get("status", 1)),
                "block_number": int(r.get("blockNumber", 0)) or None,
                "gas_used": gas_used or None,
                "gas_price": gas_price or None,
                "fee_native": str(fee) if gas_used and gas_price else None,
            }
        try:
            return await _run(network_key, fn)
        except RpcUnavailable:
            return None


evm_adapter = EVMAdapter()
