"""Static catalog of networks and tokens with their real, implemented capabilities.

Honesty rule: a flag is True ONLY when the corresponding code path is genuinely
implemented in this codebase. Chains reachable only through NEAR Intents routing
get `near_intents_supported=True` but keep native flags False.
"""
from assets.capabilities import Network, Family, Capabilities

_FULL_EVM = Capabilities(
    wallet_generation_supported=True,
    wallet_import_supported=True,
    direct_signing_supported=True,
    direct_send_supported=True,
    token_send_supported=True,
    balance_supported=True,
    receive_supported=True,
    transaction_history_supported=True,
    tracking_supported=True,
    near_intents_supported=True,
    swap_supported=True,
)

_FULL_SOL = Capabilities(
    wallet_generation_supported=True,
    wallet_import_supported=True,
    direct_signing_supported=True,
    direct_send_supported=True,
    token_send_supported=False,
    balance_supported=True,
    receive_supported=True,
    transaction_history_supported=False,
    tracking_supported=True,
    near_intents_supported=True,
    swap_supported=True,
)

# Routed-only chains: available as swap source/destination via NEAR Intents,
# but no native signing/sending implemented here yet.
_ROUTED = Capabilities(near_intents_supported=True, swap_supported=True)


NETWORKS: dict[str, Network] = {
    "ethereum": Network("ethereum", "Ethereum", Family.EVM, "ETH", 18, 1,
                        "https://etherscan.io", "ethereum", _FULL_EVM),
    "base": Network("base", "Base", Family.EVM, "ETH", 18, 8453,
                    "https://basescan.org", "ethereum", _FULL_EVM),
    "arbitrum": Network("arbitrum", "Arbitrum", Family.EVM, "ETH", 18, 42161,
                        "https://arbiscan.io", "ethereum", _FULL_EVM),
    "optimism": Network("optimism", "Optimism", Family.EVM, "ETH", 18, 10,
                        "https://optimistic.etherscan.io", "ethereum", _FULL_EVM),
    "polygon": Network("polygon", "Polygon", Family.EVM, "POL", 18, 137,
                       "https://polygonscan.com", "matic-network", _FULL_EVM),
    "bnb": Network("bnb", "BNB Chain", Family.EVM, "BNB", 18, 56,
                   "https://bscscan.com", "binancecoin", _FULL_EVM),
    "avalanche": Network("avalanche", "Avalanche C-Chain", Family.EVM, "AVAX", 18, 43114,
                         "https://snowtrace.io", "avalanche-2", _FULL_EVM),

    "solana": Network("solana", "Solana", Family.SOLANA, "SOL", 9, None,
                      "https://solscan.io", "solana", _FULL_SOL),

    # Routed-only (NEAR Intents). Native send not implemented — flagged honestly.
    "bitcoin": Network("bitcoin", "Bitcoin", Family.UTXO, "BTC", 8, None,
                       "https://mempool.space", "bitcoin", _ROUTED),
    "litecoin": Network("litecoin", "Litecoin", Family.UTXO, "LTC", 8, None,
                        "https://blockchair.com/litecoin", "litecoin", _ROUTED),
    "dogecoin": Network("dogecoin", "Dogecoin", Family.UTXO, "DOGE", 8, None,
                        "https://blockchair.com/dogecoin", "dogecoin", _ROUTED),
    "tron": Network("tron", "Tron", Family.TRON, "TRX", 6, None,
                    "https://tronscan.org/#", "tron", _ROUTED),
    "xrp": Network("xrp", "XRP Ledger", Family.XRP, "XRP", 6, None,
                   "https://xrpscan.com", "ripple", _ROUTED),
    "near": Network("near", "NEAR", Family.NEAR, "NEAR", 24, None,
                    "https://nearblocks.io", "near", _ROUTED),
    "ton": Network("ton", "TON", Family.TON, "TON", 9, None,
                   "https://tonviewer.com", "the-open-network", _ROUTED),
    "stellar": Network("stellar", "Stellar", Family.STELLAR, "XLM", 7, None,
                       "https://stellar.expert/explorer/public", "stellar", _ROUTED),
    "cardano": Network("cardano", "Cardano", Family.CARDANO, "ADA", 6, None,
                       "https://cardanoscan.io", "cardano", _ROUTED),
}


# ERC-20 tokens with real balance support on their networks.
TOKENS: list[dict] = [
    {"network": "ethereum", "symbol": "USDC", "address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "decimals": 6, "coingecko_id": "usd-coin"},
    {"network": "ethereum", "symbol": "USDT", "address": "0xdAC17F958D2ee523a2206206994597C13D831ec7", "decimals": 6, "coingecko_id": "tether"},
    {"network": "base", "symbol": "USDC", "address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "decimals": 6, "coingecko_id": "usd-coin"},
    {"network": "arbitrum", "symbol": "USDC", "address": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831", "decimals": 6, "coingecko_id": "usd-coin"},
    {"network": "optimism", "symbol": "USDC", "address": "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85", "decimals": 6, "coingecko_id": "usd-coin"},
    {"network": "polygon", "symbol": "USDC", "address": "0x3c499c542cEF5E3811e1192ce70d8cc03d5c3359", "decimals": 6, "coingecko_id": "usd-coin"},
    {"network": "bnb", "symbol": "USDC", "address": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d", "decimals": 18, "coingecko_id": "usd-coin"},
]


def evm_networks() -> list[Network]:
    return [n for n in NETWORKS.values() if n.family == Family.EVM]


def get_network(key: str) -> Network | None:
    return NETWORKS.get(key)


def tokens_for(network_key: str) -> list[dict]:
    return [t for t in TOKENS if t["network"] == network_key]


def token_by_symbol(network_key: str, symbol: str) -> dict | None:
    for t in TOKENS:
        if t["network"] == network_key and t["symbol"].upper() == symbol.upper():
            return t
    return None


def networks_with(cap_attr: str) -> list[Network]:
    return [n for n in NETWORKS.values() if getattr(n.capabilities, cap_attr, False)]
