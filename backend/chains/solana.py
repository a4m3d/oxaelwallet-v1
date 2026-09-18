"""Solana chain adapter using solders + JSON-RPC."""
from __future__ import annotations
import asyncio
from decimal import Decimal

import httpx
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.message import Message
from solders.transaction import Transaction
from solders.hash import Hash

from config import settings
from chains.base import ChainAdapter, WalletKeys, AssetBalance, FeeEstimate

_DEFAULT_RPC = "https://api.mainnet-beta.solana.com"
_LAMPORTS = Decimal(10 ** 9)


def _rpc() -> str:
    return settings.rpc("SOLANA", _DEFAULT_RPC)


async def _rpc_call(method: str, params: list) -> dict:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(_rpc(), json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise RuntimeError(f"solana rpc error: {data['error'].get('message')}")
        return data["result"]


class SolanaAdapter(ChainAdapter):
    family = "solana"

    def generate(self) -> WalletKeys:
        kp = Keypair()
        return WalletKeys(address=str(kp.pubkey()), secret=str(kp), secret_type="keypair")

    def from_secret(self, secret: str) -> WalletKeys:
        secret = secret.strip()
        try:
            kp = Keypair.from_base58_string(secret)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("Not a valid Solana keypair (base58 secret expected)") from exc
        return WalletKeys(address=str(kp.pubkey()), secret=str(kp), secret_type="keypair")

    def validate_address(self, address: str) -> bool:
        try:
            Pubkey.from_string(address)
            return True
        except Exception:  # noqa: BLE001
            return False

    async def native_balance(self, address: str) -> Decimal:
        res = await _rpc_call("getBalance", [address])
        lamports = res["value"] if isinstance(res, dict) else res
        return Decimal(lamports) / _LAMPORTS

    async def get_balances(self, address: str) -> list[AssetBalance]:
        bal = await self.native_balance(address)
        return [AssetBalance("SOL", "solana", bal, 9)]

    async def estimate_native_fee(self) -> FeeEstimate:
        # Base signature fee on Solana is 5000 lamports per signature.
        fee = Decimal(5000) / _LAMPORTS
        return FeeEstimate(fee, "SOL", {"lamports_per_signature": 5000})

    async def send_native(self, secret: str, to_address: str, amount: Decimal) -> str:
        kp = Keypair.from_base58_string(secret.strip())
        to = Pubkey.from_string(to_address)
        lamports = int(amount * _LAMPORTS)

        bh = await _rpc_call("getLatestBlockhash", [{"commitment": "finalized"}])
        blockhash = Hash.from_string(bh["value"]["blockhash"])

        ix = transfer(TransferParams(from_pubkey=kp.pubkey(), to_pubkey=to, lamports=lamports))
        msg = Message.new_with_blockhash([ix], kp.pubkey(), blockhash)
        tx = Transaction([kp], msg, blockhash)
        raw = bytes(tx)
        import base64
        b64 = base64.b64encode(raw).decode()
        sig = await _rpc_call("sendTransaction", [b64, {"encoding": "base64"}])
        return sig

    async def get_signature_status(self, signature: str) -> str | None:
        """Return 'confirmed'/'finalized'/'processed', 'failed', or None if unknown."""
        res = await _rpc_call("getSignatureStatuses", [[signature], {"searchTransactionHistory": True}])
        arr = (res or {}).get("value") if isinstance(res, dict) else None
        if not arr or arr[0] is None:
            return None
        item = arr[0]
        if item.get("err") is not None:
            return "failed"
        return item.get("confirmationStatus") or "processed"


solana_adapter = SolanaAdapter()
