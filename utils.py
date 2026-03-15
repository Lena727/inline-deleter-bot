"""Shared helpers."""
from __future__ import annotations

import asyncio
import json
import logging

from aiogram import Bot
from aiogram.types import Message, ChatMemberAdministrator, ChatMemberOwner

import config
import db

logger = logging.getLogger(__name__)


async def _delete_after(bot: Bot, chat_id: int, message_id: int, delay: int) -> None:
    if delay > 0:
        await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
        logger.debug("Deleted msg %d in chat %d (delay=%ds)", message_id, chat_id, delay)
    except Exception as exc:
        logger.debug("Cannot delete msg %d in chat %d: %s", message_id, chat_id, exc)


def schedule_delete(bot: Bot, chat_id: int, message_id: int, delay: int) -> None:
    asyncio.create_task(_delete_after(bot, chat_id, message_id, delay))


async def is_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    if user_id == config.OWNER_ID:
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        if isinstance(member, ChatMemberOwner):
            return True
        if isinstance(member, ChatMemberAdministrator):
            return bool(member.can_delete_messages)
        return False
    except Exception:
        return False


async def smart_reply(message: Message, bot: Bot, text: str, **kwargs) -> Message:
    """Send a reply; schedule its deletion based on chat settings + default policy."""
    sent = await message.answer(text, **kwargs)
    if message.chat.type not in ("group", "supergroup"):
        return sent

    settings = await db.get_settings(message.chat.id)
    if settings["delete_own"]:
        # Use default policy delay (if it's a delay type), else BOT_REPLY_TTL
        policy = await db.get_default_policy(message.chat.id)
        if policy["type"] == "delay":
            cfg   = json.loads(policy.get("config") or "{}")
            delay = cfg.get("delay", config.BOT_REPLY_TTL)
        else:
            delay = config.BOT_REPLY_TTL
    else:
        delay = config.BOT_REPLY_TTL

    schedule_delete(bot, message.chat.id, sent.message_id, delay)
    return sent
