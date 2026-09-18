# OXAEL WALLET — Product Requirements & Build Log

## Original problem statement
Build a NEW, independent, production-oriented **Telegram-only** custodial crypto
wallet (spec name "CIPHERWALLET", **rebranded by the user to OXAEL WALLET**,
tagline "Your crypto. Your control."). No frontend / React / Vercel / web
dashboard. Deployment target: Render + MongoDB Atlas. Emergent is dev-only.

## User choices (verbatim intent)
- Brand: **OXAEL WALLET** — "Your crypto. Your control." Bot: **@oxael_bot**.
- Native support: **all EVM chains** (Ethereum, Base, Arbitrum, Optimism,
  Polygon, BNB, Avalanche) + **Solana**.
- Cross-chain swaps via **NEAR Intents 1Click**.
- **No email needed** (module built but dormant/optional).
- One-click multi-chain wallet creation; EVM key = gas account across all EVM.
- Privacy-focused; explorer (blockscan) links on transactions.
- Live Telegram verified on Render; backend logic tested on Emergent.

## Architecture
FastAPI (webhook + health only) → Telegram Bot API (sole UI) · MongoDB (Motor,
loop-safe) · NEAR Intents 1Click · modular chain adapters · AES-256-GCM key
encryption · capability registry. Structure under `/app/backend/` per spec
(telegram/, wallets/, chains/, transactions/, intents/, assets/, security/,
services/, utils/, tests/). Deployment: Dockerfile, docker-compose.yml,
render.yaml, runtime.txt, .env.example, README.

## User persona
Crypto-native Telegram users who want a fast, private, multi-chain wallet inside
Telegram without installing a separate app, plus one-click cross-chain swaps.

## Core requirements (static)
Telegram-only UX · custodial AES-256-GCM key storage (never logged/exported) ·
strict per-`telegram_user_id` isolation (no IDOR) · secure wallet gen/import ·
balances · receive + QR · native send with explicit confirmation, idempotency &
EVM nonce coordination · transaction state machine · history with explorer links
· address book · NEAR Intents swap layer with dynamic capability discovery ·
optional provider-independent email · webhook auth · secret redaction · tests.

## Implemented (2026-06)
- ✅ AES-256-GCM versioned encryption + tamper detection (`security/encryption.py`)
- ✅ Log redaction filter for secrets/token/JWT (`security/redaction.py`)
- ✅ Capability registry + network/token catalog (`assets/`)
- ✅ EVM adapter: gen/import/balance/token-balance/fee/native-send + nonce lock
- ✅ Solana adapter: gen/import/balance/fee/native-send (solders + JSON-RPC)
- ✅ Wallet service: one-click EVM+Solana wallet, import, multi-wallet, rename,
  switch, delete — all scoped by telegram_user_id
- ✅ Transaction service: idempotent creation, atomic claim, state machine,
  history + explorer links
- ✅ NEAR Intents 1Click client: tokens/quote/status + dynamic network discovery
- ✅ Telegram UX: onboarding, security notice, home w/ balances+USD, send,
  receive+QR, swap, history (paginated), wallets, settings, networks, address
  book, help — inline keyboards + edited-message navigation
- ✅ Optional email service (verification + idempotent welcome, hashed tokens)
- ✅ FastAPI server: /api/health, authenticated webhook (constant-time compare),
  email verify callback; auto webhook registration on boot
- ✅ Dockerfile, docker-compose, render.yaml, runtime.txt, .env.example, README
- ✅ Test suite: 43 tests passing (encryption, wallets, isolation, capabilities,
  state machine, idempotency, redaction, intents, email, health, webhook auth)
- ✅ Live webhook registered to preview URL; @oxael_bot responding

## Implemented (2026-06) — session 2 (full Web3 wallet upgrade)
- ✅ FIXED core send bug: callback router short-circuited `send|net|…`,
  `recv|…`, `hist|…` via `head in routes`; now guarded with `len(parts)==1`.
  Picking a network now advances to the asset picker (verified live).
- ✅ ERC-20 token sending end-to-end (USDC/USDT) on every EVM chain — real
  `transfer(address,uint256)`, correct per-token decimals, token+gas balance
  checks, nonce coordination, receipt tracking.
- ✅ Native + token receive watcher (was native-only) → incoming notifications
  for coins AND tokens; snapshot-based dedup.
- ✅ Confirmation watcher: broadcasted → confirmed/failed with Telegram pings.
- ✅ Swap: NEAR Intents 1Click (unauthenticated, no JWT per user), live quote,
  deposit address, one-tap "Pay from wallet", status watcher.
- ✅ Duplicate wallet prevention with rich message (name + short address +
  [View Wallets]/[Cancel]); `find_wallet_by_address` locator.
- ✅ Wallet naming on create AND import (name-first flow) + rename.
- ✅ /track command + per-wallet tracking toggle (track_enabled); watcher honours it.
- ✅ New commands: /wallet /create /import /balance /track; full command menu
  registered via setMyCommands (13 commands, verified).
- ✅ Capability registry extended: token_send_supported, tracking_supported.
- ✅ .env.example (no secrets) added; .gitignore negation so it is committable
  while backend/.env stays ignored.
- ✅ Tests: 52 passing (added test_features.py: send-fix regression, token/native
  send wiring, duplicate prevention, naming, tracking, capabilities, command menu;
  redaction-in-logs now actively asserts the bot token never appears in logs).
- ✅ Live verified via real webhook: @oxael_bot responds, wallet create + send
  network→asset(USDC) flow advances correctly.

### Real capability matrix (no faking)
- Direct native send: Ethereum, Base, Arbitrum, Optimism, Polygon, BNB, Avalanche, Solana.
- Direct ERC-20 send: all 7 EVM chains (USDC/USDT configured; extendable via catalog).
- Swap routing: all NEAR Intents/1Click chains, discovered dynamically.
- Solana SPL-token send: NOT implemented (flagged token_send_supported=False).

## Implemented (2026-06) — session 3 (balance engine + discovery + swap assets)
- ✅ ROOT-CAUSE FIX for "imported wallet → error → no balances": chains had a
  single hard-coded RPC; `eth.llamarpc.com`→525 and `polygon-rpc.com`→401 from
  the server made Ethereum/Polygon reads throw. Rebuilt `chains/evm.py` with a
  **multi-RPC fallback engine** (publicnode/drpc/ankr + `RPC_<NET>` override
  tried first), a distinct `RpcUnavailable` error, and a remembered last-good
  endpoint. Verified: all 7 EVM chains now return real balances.
- ✅ Portfolio engine returns per-network **status** (ok/unavailable); one network
  provider outage no longer fails the whole wallet — other networks still show.
- ✅ Automatic **ERC-20 token discovery** via Etherscan V2 unified API (one
  `ETHERSCAN_API_KEY` for all chains) → `assets/discovery.py`. Discovered tokens
  are verified on-chain (balanceOf) before display; graceful `[]` fallback with
  no key. Unknown/spam tokens handled (blank metadata, "Unknown", ⚠️ unverified).
- ✅ Prices only applied where a reliable coingecko id exists; discovered tokens
  show "price unavailable" and never inflate the portfolio total.
- ✅ New Web3 UI: Portfolio home (per-network outage note), 🪙 **Tokens** view,
  refreshed main menu (Send/Receive/Swap/Tokens/Activity/Track/Wallets/Settings).
- ✅ /send now lists **discovered held tokens** (on-chain metadata) in addition
  to the curated catalog — any held token is sendable, chain-isolated.
- ✅ Swap: **source-asset selection** (pay USDC/native, not only native) +
  correct destination-chain recipient address + one-tap token/native deposit
  with balance & gas revalidation; destinations limited to receivable networks.
- ✅ Real receipt data (block/gas/fee) persisted on confirmation; tx state
  machine gained `estimating`/`replaced`.
- ✅ Token catalog expanded to USDC/USDT/DAI/WETH across the 7 EVM chains.
- ✅ Tests: 65 passing (added test_balance_engine.py: RPC fallback order,
  fallback-to-next, RpcUnavailable, discovery key-gating, partial-outage
  portfolio, no-fake-price total, chain isolation).

### Explorer / RPC providers
- RPC (primary): publicnode.com, drpc.org, ankr, chain-official — per chain, with fallback.
- Explorer/indexer: Etherscan V2 unified API (chainid) for token discovery + history-ready.

## Backlog / remaining (prioritized)
- P2: Solana SPL token send; UTXO signing (BTC/LTC/DOGE); Tron/XRP/TON adapters.
- P2: On-chain history indexing beyond OXAEL-originated txs.
- P2: Per-user envelope encryption / KMS; key-rotation routine.
- P2: DAI/WETH token entries per chain (catalog is ready, just add rows).

## Next tasks
- Push to GitHub via the Emergent "Save to Github" feature → repo
  a4m3d/oxaelwallet-v1, then deploy on Render with MongoDB Atlas; set
  PUBLIC_BASE_URL to the Render URL (webhook auto-registers on boot).

