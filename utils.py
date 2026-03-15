"""Shared helpers."""
from __future__ import annotations

import asyncio
import json
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.types import Message, ChatMemberAdministrator, ChatMemberOwner

import config
import db

logger = logging.getLogger(__name__)

# Telegram error substrings that mean "message is already gone" — not our fault
_ALREADY_GONE = (
    "message to delete not found",
    "message can't be deleted",
    "message is too old",
)

# Telegram error substrings that mean the bot lacks delete rights
_NO_RIGHTS = (
    "not enough rights",
    "bot is not a member",
    "chat not found",
    "bot was kicked",
    "bot was blocked",
    "have no rights",
    "administrator rights",
)


async def _delete_after(bot: Bot, chat_id: int, message_id: int, delay: int) -> None:
    if delay > 0:
        await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
        logger.debug("Deleted msg %d in chat %d (delay=%ds)", message_id, chat_id, delay)

    except TelegramForbiddenError as e:
        # Bot was kicked or lost admin rights — nothing we can do
        logger.warning("No access to chat %d — bot kicked or rights revoked: %s", chat_id, e)

    except TelegramBadRequest as e:
        err = str(e).lower()
        if any(s in err for s in _ALREADY_GONE):
            # Message was already deleted by someone else — that's fine
            logger.debug("Msg %d in chat %d already gone", message_id, chat_id)
        elif any(s in err for s in _NO_RIGHTS):
            logger.warning(
                "Cannot delete msg %d in chat %d — bot lacks delete rights: %s",
                message_id, chat_id, e,
            )
        else:
            logger.warning("Unexpected TelegramBadRequest deleting msg %d: %s", message_id, e)

    except Exception as e:
        logger.warning("Unexpected error deleting msg %d in chat %d: %s", message_id, chat_id, e)


def schedule_delete(bot: Bot, chat_id: int, message_id: int, delay: int) -> None:
    asyncio.create_task(_delete_after(bot, chat_id, message_id, delay))


async def is_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    """True if user is owner, or admin with can_delete_messages."""
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


async def bot_can_delete(bot: Bot, chat_id: int) -> bool:
    """Check whether the bot itself has delete_messages rights in the chat."""
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(chat_id, me.id)
        if isinstance(member, ChatMemberOwner):
            return True
        if isinstance(member, ChatMemberAdministrator):
            return bool(member.can_delete_messages)
        return False
    except Exception:
        return False


async def smart_reply(message: Message, bot: Bot, text: str, **kwargs) -> Message:
    """Reply and optionally schedule self-deletion based on chat settings."""
    sent = await message.answer(text, **kwargs)
    if message.chat.type not in ("group", "supergroup"):
        return sent

    settings = await db.get_settings(message.chat.id)
    if settings["delete_own"]:
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
