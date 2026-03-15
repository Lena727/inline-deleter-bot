"""Entry point — aiohttp webhook server for Telegram."""
from __future__ import annotations

import logging
import ssl
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

import config
import db
from handlers import router
from middlewares import DeleteCommandsMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Bot & Dispatcher ──────────────────────────────────────────────────────────

bot = Bot(
    token=config.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)

dp = Dispatcher()
dp.include_router(router)
dp.message.middleware(DeleteCommandsMiddleware())


# ── Lifecycle hooks ───────────────────────────────────────────────────────────

async def on_startup() -> None:
    await db.init_db()
    logger.info("Database initialised at '%s'", config.DB_PATH)

    webhook_url = f"{config.WEBHOOK_HOST}{config.WEBHOOK_PATH}"

    if config.SSL_CERT:
        cert_path = Path(config.SSL_CERT)
        logger.info("Setting webhook with self-signed cert: %s", webhook_url)
        await bot.set_webhook(
            url=webhook_url,
            certificate=cert_path.open("rb"),
            drop_pending_updates=True,
            allowed_updates=dp.resolve_used_update_types(),
        )
    else:
        logger.info("Setting webhook (TLS handled externally): %s", webhook_url)
        await bot.set_webhook(
            url=webhook_url,
            drop_pending_updates=True,
            allowed_updates=dp.resolve_used_update_types(),
        )

    info = await bot.get_webhook_info()
    logger.info("Webhook info → url=%s pending=%d", info.url, info.pending_update_count)


async def on_shutdown() -> None:
    logger.info("Removing webhook…")
    await bot.delete_webhook()
    await bot.session.close()


# ── Application factory ───────────────────────────────────────────────────────

def build_app() -> web.Application:
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=config.WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    return app


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ssl_context: ssl.SSLContext | None = None

    if config.SSL_CERT and config.SSL_KEY:
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(config.SSL_CERT, config.SSL_KEY)
        logger.info("SSL context loaded from %s / %s", config.SSL_CERT, config.SSL_KEY)
    else:
        logger.info(
            "No SSL_CERT/SSL_KEY set — assuming TLS is terminated upstream (e.g. nginx)"
        )

    web.run_app(
        build_app(),
        host=config.WEBAPP_HOST,
        port=config.WEBAPP_PORT,
        ssl_context=ssl_context,
    )
