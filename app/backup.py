"""Optional DB backup into the owner's private Telegram storage channel (opt-in: DB_BACKUP_MIN > 0).

For hosts with an ephemeral disk (Render free, a phone that gets wiped, etc.): music already lives in Telegram
(file_id), the only state is the small SQLite file (libraries, playlists, mixtapes, duos, statuses). A gzipped
snapshot is posted to STORAGE_CHAT_ID and pinned; on a fresh start the pinned snapshot is restored before the DB
is opened.

Zero-downtime redeploys (Render) start the new instance while the old one is still serving. To not lose the last
minutes of changes, the new instance waits (see `handover`) until the old one has been switched off and has posted
its final snapshot (caption "#final #i_<instance>"), and only then restores.
"""
from __future__ import annotations

import asyncio
import gzip
import logging
import shutil
import time

import aiohttp
from aiogram import Bot
from aiogram.types import BufferedInputFile, Message

from .config import Settings
from .db import DB

log = logging.getLogger(__name__)
PREFIX = "vibecloud-db-"


async def _pinned(bot: Bot, s: Settings) -> Message | None:
    chat = await bot.get_chat(s.storage_chat_id)
    msg = chat.pinned_message
    doc = msg and msg.document
    return msg if doc and (doc.file_name or "").startswith(PREFIX) else None


async def _probe(url: str) -> str | None:
    """Instance id that currently answers on the public URL ('?' = an old build without ids)."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as http:
            async with http.get(f"{url}/api/health", headers={"Cache-Control": "no-cache"}) as r:
                if r.status != 200:
                    return None
                data = await r.json(content_type=None)
                return str(data.get("instance") or "?")
    except Exception:  # noqa: BLE001
        return None


async def handover(bot: Bot, s: Settings, me: str) -> None:
    """Wait for a still-running previous instance to hand over its final snapshot (max ~3 min)."""
    if not s.public_url.startswith("https://"):
        return
    other = await _probe(s.public_url)
    if not other or other == me:
        return
    log.info("previous instance %s is still serving — waiting for its final DB snapshot", other)
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:  # 1) traffic switches to us, the old one gets SIGTERM
        cur = await _probe(s.public_url)
        if cur in (None, me):
            break
        await asyncio.sleep(3)
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:  # 2) the old one posts "#final" on its way out
        try:
            msg = await _pinned(bot, s)
            cap = (msg and msg.caption) or ""
            if "#final" in cap and (other == "?" or f"#i_{other}" in cap):
                log.info("got the final snapshot of %s", other)
                return
        except Exception as ex:  # noqa: BLE001
            log.info("handover: %s", ex)
        await asyncio.sleep(2)
    log.warning("no final snapshot from %s — restoring the latest pinned one", other)


async def restore(bot: Bot, s: Settings) -> int | None:
    """Download the pinned snapshot if the local DB is missing. Returns the snapshot message id."""
    if s.db_path.exists() or not s.storage_chat_id:
        return None
    try:
        msg = await _pinned(bot, s)
        if not msg:
            log.info("no DB snapshot in the storage channel — starting with an empty library")
            return None
        f = await bot.get_file(msg.document.file_id)
        gz = s.data_dir / "restore.db.gz"
        await bot.download_file(f.file_path, destination=gz, timeout=120)
        with gzip.open(gz, "rb") as src, open(s.db_path, "wb") as dst:
            shutil.copyfileobj(src, dst)
        gz.unlink(missing_ok=True)
        log.info("DB restored from %s (%d KB)", msg.document.file_name, s.db_path.stat().st_size // 1024)
        return msg.message_id
    except Exception as ex:  # noqa: BLE001
        log.warning("DB restore failed (starting empty): %s", ex)
        s.db_path.unlink(missing_ok=True)
        return None


class Backup:
    def __init__(self, bot: Bot, db: DB, s: Settings, instance: str, prev_msg: int | None = None):
        self.bot, self.db, self.s, self.instance = bot, db, s, instance
        self._last_changes = -1
        self._last_at = 0.0
        self._last_msg = prev_msg  # the restored snapshot is replaced by our first one
        self._lock = asyncio.Lock()

    async def _changes(self) -> int:
        cur = await self.db.conn.execute("SELECT total_changes()")
        return (await cur.fetchone())[0]

    async def snapshot(self, final: bool = False) -> bool:
        async with self._lock:
            changes = await self._changes()
            if changes == self._last_changes and not (final and self._last_msg):
                return False
            if changes == self._last_changes and final:
                # nothing new, just mark the current snapshot as final for the next instance
                try:
                    await self.bot.edit_message_caption(chat_id=self.s.storage_chat_id, message_id=self._last_msg,
                                                        caption=self._caption(True))
                    return True
                except Exception:  # noqa: BLE001
                    pass
            tmp = self.s.data_dir / "snapshot.db"
            tmp.unlink(missing_ok=True)
            await self.db.conn.execute("VACUUM INTO ?", (str(tmp),))  # consistent copy while running
            data = await asyncio.to_thread(lambda: gzip.compress(tmp.read_bytes(), 6))
            tmp.unlink(missing_ok=True)
            name = f"{PREFIX}{time.strftime('%Y%m%d-%H%M%S')}.sqlite.gz"
            msg = await self.bot.send_document(self.s.storage_chat_id, BufferedInputFile(data, name),
                                               disable_notification=True, caption=self._caption(final))
            await self.bot.pin_chat_message(self.s.storage_chat_id, msg.message_id, disable_notification=True)
            if self._last_msg:
                try:
                    await self.bot.delete_message(self.s.storage_chat_id, self._last_msg)
                except Exception:  # noqa: BLE001
                    pass
            self._last_msg, self._last_changes, self._last_at = msg.message_id, changes, time.monotonic()
            log.info("DB snapshot saved (%d KB)%s", len(data) // 1024, " [final]" if final else "")
            return True

    def _caption(self, final: bool) -> str:
        return (f"🗄 Резервная копия базы VibeCloud (закреплённая — последняя)\n#i_{self.instance}"
                + (" #final" if final else ""))

    async def loop(self) -> None:
        await asyncio.sleep(30)
        while True:
            try:
                await self.snapshot()
            except Exception as ex:  # noqa: BLE001
                log.warning("DB snapshot failed: %s", ex)
            await asyncio.sleep(max(1, self.s.db_backup_min) * 60)
