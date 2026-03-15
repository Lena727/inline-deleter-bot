"""Entry point — webhook (production) or long-polling (dev/no public IP)."""
from __future__ import annotations

import logging
import ssl
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

import config
import db
from handlers import router
from handlers_pm import router as pm_router
from middlewares import DeleteCommandsMiddleware, TrackChatsMiddleware

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

dp = Dispatcher(storage=MemoryStorage())
dp.include_router(pm_router)
dp.include_router(router)
dp.message.middleware(TrackChatsMiddleware())
dp.message.middleware(DeleteCommandsMiddleware())


# ── Startup / shutdown ────────────────────────────────────────────────────────

async def on_startup() -> None:
    await db.init_db()

    if config.POLLING_MODE:
        # Remove any leftover webhook so polling works
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Mode: long-polling (no WEBHOOK_HOST set)")
        return

    webhook_url = f"{config.WEBHOOK_HOST}{config.WEBHOOK_PATH}"
    kwargs = dict(
        url=webhook_url,
        drop_pending_updates=True,
        allowed_updates=dp.resolve_used_update_types(),
    )
    if config.SSL_CERT:
        kwargs["certificate"] = Path(config.SSL_CERT).open("rb")
        logger.info("Setting webhook with self-signed cert: %s", webhook_url)
    else:
        logger.info("Setting webhook (TLS via nginx): %s", webhook_url)

    await bot.set_webhook(**kwargs)
    info = await bot.get_webhook_info()
    if info.last_error_message:
        logger.warning("Webhook last error: %s", info.last_error_message)
    logger.info("Webhook active → %s  pending=%d", info.url, info.pending_update_count)


async def on_shutdown() -> None:
    if not config.POLLING_MODE:
        logger.info("Removing webhook…")
        await bot.delete_webhook()
    await bot.session.close()


# ── Webhook app ───────────────────────────────────────────────────────────────

def _build_webhook_app():
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
    from aiohttp import web

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=config.WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    return app


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if config.POLLING_MODE:
        import asyncio

        async def _polling() -> None:
            await on_startup()
            try:
                logger.info("Starting long-polling…")
                await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
            finally:
                await on_shutdown()

        asyncio.run(_polling())

    else:
        from aiohttp import web

        ssl_context: ssl.SSLContext | None = None
        if config.SSL_CERT and config.SSL_KEY:
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_context.load_cert_chain(config.SSL_CERT, config.SSL_KEY)
            logger.info("SSL: loaded %s / %s", config.SSL_CERT, config.SSL_KEY)
        else:
            logger.info("SSL: terminated upstream (nginx)")

        web.run_app(
            _build_webhook_app(),
            host=config.WEBAPP_HOST,
            port=config.WEBAPP_PORT,
            ssl_context=ssl_context,
        )
