import os
from dotenv import load_dotenv

load_dotenv()

# ── Identity ──────────────────────────────────────────────────────────────────
OWNER_ID: int = 1336934902
BOT_TOKEN: str = os.environ["BOT_TOKEN"]

# ── Webhook ───────────────────────────────────────────────────────────────────
WEBHOOK_HOST: str = os.environ["WEBHOOK_HOST"].rstrip("/")
WEBHOOK_PATH: str = "/webhook"
WEBAPP_HOST: str = "0.0.0.0"
WEBAPP_PORT: int = int(os.getenv("PORT", "8443"))

# SSL — leave empty when nginx terminates TLS
SSL_CERT: str = os.getenv("SSL_CERT", "")
SSL_KEY: str = os.getenv("SSL_KEY", "")

# ── Storage ───────────────────────────────────────────────────────────────────
DB_PATH: str = os.getenv("DB_PATH", "bot.db")

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_DELAY: int = 60          # via-bot deletion delay (seconds)
BOT_REPLY_TTL: int = 30          # bot's own replies TTL when delete_own is OFF
