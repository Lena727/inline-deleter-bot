"""Private-message settings interface — Policy Engine edition.

/start  →  chat list (where user is admin with can_delete_messages)
         →  chat menu
              ├─ ⚙️ General (togglecmds / toggleown)
              ├─ 📋 Policies
              │    ├─ list → policy detail (set default / delete)
              │    └─ ➕ New policy (FSM: name → type → config)
              └─ 🤖 Bots
                   ├─ list with current policy → reassign / unassign
                   └─ ➕ Assign bot (FSM: username → pick policy)
"""
from __future__ import annotations

import json
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
from engine import (
    POLICY_TYPES,
    PolicyParseError,
    describe_policy,
    format_tz_offset,
    parse_policy_args,
)

logger = logging.getLogger(__name__)
router = Router(name="pm")
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")

SEP = "|"


def _cb(*parts) -> str:
    return SEP.join(str(p) for p in parts)


def _p(data: str) -> list[str]:
    return data.split(SEP)


# ─────────────────────────────────────────────────────────────────────────────
#  FSM
# ─────────────────────────────────────────────────────────────────────────────

class PM(StatesGroup):
    new_policy_name   = State()
    new_policy_config = State()   # data: chat_id, ptype, nav_msg_id
    bot_assign_name   = State()   # data: chat_id, policy_id, nav_msg_id
    bot_assign_pick   = State()   # data: chat_id, bot_username, nav_msg_id


# ─────────────────────────────────────────────────────────────────────────────
#  Auth helper
# ─────────────────────────────────────────────────────────────────────────────

async def _can_manage(bot: Bot, chat_id: int, user_id: int) -> bool:
    if user_id == config.OWNER_ID:
        try:
            await bot.get_chat(chat_id)
            return True
        except Exception:
            return False
    try:
        m = await bot.get_chat_member(chat_id, user_id)
        if isinstance(m, ChatMemberOwner):
            return True
        if isinstance(m, ChatMemberAdministrator):
            return bool(m.can_delete_messages)
        return False
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  Keyboard builders
# ─────────────────────────────────────────────────────────────────────────────

async def _kb_chats(bot: Bot, user_id: int) -> InlineKeyboardMarkup | None:
    known = await db.get_known_chats()
    btns  = [
        InlineKeyboardButton(text=f"💬 {c['chat_title']}", callback_data=_cb("chat", c["chat_id"]))
        for c in known if await _can_manage(bot, c["chat_id"], user_id)
    ]
    return InlineKeyboardMarkup(inline_keyboard=[[b] for b in btns]) if btns else None


async def _kb_chat_menu(chat_id: int) -> InlineKeyboardMarkup:
    s   = await db.get_settings(chat_id)
    cmd = "✅" if s["delete_commands"] else "❌"
    own = "✅" if s["delete_own"]      else "❌"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🗑 Удалять команды: {cmd}",    callback_data=_cb("tcmds",  chat_id))],
        [InlineKeyboardButton(text=f"🤖 Удалять ответы бота: {own}", callback_data=_cb("town",   chat_id))],
        [InlineKeyboardButton(text="📋 Политики",                    callback_data=_cb("plist",  chat_id))],
        [InlineKeyboardButton(text="🤖 Боты",                        callback_data=_cb("blist",  chat_id))],
        [InlineKeyboardButton(text="← К списку чатов",              callback_data="back_chats")],
    ])


async def _kb_policies(chat_id: int) -> InlineKeyboardMarkup:
    policies = await db.get_policies(chat_id)
    rows = []
    for p in policies:
        cfg  = json.loads(p.get("config") or "{}")
        desc = describe_policy(p["type"], cfg)
        star = "⭐ " if p["is_default"] else ""
        rows.append([InlineKeyboardButton(
            text=f"{star}{p['name']} — {p['type']} ({desc})",
            callback_data=_cb("pshow", chat_id, p["id"]),
        )])
    rows.append([InlineKeyboardButton(text="➕ Новая политика",   callback_data=_cb("pnew",  chat_id))])
    rows.append([InlineKeyboardButton(text="← Назад",            callback_data=_cb("chat",  chat_id))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_policy_detail(chat_id: int, policy: dict) -> InlineKeyboardMarkup:
    rows = []
    if not policy["is_default"]:
        rows.append([InlineKeyboardButton(
            text="⭐ Сделать default", callback_data=_cb("psetdef", chat_id, policy["id"])
        )])
        rows.append([InlineKeyboardButton(
            text="🗑 Удалить",         callback_data=_cb("pdel",    chat_id, policy["id"])
        )])
    else:
        rows.append([InlineKeyboardButton(text="⭐ Уже является default", callback_data="noop")])
    rows.append([InlineKeyboardButton(text="← К политикам", callback_data=_cb("plist", chat_id))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_policy_types(chat_id: int) -> InlineKeyboardMarkup:
    labels = {
        "whitelist": "✅ whitelist — не удалять",
        "blacklist": "🚫 blacklist — мгновенно",
        "delay":     "⏱ delay — через N секунд",
        "throttle":  "📊 throttle — лимит в окне",
        "schedule":  "🕐 schedule — временное окно",
        "shadow":    "👻 shadow — случайная задержка",
    }
    rows = [[InlineKeyboardButton(text=v, callback_data=_cb("ptype", chat_id, k))]
            for k, v in labels.items()]
    rows.append([InlineKeyboardButton(text="← Отмена", callback_data=_cb("plist", chat_id))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _kb_bots(chat_id: int) -> InlineKeyboardMarkup:
    assignments = await db.get_bot_assignments(chat_id)
    default     = await db.get_default_policy(chat_id)
    rows = []
    for a in assignments:
        rows.append([InlineKeyboardButton(
            text=f"@{a['bot_username']} → {a['policy_name']} ({a['policy_type']})",
            callback_data=_cb("bshow", chat_id, a["bot_username"]),
        )])
    if not assignments:
        rows.append([InlineKeyboardButton(
            text=f"Все боты → {default['name']} (default)",
            callback_data="noop",
        )])
    rows.append([InlineKeyboardButton(text="➕ Назначить бота", callback_data=_cb("bassign", chat_id))])
    rows.append([InlineKeyboardButton(text="← Назад",          callback_data=_cb("chat",    chat_id))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_bot_detail(chat_id: int, username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Переназначить",  callback_data=_cb("breassign", chat_id, username))],
        [InlineKeyboardButton(text="✂️ Снять назначение", callback_data=_cb("bunassign", chat_id, username))],
        [InlineKeyboardButton(text="← К ботам",         callback_data=_cb("blist",     chat_id))],
    ])


async def _kb_policy_picker(chat_id: int, back_cb: str) -> InlineKeyboardMarkup:
    policies = await db.get_policies(chat_id)
    rows = [[InlineKeyboardButton(
        text=f"{'⭐ ' if p['is_default'] else ''}{p['name']} ({p['type']})",
        callback_data=_cb("bpick", chat_id, p["id"]),
    )] for p in policies]
    rows.append([InlineKeyboardButton(text="← Отмена", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ─────────────────────────────────────────────────────────────────────────────
#  /start
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("start"), StateFilter("*"))
async def pm_start(message: Message, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    kb = await _kb_chats(bot, message.from_user.id)
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
#  Chat navigation
# ─────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "back_chats")
async def cb_back_chats(call: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    kb = await _kb_chats(bot, call.from_user.id)
    txt = "Выберите чат для настройки:" if kb else "😕 Нет доступных чатов."
    await call.message.edit_text(txt, reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("chat" + SEP))
async def cb_chat_menu(call: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    chat_id = int(_p(call.data)[1])
    if not await _can_manage(bot, chat_id, call.from_user.id):
        await call.answer("⛔ Нет прав.", show_alert=True); return
    try:
        chat  = await bot.get_chat(chat_id)
        title = chat.title or str(chat_id)
    except Exception:
        title = str(chat_id)
    kb = await _kb_chat_menu(chat_id)
    await call.message.edit_text(f"⚙️ <b>{title}</b>", reply_markup=kb)
    await call.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery) -> None:
    await call.answer()


# ── Toggles ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("tcmds" + SEP))
async def cb_tcmds(call: CallbackQuery, bot: Bot) -> None:
    chat_id = int(_p(call.data)[1])
    if not await _can_manage(bot, chat_id, call.from_user.id):
        await call.answer("⛔ Нет прав.", show_alert=True); return
    s = await db.get_settings(chat_id)
    await db.upsert_settings(chat_id, delete_commands=0 if s["delete_commands"] else 1)
    await call.message.edit_reply_markup(reply_markup=await _kb_chat_menu(chat_id))
    await call.answer()


@router.callback_query(F.data.startswith("town" + SEP))
async def cb_town(call: CallbackQuery, bot: Bot) -> None:
    chat_id = int(_p(call.data)[1])
    if not await _can_manage(bot, chat_id, call.from_user.id):
        await call.answer("⛔ Нет прав.", show_alert=True); return
    s = await db.get_settings(chat_id)
    await db.upsert_settings(chat_id, delete_own=0 if s["delete_own"] else 1)
    await call.message.edit_reply_markup(reply_markup=await _kb_chat_menu(chat_id))
    await call.answer()


# ─────────────────────────────────────────────────────────────────────────────
#  Policy list & detail
# ─────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("plist" + SEP))
async def cb_plist(call: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    chat_id = int(_p(call.data)[1])
    if not await _can_manage(bot, chat_id, call.from_user.id):
        await call.answer("⛔ Нет прав.", show_alert=True); return
    await call.message.edit_text("📋 <b>Политики чата</b>", reply_markup=await _kb_policies(chat_id))
    await call.answer()


@router.callback_query(F.data.startswith("pshow" + SEP))
async def cb_pshow(call: CallbackQuery, bot: Bot) -> None:
    parts     = _p(call.data)
    chat_id   = int(parts[1])
    policy_id = int(parts[2])
    if not await _can_manage(bot, chat_id, call.from_user.id):
        await call.answer("⛔ Нет прав.", show_alert=True); return
    p = await db.get_policy_by_id(policy_id)
    if not p:
        await call.answer("❌ Политика не найдена.", show_alert=True); return
    cfg  = json.loads(p.get("config") or "{}")
    desc = describe_policy(p["type"], cfg)
    star = "  ⭐ default" if p["is_default"] else ""
    text = (
        f"📋 <b>{p['name']}</b>{star}\n"
        f"Тип: <code>{p['type']}</code>\n"
        f"Описание: {desc}\n"
        f"Config: <code>{json.dumps(cfg, ensure_ascii=False)}</code>"
    )
    await call.message.edit_text(text, reply_markup=_kb_policy_detail(chat_id, p))
    await call.answer()


@router.callback_query(F.data.startswith("psetdef" + SEP))
async def cb_psetdef(call: CallbackQuery, bot: Bot) -> None:
    parts     = _p(call.data)
    chat_id   = int(parts[1])
    policy_id = int(parts[2])
    if not await _can_manage(bot, chat_id, call.from_user.id):
        await call.answer("⛔ Нет прав.", show_alert=True); return
    p = await db.get_policy_by_id(policy_id)
    if not p:
        await call.answer("❌ Политика не найдена.", show_alert=True); return
    await db.set_default_policy(chat_id, p["name"])
    await call.answer(f"⭐ Default → {p['name']}")
    p_updated = await db.get_policy_by_id(policy_id)
    await call.message.edit_reply_markup(reply_markup=_kb_policy_detail(chat_id, p_updated))


@router.callback_query(F.data.startswith("pdel" + SEP))
async def cb_pdel(call: CallbackQuery, bot: Bot) -> None:
    parts     = _p(call.data)
    chat_id   = int(parts[1])
    policy_id = int(parts[2])
    if not await _can_manage(bot, chat_id, call.from_user.id):
        await call.answer("⛔ Нет прав.", show_alert=True); return
    p = await db.get_policy_by_id(policy_id)
    if not p:
        await call.answer("❌ Политика не найдена.", show_alert=True); return
    result = await db.delete_policy(chat_id, p["name"])
    if result == "is_default":
        await call.answer("❌ Нельзя удалить default политику.", show_alert=True); return
    await call.answer(f"🗑 {p['name']} удалена.")
    await call.message.edit_text("📋 <b>Политики чата</b>", reply_markup=await _kb_policies(chat_id))


# ─────────────────────────────────────────────────────────────────────────────
#  New policy — FSM
# ─────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("pnew" + SEP))
async def cb_pnew(call: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    chat_id = int(_p(call.data)[1])
    if not await _can_manage(bot, chat_id, call.from_user.id):
        await call.answer("⛔ Нет прав.", show_alert=True); return
    await state.set_state(PM.new_policy_name)
    await state.update_data(chat_id=chat_id, nav_msg_id=call.message.message_id)
    await call.message.answer("Введите имя новой политики:")
    await call.answer()


@router.message(PM.new_policy_name)
async def pm_policy_name(message: Message, bot: Bot, state: FSMContext) -> None:
    data    = await state.get_data()
    chat_id = data["chat_id"]
    name    = (message.text or "").strip()
    if not name or " " in name:
        await message.answer("❌ Имя не должно быть пустым или содержать пробелы."); return
    if await db.get_policy_by_name(chat_id, name):
        await message.answer(f"❌ Политика «{name}» уже существует."); return
    await state.update_data(new_policy_name=name)
    await state.set_state(PM.new_policy_config)
    # Show type selector
    kb = _kb_policy_types(chat_id)
    nav = await message.answer(f"Выберите тип для политики <b>{name}</b>:", reply_markup=kb)
    await state.update_data(type_msg_id=nav.message_id)


@router.callback_query(F.data.startswith("ptype" + SEP), PM.new_policy_config)
async def cb_ptype(call: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    parts   = _p(call.data)
    chat_id = int(parts[1])
    ptype   = parts[2]
    data    = await state.get_data()
    name    = data.get("new_policy_name", "?")

    if ptype in ("whitelist", "blacklist"):
        # No config needed — create immediately
        cfg = parse_policy_args(ptype, [])
        p   = await db.create_policy(chat_id, name, ptype, cfg)
        await state.clear()
        await call.message.edit_text(
            f"✅ Политика <b>{name}</b> создана: <i>{ptype}</i>",
            reply_markup=await _kb_policies(chat_id),
        )
        await call.answer()
        return

    # Need config input
    prompts = {
        "delay":    "Задержка в секундах (3–3600):\nПример: <code>60</code>",
        "throttle": "Лимит/окно:\nПример: <code>3/60</code>  (3 сообщ. за 60 с.)",
        "schedule": "Временное окно [timezone]:\nПример: <code>20:00-23:00 UTC+3</code>",
        "shadow":   "Диапазон секунд MIN-MAX:\nПример: <code>30-300</code>",
    }
    await state.update_data(ptype=ptype)
    await call.message.edit_text(
        f"⚙️ <b>{name}</b> — <i>{ptype}</i>\n\n{prompts[ptype]}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="← Отмена", callback_data=_cb("plist", chat_id))
        ]]),
    )
    await call.answer()


@router.message(PM.new_policy_config)
async def pm_policy_config(message: Message, bot: Bot, state: FSMContext) -> None:
    data    = await state.get_data()
    chat_id = data["chat_id"]
    ptype   = data.get("ptype")
    name    = data.get("new_policy_name", "?")

    if not ptype:
        await message.answer("❌ Сначала выберите тип политики."); return

    raw  = (message.text or "").strip()
    args = raw.split()
    try:
        cfg = parse_policy_args(ptype, args)
    except PolicyParseError as e:
        await message.answer(f"❌ {e}"); return

    p = await db.create_policy(chat_id, name, ptype, cfg)
    if p is None:
        await message.answer(f"❌ Политика «{name}» уже существует."); await state.clear(); return

    desc = describe_policy(ptype, cfg)
    await state.clear()
    await message.answer(
        f"✅ <b>{name}</b> создана: <i>{ptype}</i> — {desc}",
        reply_markup=await _kb_policies(chat_id),
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Bot assignments
# ─────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("blist" + SEP))
async def cb_blist(call: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    await state.clear()
    chat_id = int(_p(call.data)[1])
    if not await _can_manage(bot, chat_id, call.from_user.id):
        await call.answer("⛔ Нет прав.", show_alert=True); return
    await call.message.edit_text("🤖 <b>Назначения ботов</b>", reply_markup=await _kb_bots(chat_id))
    await call.answer()


@router.callback_query(F.data.startswith("bshow" + SEP))
async def cb_bshow(call: CallbackQuery, bot: Bot) -> None:
    parts    = _p(call.data)
    chat_id  = int(parts[1])
    username = parts[2]
    if not await _can_manage(bot, chat_id, call.from_user.id):
        await call.answer("⛔ Нет прав.", show_alert=True); return
    policy = await db.get_bot_policy(chat_id, username)
    cfg    = json.loads(policy.get("config") or "{}")
    desc   = describe_policy(policy["type"], cfg)
    text   = (
        f"🤖 <b>@{username}</b>\n"
        f"Политика: <b>{policy['name']}</b> <i>({policy['type']})</i>\n"
        f"{desc}"
    )
    await call.message.edit_text(text, reply_markup=_kb_bot_detail(chat_id, username))
    await call.answer()


@router.callback_query(F.data.startswith("bunassign" + SEP))
async def cb_bunassign(call: CallbackQuery, bot: Bot) -> None:
    parts    = _p(call.data)
    chat_id  = int(parts[1])
    username = parts[2]
    if not await _can_manage(bot, chat_id, call.from_user.id):
        await call.answer("⛔ Нет прав.", show_alert=True); return
    await db.unassign_bot(chat_id, username)
    await call.answer(f"✅ Назначение @{username} снято.")
    await call.message.edit_text("🤖 <b>Назначения ботов</b>", reply_markup=await _kb_bots(chat_id))


# ── Assign bot — FSM ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("bassign" + SEP))
async def cb_bassign(call: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    chat_id = int(_p(call.data)[1])
    if not await _can_manage(bot, chat_id, call.from_user.id):
        await call.answer("⛔ Нет прав.", show_alert=True); return
    await state.set_state(PM.bot_assign_name)
    await state.update_data(chat_id=chat_id, nav_msg_id=call.message.message_id)
    await call.message.answer("Введите @username бота для назначения:")
    await call.answer()


@router.message(PM.bot_assign_name)
async def pm_bot_name(message: Message, bot: Bot, state: FSMContext) -> None:
    data     = await state.get_data()
    chat_id  = data["chat_id"]
    username = (message.text or "").strip().lstrip("@").lower()
    if not username:
        await message.answer("❌ Введите корректный @username."); return
    await state.update_data(bot_username=username)
    await state.set_state(PM.bot_assign_pick)
    kb = await _kb_policy_picker(chat_id, _cb("blist", chat_id))
    nav = await message.answer(
        f"Выберите политику для <b>@{username}</b>:", reply_markup=kb
    )
    await state.update_data(pick_msg_id=nav.message_id)


@router.callback_query(F.data.startswith("bpick" + SEP), PM.bot_assign_pick)
async def cb_bpick(call: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    parts     = _p(call.data)
    chat_id   = int(parts[1])
    policy_id = int(parts[2])
    data      = await state.get_data()
    username  = data.get("bot_username", "?")
    if not await _can_manage(bot, chat_id, call.from_user.id):
        await call.answer("⛔ Нет прав.", show_alert=True); return
    p = await db.get_policy_by_id(policy_id)
    if not p:
        await call.answer("❌ Политика не найдена.", show_alert=True); return
    await db.assign_bot(chat_id, username, p["name"])
    await state.clear()
    await call.message.edit_text(
        f"✅ @{username} → <b>{p['name']}</b> <i>({p['type']})</i>",
        reply_markup=await _kb_bots(chat_id),
    )
    await call.answer()


# ── Reassign — reuse bassign flow ─────────────────────────────────────────────

@router.callback_query(F.data.startswith("breassign" + SEP))
async def cb_breassign(call: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    parts    = _p(call.data)
    chat_id  = int(parts[1])
    username = parts[2]
    if not await _can_manage(bot, chat_id, call.from_user.id):
        await call.answer("⛔ Нет прав.", show_alert=True); return
    await state.set_state(PM.bot_assign_pick)
    await state.update_data(chat_id=chat_id, bot_username=username)
    kb  = await _kb_policy_picker(chat_id, _cb("bshow", chat_id, username))
    await call.message.edit_text(
        f"Выберите новую политику для <b>@{username}</b>:", reply_markup=kb
    )
    await call.answer()
