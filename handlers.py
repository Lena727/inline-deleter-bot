"""Group handlers — bash-like commands + via-bot message processing."""
from __future__ import annotations

import asyncio
import logging
import os
import sys

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter, Command
from aiogram.types import Message

import config
import db
from engine import PolicyParseError, describe_policy, parse_policy_args, POLICY_TYPES
from utils import is_admin, schedule_delete, smart_reply

logger = logging.getLogger(__name__)
router = Router(name="group")


# ─────────────────────────────────────────────────────────────────────────────
#  Bash-like command filter
# ─────────────────────────────────────────────────────────────────────────────

class Cmd(BaseFilter):
    """Match messages whose first word equals one of the given verbs (case-insensitive).
    Only fires in group/supergroup chats.
    """

    def __init__(self, *verbs: str) -> None:
        self._verbs = {v.lower() for v in verbs}

    async def __call__(self, message: Message) -> bool:
        if message.chat.type not in ("group", "supergroup"):
            return False
        if not message.text:
            return False
        first = message.text.strip().split()[0].lower()
        return first in self._verbs


def _args(message: Message, skip: int = 1) -> list[str]:
    """Return command arguments, skipping the first `skip` words."""
    parts = (message.text or "").split()
    return parts[skip:]


# ─────────────────────────────────────────────────────────────────────────────
#  Guards
# ─────────────────────────────────────────────────────────────────────────────

async def _require_admin(message: Message, bot: Bot) -> bool:
    if not await is_admin(bot, message.chat.id, message.from_user.id):
        err = await message.answer(
            "❌ Только администраторы с правом <b>удаления сообщений</b> "
            "могут управлять ботом."
        )
        schedule_delete(bot, message.chat.id, err.message_id, 8)
        schedule_delete(bot, message.chat.id, message.message_id, 3)
        return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
#  Owner — hot reload
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("reload"), F.from_user.id == config.OWNER_ID)
async def cmd_reload(message: Message) -> None:
    await message.answer("🔄 Перезапуск бота…")
    logger.info("Hot reload by owner id=%d", message.from_user.id)
    await asyncio.sleep(0.5)
    os.execv(sys.executable, [sys.executable] + sys.argv)


@router.message(Command("reload"))
async def cmd_reload_denied(message: Message, bot: Bot) -> None:
    err = await message.answer("🚫 Только владелец бота может перезагружать его.")
    if message.chat.type in ("group", "supergroup"):
        schedule_delete(bot, message.chat.id, err.message_id, 8)
        schedule_delete(bot, message.chat.id, message.message_id, 3)


# ─────────────────────────────────────────────────────────────────────────────
#  /togglecmds  /toggleown  /chatstatus  /help
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Command("togglecmds"))
async def cmd_toggle_commands(message: Message, bot: Bot) -> None:
    if not await _require_admin(message, bot): return
    s = await db.get_settings(message.chat.id)
    v = 0 if s["delete_commands"] else 1
    await db.upsert_settings(message.chat.id, delete_commands=v)
    await smart_reply(message, bot, f"Удаление команд: <b>{'вкл ✅' if v else 'выкл ❌'}</b>")


@router.message(Command("toggleown"))
async def cmd_toggle_own(message: Message, bot: Bot) -> None:
    if not await _require_admin(message, bot): return
    s = await db.get_settings(message.chat.id)
    v = 0 if s["delete_own"] else 1
    await db.upsert_settings(message.chat.id, delete_own=v)
    await smart_reply(message, bot, f"Автоудаление ответов бота: <b>{'вкл ✅' if v else 'выкл ❌'}</b>")


@router.message(Command("chatstatus"))
async def cmd_chat_status(message: Message, bot: Bot) -> None:
    if not await _require_admin(message, bot): return
    s           = await db.get_settings(message.chat.id)
    policies    = await db.get_policies(message.chat.id)
    assignments = await db.get_bot_assignments(message.chat.id)

    pol_lines = []
    for p in policies:
        import json as _json
        cfg  = _json.loads(p.get("config") or "{}")
        desc = describe_policy(p["type"], cfg)
        mark = " ⭐" if p["is_default"] else ""
        pol_lines.append(f"  • <b>{p['name']}</b>{mark} — {p['type']} ({desc})")

    ass_lines = [f"  • @{a['bot_username']} → {a['policy_name']}" for a in assignments] or ["  нет"]

    text = (
        "⚙️ <b>Настройки чата</b>\n\n"
        f"🗑 Удалять команды: <b>{'да' if s['delete_commands'] else 'нет'}</b>\n"
        f"🤖 Удалять ответы бота: <b>{'да' if s['delete_own'] else 'нет'}</b>\n\n"
        f"📋 <b>Политики:</b>\n" + "\n".join(pol_lines or ["  нет"]) + "\n\n"
        f"🔗 <b>Назначения ботов:</b>\n" + "\n".join(ass_lines)
    )
    await smart_reply(message, bot, text)


HELP_TEXT = (
    "📖 <b>Inline Deleter — справка</b>\n\n"
    "<b>Slash-команды:</b>\n"
    "/togglecmds — вкл/выкл удаление команд\n"
    "/toggleown  — вкл/выкл удаление ответов бота\n"
    "/chatstatus — состояние чата\n"
    "/reload     — перезагрузка (только владелец)\n\n"
    "<b>Политики</b> (без /):  <code>policy &lt;subcmd&gt;</code>\n"
    "  <code>policy list</code>\n"
    "  <code>policy new &lt;name&gt; &lt;type&gt; [args]</code>\n"
    "  <code>policy set default &lt;name&gt;</code>\n"
    "  <code>policy rename &lt;old&gt; &lt;new&gt;</code>\n"
    "  <code>policy del &lt;name&gt;</code>\n"
    "  <code>policy show &lt;name&gt;</code>\n\n"
    "<b>Типы политик:</b>\n"
    "  <code>whitelist</code>              — не удалять\n"
    "  <code>blacklist</code>              — мгновенно\n"
    "  <code>delay &lt;сек&gt;</code>            — через N секунд\n"
    "  <code>throttle &lt;N&gt;/&lt;сек&gt;</code>      — не более N за окно\n"
    "  <code>schedule &lt;HH:MM&gt;-&lt;HH:MM&gt; [UTC±N]</code>\n"
    "  <code>shadow &lt;MIN&gt;-&lt;MAX&gt;</code>       — случайная задержка\n\n"
    "<b>Боты:</b>  <code>bot &lt;subcmd&gt;</code>\n"
    "  <code>bot assign @username &lt;policy&gt;</code>\n"
    "  <code>bot unassign @username</code>\n"
    "  <code>bot list</code>\n\n"
    "⭐ = политика по умолчанию  |  ЛС → /start для меню"
)


@router.message(Command("help"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_help(message: Message, bot: Bot) -> None:
    sent = await message.answer(HELP_TEXT)
    schedule_delete(bot, message.chat.id, sent.message_id, 120)


# ─────────────────────────────────────────────────────────────────────────────
#  policy <subcmd>
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Cmd("policy"))
async def cmd_policy(message: Message, bot: Bot) -> None:
    if not await _require_admin(message, bot): return
    args = _args(message)  # everything after "policy"

    if not args:
        await smart_reply(message, bot,
            "ℹ️ policy list | new | set | rename | del | show\n"
            "Подробнее: /help")
        return

    sub = args[0].lower()

    # ── policy list ──────────────────────────────────────────────────────────
    if sub == "list":
        import json as _json
        policies = await db.get_policies(message.chat.id)
        if not policies:
            await smart_reply(message, bot, "Политик нет. Создайте: policy new <name> delay 60")
            return
        lines = []
        for p in policies:
            cfg  = _json.loads(p.get("config") or "{}")
            desc = describe_policy(p["type"], cfg)
            mark = " ⭐" if p["is_default"] else ""
            lines.append(f"<b>{p['name']}</b>{mark}  <i>{p['type']}</i>  {desc}")
        await smart_reply(message, bot, "📋 <b>Политики:</b>\n" + "\n".join(lines))

    # ── policy show <name> ───────────────────────────────────────────────────
    elif sub == "show":
        if len(args) < 2:
            await smart_reply(message, bot, "Использование: policy show <name>"); return
        import json as _json
        p = await db.get_policy_by_name(message.chat.id, args[1])
        if not p:
            await smart_reply(message, bot, f"❌ Политика «{args[1]}» не найдена."); return
        cfg  = _json.loads(p.get("config") or "{}")
        desc = describe_policy(p["type"], cfg)
        mark = "  ⭐ default" if p["is_default"] else ""
        await smart_reply(message, bot,
            f"📋 <b>{p['name']}</b>{mark}\n"
            f"Тип: <code>{p['type']}</code>\n"
            f"Описание: {desc}\n"
            f"Config: <code>{_json.dumps(cfg, ensure_ascii=False)}</code>")

    # ── policy new <name> <type> [args...] ───────────────────────────────────
    elif sub == "new":
        if len(args) < 3:
            await smart_reply(message, bot,
                "Использование: policy new &lt;name&gt; &lt;type&gt; [args]\n"
                "Типы: " + " | ".join(POLICY_TYPES)); return
        name  = args[1]
        ptype = args[2].lower()
        if ptype not in POLICY_TYPES:
            await smart_reply(message, bot,
                f"❌ Неизвестный тип. Доступны: {', '.join(POLICY_TYPES)}"); return
        try:
            cfg = parse_policy_args(ptype, args[3:])
        except PolicyParseError as e:
            await smart_reply(message, bot, f"❌ {e}"); return

        p = await db.create_policy(message.chat.id, name, ptype, cfg)
        if p is None:
            await smart_reply(message, bot, f"❌ Политика «{name}» уже существует."); return
        desc = describe_policy(ptype, cfg)
        await smart_reply(message, bot,
            f"✅ Политика <b>{name}</b> создана: <i>{ptype}</i> — {desc}")

    # ── policy set default <name> ────────────────────────────────────────────
    elif sub == "set":
        if len(args) < 3 or args[1].lower() != "default":
            await smart_reply(message, bot, "Использование: policy set default <name>"); return
        ok = await db.set_default_policy(message.chat.id, args[2])
        if ok:
            await smart_reply(message, bot, f"⭐ Политика по умолчанию: <b>{args[2]}</b>")
        else:
            await smart_reply(message, bot, f"❌ Политика «{args[2]}» не найдена.")

    # ── policy rename <old> <new> ────────────────────────────────────────────
    elif sub == "rename":
        if len(args) < 3:
            await smart_reply(message, bot, "Использование: policy rename <old> <new>"); return
        ok = await db.rename_policy(message.chat.id, args[1], args[2])
        if ok:
            await smart_reply(message, bot, f"✅ Переименовано: <b>{args[1]}</b> → <b>{args[2]}</b>")
        else:
            await smart_reply(message, bot, f"❌ Не удалось: «{args[1]}» не найдена или «{args[2]}» занято.")

    # ── policy del <name> ────────────────────────────────────────────────────
    elif sub == "del":
        if len(args) < 2:
            await smart_reply(message, bot, "Использование: policy del <name>"); return
        result = await db.delete_policy(message.chat.id, args[1])
        if result == "ok":
            await smart_reply(message, bot, f"🗑 Политика <b>{args[1]}</b> удалена.")
        elif result == "is_default":
            await smart_reply(message, bot,
                "❌ Нельзя удалить политику по умолчанию.\n"
                "Сначала назначьте другую: policy set default <name>")
        else:
            await smart_reply(message, bot, f"❌ Политика «{args[1]}» не найдена.")

    else:
        await smart_reply(message, bot,
            f"❓ Неизвестная подкоманда «{sub}».\nℹ️ policy list | new | set | rename | del | show")


# ─────────────────────────────────────────────────────────────────────────────
#  bot <subcmd>
# ─────────────────────────────────────────────────────────────────────────────

@router.message(Cmd("bot"))
async def cmd_bot(message: Message, bot: Bot) -> None:
    if not await _require_admin(message, bot): return
    args = _args(message)

    if not args:
        await smart_reply(message, bot, "ℹ️ bot assign | unassign | list"); return

    sub = args[0].lower()

    # ── bot list ─────────────────────────────────────────────────────────────
    if sub == "list":
        assignments = await db.get_bot_assignments(message.chat.id)
        default     = await db.get_default_policy(message.chat.id)
        if not assignments:
            await smart_reply(message, bot,
                f"Назначений нет. Все боты → политика по умолчанию (<b>{default['name']}</b>)."); return
        lines = [f"@{a['bot_username']} → <b>{a['policy_name']}</b> <i>({a['policy_type']})</i>"
                 for a in assignments]
        await smart_reply(message, bot,
            f"🤖 <b>Назначения ботов:</b>\n" + "\n".join(lines) +
            f"\n\nОстальные → <b>{default['name']}</b> (default)")

    # ── bot assign @username <policy> ────────────────────────────────────────
    elif sub == "assign":
        if len(args) < 3:
            await smart_reply(message, bot, "Использование: bot assign @username <policy>"); return
        username    = args[1].lstrip("@").lower()
        policy_name = args[2]
        result = await db.assign_bot(message.chat.id, username, policy_name)
        if result == "ok":
            await smart_reply(message, bot,
                f"✅ @{username} → политика <b>{policy_name}</b>")
        else:
            await smart_reply(message, bot, f"❌ Политика «{policy_name}» не найдена.")

    # ── bot unassign @username ────────────────────────────────────────────────
    elif sub == "unassign":
        if len(args) < 2:
            await smart_reply(message, bot, "Использование: bot unassign @username"); return
        username = args[1].lstrip("@").lower()
        removed  = await db.unassign_bot(message.chat.id, username)
        if removed:
            default = await db.get_default_policy(message.chat.id)
            await smart_reply(message, bot,
                f"✅ Назначение @{username} снято → теперь через default (<b>{default['name']}</b>)")
        else:
            await smart_reply(message, bot, f"⚠️ @{username} не имел назначения.")

    else:
        await smart_reply(message, bot, f"❓ Неизвестная подкоманда «{sub}».")


# ─────────────────────────────────────────────────────────────────────────────
#  Core: via-bot message handler
# ─────────────────────────────────────────────────────────────────────────────

@router.message(F.via_bot)
async def handle_via_message(message: Message, bot: Bot) -> None:
    from engine import process_via_message
    await process_via_message(bot, message)
