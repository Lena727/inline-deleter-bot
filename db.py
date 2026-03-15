"""Async SQLite database layer (aiosqlite)."""
from __future__ import annotations

import aiosqlite

import config

# ── Schema ────────────────────────────────────────────────────────────────────

_CREATE_SETTINGS = """
CREATE TABLE IF NOT EXISTS chat_settings (
    chat_id         INTEGER PRIMARY KEY,
    delete_delay    INTEGER NOT NULL DEFAULT 60,
    delete_commands INTEGER NOT NULL DEFAULT 0,   -- bool: remove /command messages
    delete_own      INTEGER NOT NULL DEFAULT 0    -- bool: remove bot's own replies
)
"""

_CREATE_BANNED = """
CREATE TABLE IF NOT EXISTS banned_bots (
    chat_id      INTEGER NOT NULL,
    bot_username TEXT    NOT NULL COLLATE NOCASE,
    PRIMARY KEY (chat_id, bot_username)
)
"""

_CREATE_WHITELISTED = """
CREATE TABLE IF NOT EXISTS whitelisted_bots (
    chat_id      INTEGER NOT NULL,
    bot_username TEXT    NOT NULL COLLATE NOCASE,
    PRIMARY KEY (chat_id, bot_username)
)
"""


async def init_db() -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(_CREATE_SETTINGS)
        await db.execute(_CREATE_BANNED)
        await db.execute(_CREATE_WHITELISTED)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS known_chats (
                chat_id    INTEGER PRIMARY KEY,
                chat_title TEXT    NOT NULL DEFAULT '',
                username   TEXT    NOT NULL DEFAULT ''
            )
        """)
        await db.commit()


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
    return {
        "chat_id": chat_id,
        "delete_delay": config.DEFAULT_DELAY,
        "delete_commands": 0,
        "delete_own": 0,
    }


async def upsert_settings(chat_id: int, **kwargs) -> None:
    """Create row if missing, then update the provided fields."""
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


# ── Banned bots ───────────────────────────────────────────────────────────────

async def get_banned_bots(chat_id: int) -> set[str]:
    async with aiosqlite.connect(config.DB_PATH) as db:
        async with db.execute(
            "SELECT bot_username FROM banned_bots WHERE chat_id = ?", (chat_id,)
        ) as cur:
            rows = await cur.fetchall()
    return {row[0].lower() for row in rows}


async def add_banned_bot(chat_id: int, username: str) -> bool:
    """Return True if newly added, False if already present."""
    clean = username.lower().lstrip("@")
    async with aiosqlite.connect(config.DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO banned_bots (chat_id, bot_username) VALUES (?, ?)",
                (chat_id, clean),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def remove_banned_bot(chat_id: int, username: str) -> bool:
    """Return True if removed, False if was not in the list."""
    clean = username.lower().lstrip("@")
    async with aiosqlite.connect(config.DB_PATH) as db:
        async with db.execute(
            "DELETE FROM banned_bots WHERE chat_id = ? AND bot_username = ?",
            (chat_id, clean),
        ) as cur:
            await db.commit()
            return cur.rowcount > 0


# ── Whitelisted bots ──────────────────────────────────────────────────────────

async def get_whitelisted_bots(chat_id: int) -> set[str]:
    async with aiosqlite.connect(config.DB_PATH) as db:
        async with db.execute(
            "SELECT bot_username FROM whitelisted_bots WHERE chat_id = ?", (chat_id,)
        ) as cur:
            rows = await cur.fetchall()
    return {row[0].lower() for row in rows}


async def add_whitelisted_bot(chat_id: int, username: str) -> bool:
    """Return True if newly added, False if already present."""
    clean = username.lower().lstrip("@")
    async with aiosqlite.connect(config.DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO whitelisted_bots (chat_id, bot_username) VALUES (?, ?)",
                (chat_id, clean),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def remove_whitelisted_bot(chat_id: int, username: str) -> bool:
    """Return True if removed, False if was not in the list."""
    clean = username.lower().lstrip("@")
    async with aiosqlite.connect(config.DB_PATH) as db:
        async with db.execute(
            "DELETE FROM whitelisted_bots WHERE chat_id = ? AND bot_username = ?",
            (chat_id, clean),
        ) as cur:
            await db.commit()
            return cur.rowcount > 0



# ── Known chats (groups/supergroups the bot is a member of) ─────────────────────
async def upsert_known_chat(chat_id: int, title: str, username: str = "") -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            """INSERT INTO known_chats (chat_id, chat_title, username)
               VALUES (?, ?, ?)
               ON CONFLICT(chat_id) DO UPDATE SET
                   chat_title = excluded.chat_title,
                   username   = excluded.username""",
            (chat_id, title or "", username or ""),
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
