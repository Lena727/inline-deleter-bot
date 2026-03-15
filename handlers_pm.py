"""Private-message settings interface.

Flow:
  /start  →  list of chats where user is admin with can_delete_messages
           →  [Chat Name]  →  settings menu (inline keyboard)
                            →  toggle cmds / toggle own replies
                            →  delay submenu (quick presets + custom FSM)
                            →  ban-list  (add / remove)
                            →  whitelist (add / remove)
"""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.types import ChatMemberAdministrator, ChatMemberOwner

import config
import db

logger = logging.getLogger(__name__)
router = Router(name="pm")

# ── Only handle private chats in this router ──────────────────────────────────
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")


# ─────────────────────────────────────────────────────────────────────────────
#  FSM states
# ─────────────────────────────────────────────────────────────────────────────

class PMState(StatesGroup):
    waiting_delay     = State()
    waiting_ban_add   = State()
    waiting_white_add = State()


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

SEP = "|"   # callback_data field separator (chat_id can be negative)


def _cb(*parts) -> str:
    return SEP.join(str(p) for p in parts)


def _parse(data: str) -> list[str]:
    return data.split(SEP)


async def _user_can_manage(bot: Bot, chat_id: int, user_id: int) -> bool:
    """True if user is chat owner OR admin with can_delete_messages."""
    if user_id == config.OWNER_ID:
        # bot owner can manage any chat
        try:
            await bot.get_chat(chat_id)   # just verify bot is still in it
            return True
        except Exception:
            return False
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        if isinstance(member, ChatMemberOwner):
            return True
        if isinstance(member, ChatMemberAdministrator):
            return bool(member.can_delete_messages)
        return False
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  Keyboards
# ─────────────────────────────────────────────────────────────────────────────

async def _chats_kb(bot: Bot, user_id: int) -> InlineKeyboardMarkup | None:
    """Build a keyboard listing chats the user can manage."""
    known = await db.get_known_chats()
    buttons: list[InlineKeyboardButton] = []
    for chat in known:
        cid = chat["chat_id"]
        if await _user_can_manage(bot, cid, user_id):
            title = chat["chat_title"] or str(cid)
            buttons.append(
                InlineKeyboardButton(text=f"💬 {title}", callback_data=_cb("chat", cid))
            )
    if not buttons:
        return None
    # 1 button per row
    return InlineKeyboardMarkup(inline_keyboard=[[b] for b in buttons])


async def _settings_kb(chat_id: int) -> InlineKeyboardMarkup:
    s       = await db.get_settings(chat_id)
    banned  = await db.get_banned_bots(chat_id)
    wlisted = await db.get_whitelisted_bots(chat_id)

    cmd_icon  = "✅" if s["delete_commands"] else "❌"
    own_icon  = "✅" if s["delete_own"]      else "❌"

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"⏱ Задержка: {s['delete_delay']} с.",
            callback_data=_cb("delay_menu", chat_id),
        )],
        [InlineKeyboardButton(
            text=f"🗑 Удалять команды: {cmd_icon}",
            callback_data=_cb("toggle_cmds", chat_id),
        )],
        [InlineKeyboardButton(
            text=f"🤖 Удалять ответы бота: {own_icon}",
            callback_data=_cb("toggle_own", chat_id),
        )],
        [
            InlineKeyboardButton(
                text=f"🚫 Бан-лист ({len(banned)})",
                callback_data=_cb("ban_menu", chat_id),
            ),
            InlineKeyboardButton(
                text=f"✅ Белый список ({len(wlisted)})",
                callback_data=_cb("white_menu", chat_id),
            ),
        ],
        [InlineKeyboardButton(text="← К списку чатов", callback_data="back_chats")],
    ])


def _delay_kb(chat_id: int) -> InlineKeyboardMarkup:
    presets = [3, 10, 30, 60, 300, 600, 1800, 3600]
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for v in presets:
        label = f"{v}с" if v < 60 else (f"{v//60}м" if v < 3600 else "1ч")
        row.append(InlineKeyboardButton(
            text=label, callback_data=_cb("delay_set", chat_id, v)
        ))
        if len(row) == 4:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(
        text="✏️ Своё значение", callback_data=_cb("delay_custom", chat_id)
    )])
    rows.append([InlineKeyboardButton(
        text="← Назад", callback_data=_cb("settings", chat_id)
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _bot_list_kb(
    chat_id: int, bots: set[str], add_cb: str, del_prefix: str, back_cb: str
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for username in sorted(bots):
        rows.append([InlineKeyboardButton(
            text=f"🗑 @{username}",
            callback_data=_cb(del_prefix, chat_id, username),
        )])
    rows.append([InlineKeyboardButton(text="➕ Добавить", callback_data=_cb(add_cb, chat_id))])
    rows.append([InlineKeyboardButton(text="← Назад",    callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ─────────────────────────────────────────────────────────────────────────────
#  /start  —  show chat list
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("start"), StateFilter("*"))
async def pm_start(message: Message, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    kb = await _chats_kb(bot, message.from_user.id)
    if kb is None:
        await message.answer(
            "😕 Нет доступных чатов.\n\n"
            "Добавьте бота в группу как администратора с правом "
            "<b>удаления сообщений</b> — чат появится здесь после "
            "первого сообщения в нём."
        )
        return
    await message.answer("Выберите чат для настройки:", reply_markup=kb)


# ─────────────────────────────────────────────────────────────────────────────
#  Callback: select chat
# ─────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("chat" + SEP))
async def cb_select_chat(call: CallbackQuery, bot: Bot) -> None:
    chat_id = int(_parse(call.data)[1])
    if not await _user_can_manage(bot, chat_id, call.from_user.id):
        await call.answer("⛔ Нет прав.", show_alert=True)
        return

    try:
        chat = await bot.get_chat(chat_id)
        title = chat.title or str(chat_id)
    except Exception:
        title = str(chat_id)

    kb = await _settings_kb(chat_id)
    await call.message.edit_text(f"⚙️ <b>{title}</b>", reply_markup=kb)
    await call.answer()


@router.callback_query(F.data == "back_chats")
async def cb_back_chats(call: CallbackQuery, bot: Bot) -> None:
    kb = await _chats_kb(bot, call.from_user.id)
    if kb is None:
        await call.message.edit_text("😕 Нет доступных чатов.")
    else:
        await call.message.edit_text("Выберите чат для настройки:", reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("settings" + SEP))
async def cb_back_settings(call: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    chat_id = int(_parse(call.data)[1])
    if not await _user_can_manage(bot, chat_id, call.from_user.id):
        await call.answer("⛔ Нет прав.", show_alert=True)
        return
    try:
        chat  = await bot.get_chat(chat_id)
        title = chat.title or str(chat_id)
    except Exception:
        title = str(chat_id)
    kb = await _settings_kb(chat_id)
    await call.message.edit_text(f"⚙️ <b>{title}</b>", reply_markup=kb)
    await call.answer()


# ─────────────────────────────────────────────────────────────────────────────
#  Toggle commands / own replies
# ─────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("toggle_cmds" + SEP))
async def cb_toggle_cmds(call: CallbackQuery, bot: Bot) -> None:
    chat_id = int(_parse(call.data)[1])
    if not await _user_can_manage(bot, chat_id, call.from_user.id):
        await call.answer("⛔ Нет прав.", show_alert=True); return
    s = await db.get_settings(chat_id)
    await db.upsert_settings(chat_id, delete_commands=0 if s["delete_commands"] else 1)
    await call.message.edit_reply_markup(reply_markup=await _settings_kb(chat_id))
    await call.answer()


@router.callback_query(F.data.startswith("toggle_own" + SEP))
async def cb_toggle_own(call: CallbackQuery, bot: Bot) -> None:
    chat_id = int(_parse(call.data)[1])
    if not await _user_can_manage(bot, chat_id, call.from_user.id):
        await call.answer("⛔ Нет прав.", show_alert=True); return
    s = await db.get_settings(chat_id)
    await db.upsert_settings(chat_id, delete_own=0 if s["delete_own"] else 1)
    await call.message.edit_reply_markup(reply_markup=await _settings_kb(chat_id))
    await call.answer()


# ─────────────────────────────────────────────────────────────────────────────
#  Delay submenu
# ─────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("delay_menu" + SEP))
async def cb_delay_menu(call: CallbackQuery, bot: Bot) -> None:
    chat_id = int(_parse(call.data)[1])
    if not await _user_can_manage(bot, chat_id, call.from_user.id):
        await call.answer("⛔ Нет прав.", show_alert=True); return
    s = await db.get_settings(chat_id)
    await call.message.edit_text(
        f"⏱ <b>Задержка удаления via-сообщений</b>\n"
        f"Текущая: <b>{s['delete_delay']} с.</b>\n\n"
        f"Диапазон: 3 – 3 600 с. Выберите или введите своё:",
        reply_markup=_delay_kb(chat_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("delay_set" + SEP))
async def cb_delay_set(call: CallbackQuery, bot: Bot) -> None:
    parts   = _parse(call.data)
    chat_id = int(parts[1])
    delay   = int(parts[2])
    if not await _user_can_manage(bot, chat_id, call.from_user.id):
        await call.answer("⛔ Нет прав.", show_alert=True); return
    await db.upsert_settings(chat_id, delete_delay=delay)
    await call.answer(f"✅ Задержка: {delay} с.", show_alert=False)
    await call.message.edit_reply_markup(reply_markup=_delay_kb(chat_id))


@router.callback_query(F.data.startswith("delay_custom" + SEP))
async def cb_delay_custom(call: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    chat_id = int(_parse(call.data)[1])
    if not await _user_can_manage(bot, chat_id, call.from_user.id):
        await call.answer("⛔ Нет прав.", show_alert=True); return
    await state.set_state(PMState.waiting_delay)
    await state.update_data(chat_id=chat_id, settings_msg_id=call.message.message_id)
    await call.message.answer("Введите задержку в секундах (3 – 3 600):")
    await call.answer()


@router.message(PMState.waiting_delay)
async def pm_receive_delay(message: Message, bot: Bot, state: FSMContext) -> None:
    data    = await state.get_data()
    chat_id = data["chat_id"]
    raw     = (message.text or "").strip()
    if not raw.isdigit() or not (3 <= int(raw) <= 3600):
        await message.answer("❌ Введите целое число от 3 до 3 600.")
        return
    delay = int(raw)
    await db.upsert_settings(chat_id, delete_delay=delay)
    await state.clear()
    await message.answer(f"✅ Задержка установлена: <b>{delay} с.</b>")
    # Refresh the settings menu in the original message
    try:
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=data["settings_msg_id"],
            text=f"⏱ <b>Задержка удаления via-сообщений</b>\n"
                 f"Текущая: <b>{delay} с.</b>\n\n"
                 f"Диапазон: 3 – 3 600 с. Выберите или введите своё:",
            reply_markup=_delay_kb(chat_id),
        )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  Ban-list submenu
# ─────────────────────────────────────────────────────────────────────────────

async def _render_ban_menu(call: CallbackQuery, chat_id: int) -> None:
    banned = await db.get_banned_bots(chat_id)
    text   = "🚫 <b>Бан-лист</b> (мгновенное удаление)\n" + (
        "\n".join(f"• @{u}" for u in sorted(banned)) if banned else "Список пуст."
    )
    kb = _bot_list_kb(
        chat_id, banned,
        add_cb="ban_add", del_prefix="ban_del",
        back_cb=_cb("settings", chat_id),
    )
    await call.message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data.startswith("ban_menu" + SEP))
async def cb_ban_menu(call: CallbackQuery, bot: Bot) -> None:
    chat_id = int(_parse(call.data)[1])
    if not await _user_can_manage(bot, chat_id, call.from_user.id):
        await call.answer("⛔ Нет прав.", show_alert=True); return
    await _render_ban_menu(call, chat_id)
    await call.answer()


@router.callback_query(F.data.startswith("ban_add" + SEP))
async def cb_ban_add(call: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    chat_id = int(_parse(call.data)[1])
    if not await _user_can_manage(bot, chat_id, call.from_user.id):
        await call.answer("⛔ Нет прав.", show_alert=True); return
    await state.set_state(PMState.waiting_ban_add)
    await state.update_data(chat_id=chat_id, list_msg_id=call.message.message_id)
    await call.message.answer("Введите @username бота для бана:")
    await call.answer()


@router.message(PMState.waiting_ban_add)
async def pm_receive_ban(message: Message, bot: Bot, state: FSMContext) -> None:
    data     = await state.get_data()
    chat_id  = data["chat_id"]
    username = (message.text or "").strip().lstrip("@").lower()
    if not username:
        await message.answer("❌ Введите корректный @username."); return

    # Remove from whitelist if present
    await db.remove_whitelisted_bot(chat_id, username)
    added = await db.add_banned_bot(chat_id, username)
    await state.clear()
    note = " (удалён из белого списка)" if not added else ""
    await message.answer(
        f"🚫 @{username} добавлен в бан-лист{note}." if added else
        f"⚠️ @{username} уже в бан-листе."
    )
    # Refresh list message
    try:
        banned = await db.get_banned_bots(chat_id)
        text   = "🚫 <b>Бан-лист</b> (мгновенное удаление)\n" + (
            "\n".join(f"• @{u}" for u in sorted(banned)) if banned else "Список пуст."
        )
        kb = _bot_list_kb(chat_id, banned, "ban_add", "ban_del", _cb("settings", chat_id))
        await bot.edit_message_text(
            chat_id=message.chat.id, message_id=data["list_msg_id"],
            text=text, reply_markup=kb,
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("ban_del" + SEP))
async def cb_ban_del(call: CallbackQuery, bot: Bot) -> None:
    parts    = _parse(call.data)
    chat_id  = int(parts[1])
    username = parts[2]
    if not await _user_can_manage(bot, chat_id, call.from_user.id):
        await call.answer("⛔ Нет прав.", show_alert=True); return
    await db.remove_banned_bot(chat_id, username)
    await call.answer(f"✅ @{username} удалён из бан-листа.")
    await _render_ban_menu(call, chat_id)


# ─────────────────────────────────────────────────────────────────────────────
#  Whitelist submenu
# ─────────────────────────────────────────────────────────────────────────────

async def _render_white_menu(call: CallbackQuery, chat_id: int) -> None:
    wlisted = await db.get_whitelisted_bots(chat_id)
    text    = "✅ <b>Белый список</b> (никогда не удалять)\n" + (
        "\n".join(f"• @{u}" for u in sorted(wlisted)) if wlisted else "Список пуст."
    )
    kb = _bot_list_kb(
        chat_id, wlisted,
        add_cb="white_add", del_prefix="white_del",
        back_cb=_cb("settings", chat_id),
    )
    await call.message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data.startswith("white_menu" + SEP))
async def cb_white_menu(call: CallbackQuery, bot: Bot) -> None:
    chat_id = int(_parse(call.data)[1])
    if not await _user_can_manage(bot, chat_id, call.from_user.id):
        await call.answer("⛔ Нет прав.", show_alert=True); return
    await _render_white_menu(call, chat_id)
    await call.answer()


@router.callback_query(F.data.startswith("white_add" + SEP))
async def cb_white_add(call: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    chat_id = int(_parse(call.data)[1])
    if not await _user_can_manage(bot, chat_id, call.from_user.id):
        await call.answer("⛔ Нет прав.", show_alert=True); return
    await state.set_state(PMState.waiting_white_add)
    await state.update_data(chat_id=chat_id, list_msg_id=call.message.message_id)
    await call.message.answer("Введите @username бота для белого списка:")
    await call.answer()


@router.message(PMState.waiting_white_add)
async def pm_receive_white(message: Message, bot: Bot, state: FSMContext) -> None:
    data     = await state.get_data()
    chat_id  = data["chat_id"]
    username = (message.text or "").strip().lstrip("@").lower()
    if not username:
        await message.answer("❌ Введите корректный @username."); return

    # Remove from ban-list if present
    was_banned = await db.remove_banned_bot(chat_id, username)
    added      = await db.add_whitelisted_bot(chat_id, username)
    await state.clear()
    note = " (удалён из бан-листа)" if was_banned else ""
    await message.answer(
        f"✅ @{username} добавлен в белый список{note}." if added else
        f"⚠️ @{username} уже в белом списке."
    )
    try:
        wlisted = await db.get_whitelisted_bots(chat_id)
        text    = "✅ <b>Белый список</b> (никогда не удалять)\n" + (
            "\n".join(f"• @{u}" for u in sorted(wlisted)) if wlisted else "Список пуст."
        )
        kb = _bot_list_kb(chat_id, wlisted, "white_add", "white_del", _cb("settings", chat_id))
        await bot.edit_message_text(
            chat_id=message.chat.id, message_id=data["list_msg_id"],
            text=text, reply_markup=kb,
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("white_del" + SEP))
async def cb_white_del(call: CallbackQuery, bot: Bot) -> None:
    parts    = _parse(call.data)
    chat_id  = int(parts[1])
    username = parts[2]
    if not await _user_can_manage(bot, chat_id, call.from_user.id):
        await call.answer("⛔ Нет прав.", show_alert=True); return
    await db.remove_whitelisted_bot(chat_id, username)
    await call.answer(f"✅ @{username} удалён из белого списка.")
    await _render_white_menu(call, chat_id)
