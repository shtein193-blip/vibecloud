"""Optional DB backup into the owner's private Telegram storage channel (opt-in: DB_BACKUP_MIN > 0).

For hosts with an ephemeral disk (Render free, etc.): music already lives in Telegram (file_id), the only
state is the small SQLite file (libraries, playlists, mixtapes). When enabled, a gzipped snapshot is posted
to STORAGE_CHAT_ID and pinned; on a fresh start the pinned snapshot is restored before the DB is opened.
Only the bot's own previous snapshot message from the current run is replaced.
"""
from __future__ import annotations

import asyncio
import gzip
import logging
import shutil
import time

from aiogram import Bot
from aiogram.types import BufferedInputFile

from .config import Settings
from .db import DB

log = logging.getLogger(__name__)
PREFIX = "vibecloud-db-"


async def restore(bot: Bot, s: Settings) -> bool:
    """Download the pinned snapshot if the local DB is missing (fresh container)."""
    if s.db_path.exists() or not s.storage_chat_id:
        return False
    try:
        chat = await bot.get_chat(s.storage_chat_id)
        doc = chat.pinned_message and chat.pinned_message.document
        if not doc or not (doc.file_name or "").startswith(PREFIX):
            log.info("no DB snapshot in the storage channel — starting with an empty library")
            return False
        f = await bot.get_file(doc.file_id)
        gz = s.data_dir / "restore.db.gz"
        await bot.download_file(f.file_path, destination=gz, timeout=120)
        with gzip.open(gz, "rb") as src, open(s.db_path, "wb") as dst:
            shutil.copyfileobj(src, dst)
        gz.unlink(missing_ok=True)
        log.info("DB restored from snapshot %s (%d KB)", doc.file_name, s.db_path.stat().st_size // 1024)
        return True
    except Exception as ex:  # noqa: BLE001
        log.warning("DB restore failed (starting empty): %s", ex)
        s.db_path.unlink(missing_ok=True)
        return False


class Backup:
    def __init__(self, bot: Bot, db: DB, s: Settings):
        self.bot, self.db, self.s = bot, db, s
        self._last_changes = -1
        self._last_msg: int | None = None
        self._lock = asyncio.Lock()

    async def _changes(self) -> int:
        cur = await self.db.conn.execute("SELECT total_changes()")
        return (await cur.fetchone())[0]

    async def snapshot(self) -> bool:
        async with self._lock:
            changes = await self._changes()
            if changes == self._last_changes:
                return False
            tmp = self.s.data_dir / "snapshot.db"
            tmp.unlink(missing_ok=True)
            await self.db.conn.execute("VACUUM INTO ?", (str(tmp),))  # consistent copy while running
            data = gzip.compress(tmp.read_bytes(), 6)
            tmp.unlink(missing_ok=True)
            name = f"{PREFIX}{time.strftime('%Y%m%d-%H%M%S')}.sqlite.gz"
            msg = await self.bot.send_document(
                self.s.storage_chat_id, BufferedInputFile(data, name), disable_notification=True,
                caption="🗄 Резервная копия базы VibeCloud (закреплённая — последняя)")
            await self.bot.pin_chat_message(self.s.storage_chat_id, msg.message_id, disable_notification=True)
            if self._last_msg:
                try:
                    await self.bot.delete_message(self.s.storage_chat_id, self._last_msg)
                except Exception:  # noqa: BLE001
                    pass
            self._last_msg, self._last_changes = msg.message_id, changes
            log.info("DB snapshot saved (%d KB)", len(data) // 1024)
            return True

    async def loop(self) -> None:
        await asyncio.sleep(60)
        while True:
            try:
                await self.snapshot()
            except Exception as ex:  # noqa: BLE001
                log.warning("DB snapshot failed: %s", ex)
            await asyncio.sleep(self.s.db_backup_min * 60)
