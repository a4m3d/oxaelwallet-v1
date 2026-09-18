# OXAEL WALLET — test/access notes

This is a **Telegram-only** bot. There is no web login. "Access" = opening the
bot in Telegram as any Telegram user; each Telegram user id is isolated.

- Bot: **@oxael_bot** (live in the preview environment)
- No username/password accounts. Wallets are created/imported inside the chat.

## Secrets (values live ONLY in backend/.env, never in git)
- TELEGRAM_TOKEN — set (provided by user)
- TELEGRAM_WEBHOOK_SECRET — set
- WALLET_ENCRYPTION_KEY — set (DO NOT rotate; wallets become unrecoverable)
- NEAR_INTENTS_BASE — https://1click.chaindefuser.com (unauthenticated; no JWT)

## Handy test values
- EVM private key for import tests: `0x` + 64 hex (e.g. `0x` + "1"*64) — testnet/dummy only.
- Test telegram user ids used by automated tests are random and cleaned up.

## Webhook
- POST /api/telegram/webhook  (requires header `X-Telegram-Bot-Api-Secret-Token`)
- Health: GET /api/health  → {"status":"ok"}
