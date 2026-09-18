"""Central configuration for OXAEL WALLET. All values come from environment."""
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

BRAND_NAME = "OXAEL WALLET"
BRAND_TAGLINE = "Your crypto. Your control."


def _get(key: str, default: str = "") -> str:
    val = os.environ.get(key)
    return val if val not in (None, "") else default


class Settings:
    # database
    MONGO_URL = os.environ["MONGO_URL"]
    DB_NAME = os.environ["DB_NAME"]

    # telegram
    TELEGRAM_TOKEN = _get("TELEGRAM_TOKEN")
    TELEGRAM_WEBHOOK_SECRET = _get("TELEGRAM_WEBHOOK_SECRET")
    PUBLIC_BASE_URL = _get("PUBLIC_BASE_URL").rstrip("/")

    # encryption
    WALLET_ENCRYPTION_KEY = _get("WALLET_ENCRYPTION_KEY")

    # near intents / 1click
    NEAR_INTENTS_BASE = _get("NEAR_INTENTS_BASE", "https://1click.chaindefuser.com").rstrip("/")
    NEAR_INTENTS_JWT = _get("NEAR_INTENTS_JWT")
    NEAR_INTENTS_API_KEY = _get("NEAR_INTENTS_API_KEY")
    NEAR_INTENTS_REFERRAL = _get("NEAR_INTENTS_REFERRAL", "oxael")

    # explorer / indexer (Etherscan V2 unified API — one key, all EVM chains)
    ETHERSCAN_API_KEY = _get("ETHERSCAN_API_KEY")

    # email (optional)
    EMAIL_PROVIDER = _get("EMAIL_PROVIDER")
    EMAIL_FROM = _get("EMAIL_FROM")
    EMAIL_FROM_NAME = _get("EMAIL_FROM_NAME", BRAND_NAME)
    SMTP_HOST = _get("SMTP_HOST")
    SMTP_PORT = _get("SMTP_PORT")
    SMTP_USERNAME = _get("SMTP_USERNAME")
    SMTP_PASSWORD = _get("SMTP_PASSWORD")

    CORS_ORIGINS = _get("CORS_ORIGINS", "*")

    @property
    def telegram_configured(self) -> bool:
        return bool(self.TELEGRAM_TOKEN)

    @property
    def email_configured(self) -> bool:
        return bool(self.EMAIL_PROVIDER and self.EMAIL_FROM)

    @property
    def near_configured(self) -> bool:
        return bool(self.NEAR_INTENTS_BASE)

    def rpc(self, key: str, default: str) -> str:
        return _get(f"RPC_{key}", default)


settings = Settings()
