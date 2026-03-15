"""All message and command handlers."""
from __future__ import annotations

import asyncio
import logging
import os
import sys

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import ChatMemberUpdated, Message

import config
import db
from utils import is_admin, schedule_delete, smart_reply

# conveniences
_get_banned      = db.get_banned_bots
_get_whitelisted = db.get_whitelisted_bots

logger = logging.getLogger(__name__)
router = Router(name="main")


# ─────────────────────────────────────────────────────────────────────────────
#  Guards
# ─────────────────────────────────────────────────────────────────────────────

async def _require_admin(message: Message, bot: Bot) -> bool:
    """Return True if user is admin/owner; send an error and return False otherwise."""
    if message.chat.type == "private":
        await message.answer("❌ Эта команда работает только в группах.")
        return False
    if not await is_admin(bot, message.chat.id, message.from_user.id):
        err = await message.answer("❌ Только администраторы с правом <b>удаления сообщений</b> могут управлять ботом.")
        schedule_delete(bot, message.chat.id, err.message_id, 8)
        schedule_delete(bot, message.chat.id, message.message_id, 3)
        return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
#  Owner-only commands
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("reload"), F.from_user.id == config.OWNER_ID)
async def cmd_reload(message: Message) -> None:
    """Hot-reload: replace the current process with a fresh one."""
    await message.answer("🔄 Перезапуск бота…")
    logger.info("Hot reload triggered by owner (id=%d)", message.from_user.id)
    await asyncio.sleep(0.5)          # let the answer send before we die
    os.execv(sys.executable, [sys.executable] + sys.argv)


@router.message(Command("reload"))   # anyone else
async def cmd_reload_denied(message: Message, bot: Bot) -> None:
    err = await message.answer("🚫 Только владелец бота может перезагружать его.")
    if message.chat.type in ("group", "supergroup"):
        schedule_delete(bot, message.chat.id, err.message_id, 8)
        schedule_delete(bot, message.chat.id, message.message_id, 3)


# ─────────────────────────────────────────────────────────────────────────────
#  Admin commands — per-chat configuration
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("setdelay"))
async def cmd_setdelay(message: Message, bot: Bot) -> None:
    """Set the via-bot deletion delay for this chat (seconds)."""
    if not await _require_admin(message, bot):
        return

    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await smart_reply(
            message, bot,
            "ℹ️ <b>Использование:</b> /setdelay &lt;секунды&gt;\n"
            "Пример: <code>/setdelay 120</code>\n"
            "Диапазон: 3 – 3 600 с.",
        )
        return

    delay = int(parts[1])
    if not (3 <= delay <= 3600):
        await smart_reply(message, bot, "❌ Задержка должна быть от 3 до 3 600 секунд.")
        return

    await db.upsert_settings(message.chat.id, delete_delay=delay)
    await smart_reply(message, bot, f"✅ Задержка удаления via-сообщений: <b>{delay} с.</b>")


@router.message(Command("togglecmds"))
async def cmd_toggle_commands(message: Message, bot: Bot) -> None:
    """Toggle automatic deletion of command messages in this chat."""
    if not await _require_admin(message, bot):
        return
    settings = await db.get_settings(message.chat.id)
    new_val = 0 if settings["delete_commands"] else 1
    await db.upsert_settings(message.chat.id, delete_commands=new_val)
    state = "включено ✅" if new_val else "выключено ❌"
    await smart_reply(message, bot, f"Удаление команд: <b>{state}</b>")


@router.message(Command("toggleown"))
async def cmd_toggle_own(message: Message, bot: Bot) -> None:
    """Toggle auto-deletion of the bot's own replies in this chat."""
    if not await _require_admin(message, bot):
        return
    settings = await db.get_settings(message.chat.id)
    new_val = 0 if settings["delete_own"] else 1
    await db.upsert_settings(message.chat.id, delete_own=new_val)
    state = "включено ✅" if new_val else "выключено ❌"
    await smart_reply(
        message, bot,
        f"Удаление ответов бота: <b>{state}</b>\n"
        f"(TTL = {'<code>delete_delay</code>' if new_val else f'<b>{config.BOT_REPLY_TTL} с.</b>'})",
    )


@router.message(Command("banbot"))
async def cmd_banbot(message: Message, bot: Bot) -> None:
    """Add an inline bot to the instant-delete list for this chat."""
    if not await _require_admin(message, bot):
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await smart_reply(message, bot, "ℹ️ <b>Использование:</b> /banbot @username")
        return

    username = parts[1].lstrip("@").lower()
    added = await db.add_banned_bot(message.chat.id, username)
    if added:
        await smart_reply(
            message, bot,
            f"🚫 <b>@{username}</b> добавлен в бан-лист.\n"
            "Его сообщения будут удаляться <b>мгновенно</b>.",
        )
    else:
        await smart_reply(message, bot, f"⚠️ <b>@{username}</b> уже в бан-листе.")


@router.message(Command("unbanbot"))
async def cmd_unbanbot(message: Message, bot: Bot) -> None:
    """Remove an inline bot from the instant-delete list."""
    if not await _require_admin(message, bot):
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await smart_reply(message, bot, "ℹ️ <b>Использование:</b> /unbanbot @username")
        return

    username = parts[1].lstrip("@").lower()
    removed = await db.remove_banned_bot(message.chat.id, username)
    if removed:
        await smart_reply(message, bot, f"✅ <b>@{username}</b> удалён из бан-листа.")
    else:
        await smart_reply(message, bot, f"⚠️ <b>@{username}</b> не был в бан-листе.")


@router.message(Command("whitebot"))
async def cmd_whitebot(message: Message, bot: Bot) -> None:
    """Add an inline bot to the whitelist — its messages will never be deleted."""
    if not await _require_admin(message, bot):
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await smart_reply(message, bot, "ℹ️ <b>Использование:</b> /whitebot @username")
        return

    username = parts[1].lstrip("@").lower()

    # A bot can't be in both lists at once — remove from ban if present
    was_banned = await db.remove_banned_bot(message.chat.id, username)
    added = await db.add_whitelisted_bot(message.chat.id, username)

    if added:
        note = " (и удалён из бан-листа)" if was_banned else ""
        await smart_reply(
            message, bot,
            f"✅ <b>@{username}</b> добавлен в белый список{note}.\n"
            "Его сообщения <b>не будут удаляться</b>.",
        )
    else:
        await smart_reply(message, bot, f"⚠️ <b>@{username}</b> уже в белом списке.")


@router.message(Command("unwhitebot"))
async def cmd_unwhitebot(message: Message, bot: Bot) -> None:
    """Remove an inline bot from the whitelist."""
    if not await _require_admin(message, bot):
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await smart_reply(message, bot, "ℹ️ <b>Использование:</b> /unwhitebot @username")
        return

    username = parts[1].lstrip("@").lower()
    removed = await db.remove_whitelisted_bot(message.chat.id, username)
    if removed:
        await smart_reply(
            message, bot,
            f"✅ <b>@{username}</b> удалён из белого списка.\n"
            f"Теперь его сообщения будут удаляться по общему таймеру.",
        )
    else:
        await smart_reply(message, bot, f"⚠️ <b>@{username}</b> не был в белом списке.")


@router.message(Command("chatstatus"))
async def cmd_chat_status(message: Message, bot: Bot) -> None:
    """Show current settings for this chat."""
    if not await _require_admin(message, bot):
        return
    settings    = await db.get_settings(message.chat.id)
    banned      = await db.get_banned_bots(message.chat.id)
    whitelisted = await db.get_whitelisted_bots(message.chat.id)

    def fmt(bots: set[str]) -> str:
        return "\n".join(f"  • @{u}" for u in sorted(bots)) if bots else "  нет"

    text = (
        "⚙️ <b>Настройки чата</b>\n\n"
        f"⏱ Задержка удаления via: <b>{settings['delete_delay']} с.</b>\n"
        f"🗑 Удалять команды: <b>{'да' if settings['delete_commands'] else 'нет'}</b>\n"
        f"🤖 Удалять ответы бота: <b>{'да' if settings['delete_own'] else 'нет'}</b>\n\n"
        f"🚫 <b>Бан-лист</b> (мгновенное удаление):\n{fmt(banned)}\n\n"
        f"✅ <b>Белый список</b> (никогда не удалять):\n{fmt(whitelisted)}"
    )
    await smart_reply(message, bot, text)


# ─────────────────────────────────────────────────────────────────────────────
#  Help / start
# ─────────────────────────────────────────────────────────────────────────────

HELP_TEXT = (
    "🤖 <b>Inline Deleter Bot</b>\n\n"
    "<b>Команды администратора (только в группах):</b>\n"
    "/setdelay &lt;сек&gt; — задержка удаления via-сообщений\n"
    "/togglecmds — вкл/выкл удаление команд пользователей\n"
    "/toggleown — вкл/выкл автоудаление ответов бота\n\n"
    "🚫 <b>Бан-лист</b> (мгновенное удаление):\n"
    "/banbot @username — добавить бота в бан\n"
    "/unbanbot @username — убрать из бана\n\n"
    "✅ <b>Белый список</b> (никогда не удалять):\n"
    "/whitebot @username — добавить бота в белый список\n"
    "/unwhitebot @username — убрать из белого списка\n\n"
    "/chatstatus — текущие настройки чата\n\n"
    "<b>Только для владельца:</b>\n"
    "/reload — горячая перезагрузка процесса бота\n\n"
    "<i>Приоритет: белый список &gt; бан-лист &gt; общий таймер</i>"
)


@router.message(Command("help"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_help(message: Message, bot: Bot) -> None:
    sent = await message.answer(HELP_TEXT)
    settings = await db.get_settings(message.chat.id)
    delay = settings["delete_delay"] if settings["delete_own"] else 60
    schedule_delete(bot, message.chat.id, sent.message_id, delay)


# ─────────────────────────────────────────────────────────────────────────────
#  Track bot membership in chats
# ─────────────────────────────────────────────────────────────────────────────

@router.my_chat_member()
async def on_bot_membership_change(event: ChatMemberUpdated) -> None:
    """Track every group/supergroup the bot joins or leaves."""
    if event.chat.type not in ("group", "supergroup"):
        return
    new_status = event.new_chat_member.status
    if new_status in ("member", "administrator"):
        await db.upsert_known_chat(
            event.chat.id,
            event.chat.title or "",
            event.chat.username or "",
        )
        logger.info("Bot joined chat %d (%s)", event.chat.id, event.chat.title)
    elif new_status in ("left", "kicked", "restricted"):
        await db.remove_known_chat(event.chat.id)
        logger.info("Bot left chat %d (%s)", event.chat.id, event.chat.title)


# ─────────────────────────────────────────────────────────────────────────────
#  Core: via-bot message handler
# ─────────────────────────────────────────────────────────────────────────────

@router.message(F.via_bot)
async def handle_via_message(message: Message, bot: Bot) -> None:
    chat_id = message.chat.id
    via_username = (message.via_bot.username or "").lower()

    # Keep known_chats table fresh (title may change)
    if message.chat.type in ("group", "supergroup"):
        await db.upsert_known_chat(
            chat_id,
            message.chat.title or "",
            message.chat.username or "",
        )

    # ── 1. Whitelist check — highest priority, do nothing ─────────────────────
    whitelisted = await db.get_whitelisted_bots(chat_id)
    if via_username in whitelisted:
        logger.debug(
            "Whitelist pass: @%s msg=%d chat=%d", via_username, message.message_id, chat_id
        )
        return

    # ── 2. Banlist check — instant delete ─────────────────────────────────────
    banned = await db.get_banned_bots(chat_id)
    if via_username in banned:
        logger.info(
            "Instant delete (banned): @%s msg=%d chat=%d",
            via_username, message.message_id, chat_id,
        )
        schedule_delete(bot, chat_id, message.message_id, 0)
        return

    # ── 3. Default — delete after configured delay ────────────────────────────
    settings = await db.get_settings(chat_id)
    delay = settings["delete_delay"]
    logger.info(
        "Schedule delete: via @%s msg=%d chat=%d delay=%ds",
        via_username, message.message_id, chat_id, delay,
    )
    schedule_delete(bot, chat_id, message.message_id, delay)
