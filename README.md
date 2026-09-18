# OXAEL WALLET

**Your crypto. Your control.** — a premium, Telegram-only crypto wallet.

OXAEL WALLET is a **custodial** multi-network crypto wallet that lives entirely
inside Telegram. There is **no web frontend, no React, no Vercel** — Telegram is
the complete user interface. The backend is a FastAPI service that talks to the
Telegram Bot API over a secure webhook, persists to MongoDB, encrypts signing
material with AES-256-GCM, and routes cross-chain swaps through NEAR Intents.

> Telegram bot: **@oxael_bot**

---

## ⚠️ Security model — please read

OXAEL WALLET is **custodial**. The backend stores each wallet's signing material
**encrypted** (AES-256-GCM) and decrypts it *only for the instant needed* to sign
a transaction the user explicitly approves.

**This is not a non-custodial wallet.** Because the server can decrypt signing
material to sign, you should understand:

- Whoever controls the server + `WALLET_ENCRYPTION_KEY` can, in principle, sign.
- Access to a user's Telegram account is effectively access to their wallet.
- Users should secure their Telegram account (password + 2FA).

Secrets are **never** logged, returned via API, sent through Telegram messages,
stored in `callback_data`, or committed to source control.

A future hardening path (KMS/HSM, threshold signing, per-user envelope keys) is
described in [Future work](#future-work).

---

## Architecture

```
Emergent            GitHub            Render                 MongoDB Atlas
(dev only)   →   (source code)  →   (backend + bot)   ↔    (oxael_wallet db)
                                          ↑
                                     Telegram  ← the only user interface
                                          ↑
                                 NEAR Intents / 1Click  (cross-chain routing)
```

- **Telegram** — the entire UX: onboarding, wallets, send, receive, swap,
  history, address book, settings. Inline keyboards + edited messages make it
  feel like a single app, not a message chain.
- **FastAPI** — minimal surface: a webhook, a health probe, and an optional
  email-verification callback. No public wallet endpoints.
- **MongoDB** — a dedicated `oxael_wallet` database (separate from any other
  project). Motor async driver, loop-safe client resolution.
- **NEAR Intents (1Click)** — cross-chain quotes, deposit addresses, status.
- **Chain adapters** — modular per-family adapters with a capability registry.

### Project layout
```
backend/
  server.py            FastAPI app (webhook + health + email verify)
  config.py            env-driven settings
  database.py          Mongo connection + indexes (loop-safe)
  telegram/            bot client, handlers, keyboards, session states
  wallets/             wallet models, service, import, portfolio
  chains/              base + evm + solana adapters
  transactions/        state machine, idempotency, execution service
  intents/             NEAR Intents / 1Click client
  assets/              capability registry + network/token catalog
  security/            AES-256-GCM encryption + log redaction
  services/            provider-independent email service (optional)
  utils/               QR, formatting, pricing
  tests/               automated test suite
Dockerfile  docker-compose.yml  render.yaml  runtime.txt  .env.example
```

---

## Capabilities & network support

Support is declared per network in a **capability registry**
(`assets/capabilities.py` + `assets/catalog.py`). A capability is `True` only
when the code path is genuinely implemented — nothing is faked.

| Tier | Networks | gen | import | balance | receive | send | history | swap |
|------|----------|-----|--------|---------|---------|------|---------|------|
| **Native wallet** | Ethereum, Base, Arbitrum, Optimism, Polygon, BNB Chain, Avalanche | ✅ | ✅ | ✅ | ✅ | ✅ (native) | ✅ (DB) | ✅ |
| **Native wallet** | Solana | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ |
| **Swap routing** | Bitcoin, Litecoin, Dogecoin, Tron, XRP, NEAR, TON, Stellar, Cardano | — | — | — | — | — | — | ✅ (via NEAR Intents) |

- One EVM key = one address across **all** EVM networks (the "one-click EVM
  wallet" / gas account).
- Native **send** currently covers native assets (ETH/BNB/POL/AVAX/SOL). ERC-20
  balances are shown and are swappable via NEAR Intents.
- The swap catalog (assets + destination networks) is **discovered dynamically**
  at swap time — there is **no hardcoded network count**.

---

## Environment variables

Copy `backend/.env.example` → `backend/.env` and fill in. Never commit real
values.

| Variable | Purpose |
|----------|---------|
| `MONGO_URL` | MongoDB Atlas connection string |
| `DB_NAME` | Database name (default `oxael_wallet`) |
| `TELEGRAM_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_WEBHOOK_SECRET` | Secret validated on every webhook request |
| `PUBLIC_BASE_URL` | Public HTTPS base URL (Render). Used to register webhook |
| `WALLET_ENCRYPTION_KEY` | base64 of 32 random bytes. **Do not rotate** or wallets become unrecoverable |
| `NEAR_INTENTS_BASE` | `https://1click.chaindefuser.com` |
| `NEAR_INTENTS_JWT` | 1Click JWT (optional; unauth requests incur a fee) |
| `EMAIL_PROVIDER` / `EMAIL_FROM` / `EMAIL_FROM_NAME` | Optional email channel |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` | SMTP delivery |
| `CORS_ORIGINS` | Comma-separated origins |
| `RPC_<NETWORK>` | Optional per-chain RPC overrides (public defaults used otherwise) |

Generate an encryption key:
```bash
python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"
```

---

## Telegram setup

1. Create a bot with **@BotFather**, copy the token into `TELEGRAM_TOKEN`.
2. Choose a strong `TELEGRAM_WEBHOOK_SECRET`.
3. On startup (with `PUBLIC_BASE_URL` set) the service registers the webhook at
   `{PUBLIC_BASE_URL}/api/telegram/webhook` automatically.
4. Every webhook request is validated against the
   `X-Telegram-Bot-Api-Secret-Token` header.

## MongoDB (Atlas) setup

1. Create a cluster and a database user.
2. Set `MONGO_URL` to the SRV connection string and `DB_NAME=oxael_wallet`.
3. Indexes are created automatically on startup (see `database.ensure_indexes`).

## Email setup (optional)

Email is a **secondary** channel and is never required to use the wallet. When
`EMAIL_PROVIDER=smtp` and SMTP vars are set, verification and one-time welcome
emails are sent. Emails **never** contain wallet secrets. Verification tokens are
stored only as SHA-256 hashes with a 60-minute expiry; welcome emails are
idempotent (sent at most once).

## NEAR Intents

`intents/near_intents.py` wraps the 1Click REST API: `GET /v0/tokens`
(dynamic discovery), `POST /v0/quote` (dry + live), `GET /v0/status`. Credentials
come from env; the JWT is never logged.

---

## Transaction state machine

```
created → awaiting_confirmation → signing → broadcasting → broadcasted
        → pending → confirmed
(any pre-broadcast) → cancelled / expired ;  (failure) → failed
```
Transitions are validated in `transactions/state_machine.py`.

## Idempotency & duplicate-send protection

- Unique index on `(telegram_user_id, idempotency_key)` makes duplicate creation
  impossible.
- Before broadcasting, an **atomic** `findOneAndUpdate` moves the record from a
  pre-signing state to `signing`; a second Telegram button press finds no
  matching record and is safely ignored.
- EVM sends coordinate the pending nonce per `(address, network)` with an
  in-process lock and read the chain's pending nonce.

---

## Running locally

```bash
# With Docker (includes MongoDB)
docker compose up --build      # backend on :8001, mongo on :27017

# Or run the backend directly
cd backend
pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 8001
```
Health check: `GET /api/health` → `{"status":"ok"}`.

## Testing

```bash
cd backend
python -m pytest tests -q
```
Covers: encryption round-trip & tamper detection, wallet generation/import,
address validation & wrong-network rejection, user isolation & ownership,
capability registry, state machine, idempotency & duplicate-send protection,
secret redaction, NEAR Intents client & dynamic discovery, email
validation/verification & duplicate-welcome prevention, health endpoint, and
webhook authentication. Tests use test credentials only and never move real funds.

---

## Render deployment

1. Push this repo to a **new** GitHub repository (see below).
2. Create a **Web Service** on Render (or use `render.yaml` blueprint):
   - **Root Directory:** `backend`
   - **Build:** `pip install -r requirements.txt`
   - **Start:** `uvicorn server:app --host 0.0.0.0 --port $PORT`
   - **Health check path:** `/api/health`
3. Add env vars (secrets) in the Render dashboard.
4. Set `PUBLIC_BASE_URL` to the Render service URL — the webhook registers on
   boot.

## GitHub

This is an independent project. Push to a **new** repository, e.g.
`a4m3d/cipherwallet` or `a4m3d/oxael-wallet`. The `frontend/` directory is
git-ignored and is not part of this Telegram-only project. Never commit `.env`.

---

## Backup & recovery considerations

- Wallets are recoverable **only** while `WALLET_ENCRYPTION_KEY` and the MongoDB
  data both exist. Back up the key securely and independently of the database.
- Rotating `WALLET_ENCRYPTION_KEY` without re-encrypting existing ciphertext
  makes wallets unrecoverable. A key-rotation routine (decrypt-with-old,
  encrypt-with-new, versioned payloads) is the recommended future addition.
- Users importing keys should retain their own backups; deleting a wallet in
  OXAEL removes the stored (encrypted) secret.

## Future work

- KMS/HSM-backed key storage or envelope encryption per user.
- Threshold / MPC signing to remove single-key custody.
- ERC-20 and non-EVM native sends; UTXO signing (BTC/LTC/DOGE).
- On-chain history indexing beyond OXAEL-originated transactions.
- Optional email-based recovery with an explicitly designed threat model.

---

_OXAEL WALLET · Your crypto. Your control._
