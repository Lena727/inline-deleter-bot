"""Policy execution engine.

Resolves and applies the correct policy to every via-bot message.
Throttle counters are kept in-memory (single-process, resets on restart).
"""
from __future__ import annotations

import json
import logging
import random
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.types import Message

import db
from utils import schedule_delete

logger = logging.getLogger(__name__)

# {policy_id: {"count": int, "window_start": int}}
_throttle_counters: dict[int, dict] = defaultdict(lambda: {"count": 0, "window_start": 0})


# ── Timezone helpers ──────────────────────────────────────────────────────────

def parse_tz_offset(s: str) -> int:
    """Parse 'UTC+3', 'UTC-5', 'UTC' → offset in minutes."""
    s = s.strip().upper()
    if s in ("UTC", "UTC+0", "UTC-0"):
        return 0
    if s.startswith("UTC+"):
        try:
            return int(s[4:]) * 60
        except ValueError:
            return 0
    if s.startswith("UTC-"):
        try:
            return -int(s[4:]) * 60
        except ValueError:
            return 0
    return 0


def format_tz_offset(minutes: int) -> str:
    h = abs(minutes) // 60
    sign = "+" if minutes >= 0 else "-"
    return f"UTC{sign}{h}" if h else "UTC"


# ── Policy config parsers (for bash-like commands) ────────────────────────────

class PolicyParseError(ValueError):
    pass


def parse_policy_args(ptype: str, args: list[str]) -> dict:
    """Parse positional args into a config dict for the given policy type.

    delay    <seconds>
    throttle <N>/<W>
    schedule <HH:MM>-<HH:MM> [UTC±N]
    shadow   <MIN>-<MAX>
    whitelist / blacklist  → no args needed
    """
    if ptype in ("whitelist", "blacklist"):
        return {}

    if ptype == "delay":
        if not args:
            raise PolicyParseError("укажите задержку: delay <секунды>")
        try:
            delay = int(args[0])
        except ValueError:
            raise PolicyParseError("задержка должна быть целым числом")
        if not (3 <= delay <= 3600):
            raise PolicyParseError("задержка должна быть от 3 до 3600 секунд")
        return {"delay": delay}

    if ptype == "throttle":
        if not args:
            raise PolicyParseError("укажите лимит: throttle <N>/<секунды>  (напр. 3/60)")
        try:
            limit_s, window_s = args[0].split("/")
            limit, window = int(limit_s), int(window_s)
        except (ValueError, TypeError):
            raise PolicyParseError("формат: throttle <N>/<секунды>  напр. throttle 3/60")
        if limit < 1 or window < 1:
            raise PolicyParseError("N и секунды должны быть положительными")
        return {"limit": limit, "window": window}

    if ptype == "schedule":
        # args[0] = "HH:MM-HH:MM"  args[1] (opt) = "UTC+3"
        if not args:
            raise PolicyParseError("укажите окно: schedule HH:MM-HH:MM [UTC±N]")
        try:
            from_str, to_str = args[0].split("-", 1)
            # validate
            for t in (from_str, to_str):
                hh, mm = t.split(":")
                assert 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59
        except Exception:
            raise PolicyParseError("формат времени: HH:MM-HH:MM  напр. 20:00-23:00")
        tz_offset = parse_tz_offset(args[1]) if len(args) > 1 else 0
        return {"from": from_str, "to": to_str, "tz_offset": tz_offset}

    if ptype == "shadow":
        if not args:
            raise PolicyParseError("укажите диапазон: shadow <MIN>-<MAX>  (секунды)")
        try:
            lo_s, hi_s = args[0].split("-", 1)
            lo, hi = int(lo_s), int(hi_s)
        except Exception:
            raise PolicyParseError("формат: shadow <MIN>-<MAX>  напр. shadow 30-300")
        if not (3 <= lo < hi <= 3600):
            raise PolicyParseError("допустимо: 3 ≤ MIN < MAX ≤ 3600")
        return {"min": lo, "max": hi}

    raise PolicyParseError(f"неизвестный тип политики: {ptype}")


def describe_policy(ptype: str, cfg: dict) -> str:
    """Human-readable one-line description of a policy config."""
    if ptype == "whitelist":
        return "никогда не удалять"
    if ptype == "blacklist":
        return "мгновенное удаление"
    if ptype == "delay":
        return f"удалить через {cfg.get('delay', '?')} с."
    if ptype == "throttle":
        return f"не более {cfg.get('limit','?')} шт. / {cfg.get('window','?')} с."
    if ptype == "schedule":
        tz = format_tz_offset(cfg.get("tz_offset", 0))
        return f"разрешено {cfg.get('from','?')}–{cfg.get('to','?')} {tz}"
    if ptype == "shadow":
        return f"случайно {cfg.get('min','?')}–{cfg.get('max','?')} с."
    return ptype


# ── Policy application ────────────────────────────────────────────────────────

async def _apply(bot: Bot, message: Message, policy: dict) -> None:
    chat_id = message.chat.id
    msg_id  = message.message_id
    ptype   = policy["type"]
    cfg: dict = json.loads(policy.get("config") or "{}")

    if ptype == "whitelist":
        return

    if ptype == "blacklist":
        schedule_delete(bot, chat_id, msg_id, 0)

    elif ptype == "delay":
        schedule_delete(bot, chat_id, msg_id, cfg.get("delay", 60))

    elif ptype == "shadow":
        delay = random.randint(cfg.get("min", 10), cfg.get("max", 300))
        logger.debug("shadow delay=%ds msg=%d", delay, msg_id)
        schedule_delete(bot, chat_id, msg_id, delay)

    elif ptype == "throttle":
        limit  = cfg.get("limit", 5)
        window = cfg.get("window", 60)
        pid    = policy["id"]
        now    = int(time.time())
        slot   = _throttle_counters[pid]

        if now - slot["window_start"] >= window:
            slot["window_start"] = now
            slot["count"] = 0

        slot["count"] += 1
        if slot["count"] > limit:
            logger.debug("throttle exceeded %d/%d msg=%d", slot["count"], limit, msg_id)
            schedule_delete(bot, chat_id, msg_id, 0)

    elif ptype == "schedule":
        tz_offset = cfg.get("tz_offset", 0)
        tz        = timezone(timedelta(minutes=tz_offset))
        now_dt    = datetime.now(tz)
        current   = now_dt.hour * 60 + now_dt.minute

        fh, fm   = map(int, cfg.get("from", "00:00").split(":"))
        th, tm   = map(int, cfg.get("to",   "23:59").split(":"))
        from_min = fh * 60 + fm
        to_min   = th * 60 + tm

        # Support overnight windows: 22:00-06:00
        if from_min <= to_min:
            in_window = from_min <= current <= to_min
        else:
            in_window = current >= from_min or current <= to_min

        if not in_window:
            logger.debug("schedule: outside window msg=%d", msg_id)
            schedule_delete(bot, chat_id, msg_id, 0)


async def process_via_message(bot: Bot, message: Message) -> None:
    """Resolve and apply the effective policy for a via-bot message."""
    from utils import bot_can_delete
    chat_id  = message.chat.id
    username = (message.via_bot.username or "").lower()

    # Skip silently if the bot has no delete rights — nothing useful we can do
    if not await bot_can_delete(bot, chat_id):
        logger.warning(
            "Skipping policy for @%s in chat %d — bot lacks delete rights",
            username, chat_id,
        )
        return

    policy = await db.get_bot_policy(chat_id, username)
    logger.info(
        "via @%s → policy '%s'(%s) msg=%d chat=%d",
        username, policy["name"], policy["type"], message.message_id, chat_id,
    )
    await _apply(bot, message, policy)
