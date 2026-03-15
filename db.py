"""Async SQLite database layer — Policy Engine edition."""
from __future__ import annotations

import json
import logging

import aiosqlite

import config

logger = logging.getLogger(__name__)

# ── Schema ────────────────────────────────────────────────────────────────────

_CREATE_SETTINGS = """
CREATE TABLE IF NOT EXISTS chat_settings (
    chat_id         INTEGER PRIMARY KEY,
    delete_commands INTEGER NOT NULL DEFAULT 0,
    delete_own      INTEGER NOT NULL DEFAULT 0
)
"""

_CREATE_KNOWN_CHATS = """
CREATE TABLE IF NOT EXISTS known_chats (
    chat_id    INTEGER PRIMARY KEY,
    chat_title TEXT NOT NULL DEFAULT '',
    username   TEXT NOT NULL DEFAULT ''
)
"""

_CREATE_POLICIES = """
CREATE TABLE IF NOT EXISTS policies (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL,
    name       TEXT    NOT NULL COLLATE NOCASE,
    type       TEXT    NOT NULL,
    config     TEXT    NOT NULL DEFAULT '{}',
    is_default INTEGER NOT NULL DEFAULT 0,
    UNIQUE(chat_id, name)
)
"""

_CREATE_BOT_ASSIGNMENTS = """
CREATE TABLE IF NOT EXISTS bot_assignments (
    chat_id      INTEGER NOT NULL,
    bot_username TEXT    NOT NULL COLLATE NOCASE,
    policy_id    INTEGER NOT NULL,
    PRIMARY KEY (chat_id, bot_username),
    FOREIGN KEY (policy_id) REFERENCES policies(id) ON DELETE CASCADE
)
"""

_DEFAULT_POLICY_NAME   = "default"
_DEFAULT_POLICY_TYPE   = "delay"
_DEFAULT_POLICY_CONFIG = json.dumps({"delay": config.DEFAULT_DELAY})

POLICY_TYPES = ("whitelist", "blacklist", "delay", "throttle", "schedule", "shadow")


# ── Init ──────────────────────────────────────────────────────────────────────

async def init_db() -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(_CREATE_SETTINGS)
        await db.execute(_CREATE_KNOWN_CHATS)
        await db.execute(_CREATE_POLICIES)
        await db.execute(_CREATE_BOT_ASSIGNMENTS)
        # Soft migrations for existing deployments
        for stmt in [
            "ALTER TABLE chat_settings ADD COLUMN reply_ttl INTEGER NOT NULL DEFAULT 30",
        ]:
            try:
                await db.execute(stmt)
            except Exception:
                pass
        await db.commit()
    logger.info("DB ready at '%s'", config.DB_PATH)


# ── Chat settings ─────────────────────────────────────────────────────────────

async def get_settings(chat_id: int) -> dict:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM chat_settings WHERE chat_id = ?", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return dict(row)
    return {"chat_id": chat_id, "delete_commands": 0, "delete_own": 0}


async def upsert_settings(chat_id: int, **kwargs) -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO chat_settings (chat_id) VALUES (?)", (chat_id,)
        )
        for field, value in kwargs.items():
            await db.execute(
                f"UPDATE chat_settings SET {field} = ? WHERE chat_id = ?",
                (value, chat_id),
            )
        await db.commit()


# ── Known chats ───────────────────────────────────────────────────────────────

async def upsert_known_chat(chat_id: int, title: str, username: str = "") -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            """INSERT INTO known_chats (chat_id, chat_title, username)
               VALUES (?, ?, ?)
               ON CONFLICT(chat_id) DO UPDATE SET
                   chat_title = excluded.chat_title,
                   username   = excluded.username""",
            (chat_id, title or str(chat_id), username or ""),
        )
        await db.commit()


async def remove_known_chat(chat_id: int) -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute("DELETE FROM known_chats WHERE chat_id = ?", (chat_id,))
        await db.commit()


async def get_known_chats() -> list[dict]:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT chat_id, chat_title, username FROM known_chats ORDER BY chat_title"
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ── Policy CRUD ───────────────────────────────────────────────────────────────

async def ensure_default_policy(chat_id: int) -> dict:
    """Return the default policy, creating it if it doesn't exist."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM policies WHERE chat_id = ? AND is_default = 1", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return dict(row)
        # Create default
        await db.execute(
            "INSERT OR IGNORE INTO policies (chat_id, name, type, config, is_default) "
            "VALUES (?, ?, ?, ?, 1)",
            (chat_id, _DEFAULT_POLICY_NAME, _DEFAULT_POLICY_TYPE, _DEFAULT_POLICY_CONFIG),
        )
        await db.commit()
        async with db.execute(
            "SELECT * FROM policies WHERE chat_id = ? AND is_default = 1", (chat_id,)
        ) as cur:
            return dict(await cur.fetchone())


async def get_policies(chat_id: int) -> list[dict]:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM policies WHERE chat_id = ? ORDER BY is_default DESC, name",
            (chat_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_policy_by_name(chat_id: int, name: str) -> dict | None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM policies WHERE chat_id = ? AND name = ? COLLATE NOCASE",
            (chat_id, name),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def get_policy_by_id(policy_id: int) -> dict | None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM policies WHERE id = ?", (policy_id,)
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def create_policy(
    chat_id: int, name: str, ptype: str, cfg: dict
) -> dict | None:
    """Return created policy dict, or None if name already exists."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO policies (chat_id, name, type, config) VALUES (?, ?, ?, ?)",
                (chat_id, name, ptype, json.dumps(cfg)),
            )
            await db.commit()
        except aiosqlite.IntegrityError:
            return None
    return await get_policy_by_name(chat_id, name)


async def update_policy_config(policy_id: int, cfg: dict) -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            "UPDATE policies SET config = ? WHERE id = ?",
            (json.dumps(cfg), policy_id),
        )
        await db.commit()


async def rename_policy(chat_id: int, old_name: str, new_name: str) -> bool:
    """Returns False if old not found or new name already taken."""
    policy = await get_policy_by_name(chat_id, old_name)
    if not policy:
        return False
    async with aiosqlite.connect(config.DB_PATH) as db:
        try:
            await db.execute(
                "UPDATE policies SET name = ? WHERE id = ?",
                (new_name, policy["id"]),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def delete_policy(chat_id: int, name: str) -> str:
    """Returns 'ok', 'not_found', or 'is_default'."""
    policy = await get_policy_by_name(chat_id, name)
    if not policy:
        return "not_found"
    if policy["is_default"]:
        return "is_default"
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute("DELETE FROM policies WHERE id = ?", (policy["id"],))
        await db.commit()
    return "ok"


async def set_default_policy(chat_id: int, name: str) -> bool:
    """Clears old default, sets new one. Returns False if name not found."""
    policy = await get_policy_by_name(chat_id, name)
    if not policy:
        return False
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            "UPDATE policies SET is_default = 0 WHERE chat_id = ?", (chat_id,)
        )
        await db.execute(
            "UPDATE policies SET is_default = 1 WHERE id = ?", (policy["id"],)
        )
        await db.commit()
    return True


async def get_default_policy(chat_id: int) -> dict:
    """Always returns a policy (creates default if missing)."""
    return await ensure_default_policy(chat_id)


# ── Bot assignments ───────────────────────────────────────────────────────────

async def get_bot_policy(chat_id: int, bot_username: str) -> dict:
    """Return the effective policy for this bot (assigned or default)."""
    clean = bot_username.lower().lstrip("@")
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT p.* FROM bot_assignments ba
               JOIN policies p ON p.id = ba.policy_id
               WHERE ba.chat_id = ? AND ba.bot_username = ? COLLATE NOCASE""",
            (chat_id, clean),
        ) as cur:
            row = await cur.fetchone()
            if row:
                return dict(row)
    return await ensure_default_policy(chat_id)


async def assign_bot(chat_id: int, bot_username: str, policy_name: str) -> str:
    """Returns 'ok', 'policy_not_found'."""
    clean  = bot_username.lower().lstrip("@")
    policy = await get_policy_by_name(chat_id, policy_name)
    if not policy:
        return "policy_not_found"
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            """INSERT INTO bot_assignments (chat_id, bot_username, policy_id)
               VALUES (?, ?, ?)
               ON CONFLICT(chat_id, bot_username) DO UPDATE SET policy_id = excluded.policy_id""",
            (chat_id, clean, policy["id"]),
        )
        await db.commit()
    return "ok"


async def unassign_bot(chat_id: int, bot_username: str) -> bool:
    clean = bot_username.lower().lstrip("@")
    async with aiosqlite.connect(config.DB_PATH) as db:
        async with db.execute(
            "DELETE FROM bot_assignments WHERE chat_id = ? AND bot_username = ?",
            (chat_id, clean),
        ) as cur:
            await db.commit()
            return cur.rowcount > 0


async def get_bot_assignments(chat_id: int) -> list[dict]:
    """Return list of {bot_username, policy_name, policy_type}."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT ba.bot_username, p.name AS policy_name, p.type AS policy_type
               FROM bot_assignments ba
               JOIN policies p ON p.id = ba.policy_id
               WHERE ba.chat_id = ?
               ORDER BY ba.bot_username""",
            (chat_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]
