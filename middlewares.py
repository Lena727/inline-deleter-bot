"""Middlewares for the bot."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot
from aiogram.types import Message

import db
from utils import schedule_delete


class DeleteCommandsMiddleware(BaseMiddleware):
    """After any handler runs, remove the triggering command message if the
    chat has ``delete_commands`` enabled.
    """

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        result = await handler(event, data)

        if (
            event.chat.type in ("group", "supergroup")
            and event.text
            and event.text.startswith("/")
            and not event.via_bot
        ):
            settings = await db.get_settings(event.chat.id)
            if settings["delete_commands"]:
                bot: Bot = data["bot"]
                schedule_delete(bot, event.chat.id, event.message_id, 3)

        return result


class TrackChatsMiddleware(BaseMiddleware):
    """Record every group/supergroup the bot sees a message in.

    Stores chat_id + title so the PM settings menu can list them.
    Also handles bot removal from known_chats when kicked.
    """

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        if event.chat.type in ("group", "supergroup"):
            await db.upsert_known_chat(
                chat_id=event.chat.id,
                title=event.chat.title or str(event.chat.id),
                username=event.chat.username or "",
            )
        return await handler(event, data)
