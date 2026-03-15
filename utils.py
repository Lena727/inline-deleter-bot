"""Shared helpers used across handlers and middleware."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.types import Message, ChatMemberAdministrator, ChatMemberOwner

import config
import db

logger = logging.getLogger(__name__)


# ── Delayed deletion ──────────────────────────────────────────────────────────

async def _delete_after(bot: Bot, chat_id: int, message_id: int, delay: int) -> None:
    if delay > 0:
        await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
        logger.debug("Deleted msg %d in chat %d (delay=%ds)", message_id, chat_id, delay)
    except Exception as exc:
        logger.debug("Cannot delete msg %d in chat %d: %s", message_id, chat_id, exc)


def schedule_delete(bot: Bot, chat_id: int, message_id: int, delay: int) -> None:
    """Fire-and-forget: schedule message deletion without blocking."""
    asyncio.create_task(_delete_after(bot, chat_id, message_id, delay))


# ── Admin check ───────────────────────────────────────────────────────────────

async def is_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Return True if the user may manage the bot in this chat.

    Rules:
    - Bot owner (OWNER_ID)       — always allowed.
    - Chat owner (ChatMemberOwner) — always allowed.
    - Administrator               — only if can_delete_messages is True.
    """
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


# ── Smart reply ───────────────────────────────────────────────────────────────

async def smart_reply(message: Message, bot: Bot, text: str, **kwargs) -> Message:
    """Send a reply and schedule its deletion based on chat settings.

    - delete_own ON  → deleted after ``delete_delay`` seconds
    - delete_own OFF → deleted after BOT_REPLY_TTL seconds (default 30 s)
    Private chats are never scheduled for deletion.
    """
    sent = await message.answer(text, **kwargs)
    if message.chat.type not in ("group", "supergroup"):
        return sent

    settings = await db.get_settings(message.chat.id)
    delay = settings["delete_delay"] if settings["delete_own"] else config.BOT_REPLY_TTL
    schedule_delete(bot, message.chat.id, sent.message_id, delay)
    return sent
