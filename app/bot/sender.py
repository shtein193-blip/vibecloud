"""Everything that uploads audio to Telegram. file_id is cached forever, so a track is uploaded once."""
from __future__ import annotations

import asyncio
import io
import logging
import re
from collections import OrderedDict, defaultdict
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import BufferedInputFile, FSInputFile, InputMediaAudio, Message
from PIL import Image

from ..context import Ctx
from . import keyboards as kb
from .texts import bar, e, track_caption

log = logging.getLogger(__name__)


def safe_name(row: dict) -> str:
    name = f"{row.get('artist') or ''} - {row.get('title') or row['id']}".strip(" -")
    return re.sub(r'[\\/:*?"<>|]+', " ", name)[:120] + ".mp3"


class TelegramIO:
    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        self.bot: Bot = ctx.bot
        self._thumbs: OrderedDict[str, bytes] = OrderedDict()
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)  # one upload per track
        ctx.dl.tg_fetch = self.fetch_file

    async def fetch_file(self, file_id: str, dest: Path) -> bool:
        """Pull an uploaded MP3 back from Telegram instead of SoundCloud (cloud Bot API: files <= 20 MB)."""
        f = await self.bot.get_file(file_id)
        if not f.file_path:
            return False
        part = dest.with_suffix(".part")
        await self.bot.download_file(f.file_path, destination=part, timeout=300)
        part.replace(dest)
        return True

    # ---------- helpers ----------
    async def thumb(self, url: str | None) -> BufferedInputFile | None:
        if not url:
            return None
        url = url.replace("-t500x500.", "-t300x300.")
        data = self._thumbs.get(url)
        if data is None:
            try:
                async with self.ctx.sc.session.get(url) as r:
                    raw = await r.read()
                im = Image.open(io.BytesIO(raw)).convert("RGB")
                im.thumbnail((320, 320))
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=85)
                data = buf.getvalue()
            except Exception:  # noqa: BLE001
                return None
            self._thumbs[url] = data
            if len(self._thumbs) > 300:
                self._thumbs.popitem(last=False)
        return BufferedInputFile(data, "cover.jpg")

    async def _download_with_status(self, tid: int, status: Message | None, title: str):
        task = asyncio.ensure_future(self.ctx.dl.ensure(tid))
        last_text = ""
        while True:
            done, _ = await asyncio.wait({task}, timeout=2.0)
            if done:
                return task.result()
            if status:
                pct = self.ctx.dl.progress.get(tid, 0)
                text = f"⏳ Качаю <b>{e(title)}</b>\n{bar(pct / 100)} {pct}%"
                if text != last_text:
                    last_text = text
                    try:
                        await status.edit_text(text)
                    except TelegramBadRequest:
                        pass

    async def _call(self, fn, *args, **kwargs):
        for _ in range(4):
            try:
                return await fn(*args, **kwargs)
            except TelegramRetryAfter as ex:
                await asyncio.sleep(ex.retry_after + 0.5)
        return await fn(*args, **kwargs)

    # ---------- public ----------
    async def send_track(self, chat_id: int, tid: int, uid: int, status: Message | None = None,
                         private: bool = True) -> Message:
        row = await self.ctx.db.get_track(tid)
        caption = track_caption(row, self.ctx.bot_username)
        markup = kb.track(self.ctx, row, private)
        if row.get("tg_file_id"):
            try:
                msg = await self._call(self.bot.send_audio, chat_id, row["tg_file_id"],
                                       caption=caption, reply_markup=markup)
                await self.ctx.db.inc_downloads(uid)
                return msg
            except TelegramBadRequest:
                await self.ctx.db.update_track(tid, tg_file_id=None)
        async with self._locks[tid]:
            row = await self.ctx.db.get_track(tid)
            if row.get("tg_file_id"):  # someone uploaded it while we waited — reuse, no second upload
                msg = await self._call(self.bot.send_audio, chat_id, row["tg_file_id"],
                                       caption=caption, reply_markup=markup)
                await self.ctx.db.inc_downloads(uid)
                return msg
            return await self._upload_to(chat_id, tid, uid, row, status, caption, markup)

    async def _upload_to(self, chat_id: int, tid: int, uid: int, row: dict, status, caption, markup) -> Message:
        path = await self._download_with_status(tid, status, row["title"])
        if status:
            try:
                await status.edit_text(f"📤 Отправляю <b>{e(row['title'])}</b>…")
            except TelegramBadRequest:
                pass
        msg = await self._call(
            self.bot.send_audio, chat_id, FSInputFile(path, filename=safe_name(row)),
            title=row["title"], performer=row["artist"], duration=round((row["duration_ms"] or 0) / 1000),
            thumbnail=await self.thumb(row.get("artwork")), caption=caption, reply_markup=markup)
        await self._remember(tid, msg, uid)
        await self.ctx.db.inc_downloads(uid)
        return msg

    async def _remember(self, tid: int, msg: Message, uid: int | None = None) -> None:
        if msg and msg.audio:
            await self.ctx.db.update_track(tid, tg_file_id=msg.audio.file_id)
            payload = {"id": tid, "in_telegram": True}
            if self.ctx.settings.lite_mode:
                payload["status"] = "ready"  # in LITE "ready" means "lives in Telegram"
            for owner in await self.ctx.db.owners(tid):
                self.ctx.bus.publish(owner, "track_status", payload)
            self.ctx.dl.release_later(tid)  # LITE: the bytes are in Telegram now, free the disk

    async def ensure_file_id(self, tid: int, uid: int) -> str:
        """Upload silently (storage channel, or the user's chat + delete) to obtain a reusable file_id."""
        row = await self.ctx.db.get_track(tid)
        if row.get("tg_file_id"):
            return row["tg_file_id"]
        async with self._locks[tid]:
            row = await self.ctx.db.get_track(tid)
            if row.get("tg_file_id"):
                return row["tg_file_id"]
            return await self._upload_silent(tid, uid, row)

    async def _upload_silent(self, tid: int, uid: int, row: dict) -> str:
        path = await self.ctx.dl.ensure(tid)
        target = self.ctx.settings.storage_chat_id or uid
        msg = await self._call(
            self.bot.send_audio, target, FSInputFile(path, filename=safe_name(row)),
            title=row["title"], performer=row["artist"], duration=round((row["duration_ms"] or 0) / 1000),
            thumbnail=await self.thumb(row.get("artwork")), disable_notification=True,
            caption=track_caption(row, self.ctx.bot_username))
        await self._remember(tid, msg)
        if not self.ctx.settings.storage_chat_id:
            try:
                await msg.delete()
            except TelegramBadRequest:
                pass
        return msg.audio.file_id

    async def send_many(self, chat_id: int, tids: list[int], uid: int, status: Message | None = None) -> tuple[int, int]:
        """Send tracks as albums of up to 10 audio files. Returns (sent, failed)."""
        sent = failed = 0
        total = len(tids)
        for i in range(0, total, 10):
            group = tids[i:i + 10]
            rows = [await self.ctx.db.get_track(t) for t in group]
            paths = await asyncio.gather(
                *[self._noop(r["tg_file_id"]) if r.get("tg_file_id") else self.ctx.dl.ensure(r["id"]) for r in rows],
                return_exceptions=True)
            media, order = [], []
            for r, p in zip(rows, paths):
                if isinstance(p, Exception):
                    failed += 1
                    continue
                if r.get("tg_file_id"):
                    item = InputMediaAudio(media=r["tg_file_id"], caption=track_caption(r, self.ctx.bot_username))
                else:
                    item = InputMediaAudio(
                        media=FSInputFile(p, filename=safe_name(r)), title=r["title"], performer=r["artist"],
                        duration=round((r["duration_ms"] or 0) / 1000), thumbnail=await self.thumb(r.get("artwork")),
                        caption=track_caption(r, self.ctx.bot_username))
                media.append(item)
                order.append(r["id"])
            if len(media) == 1:
                await self.send_track(chat_id, order[0], uid)
                sent += 1
            elif media:
                msgs = await self._call(self.bot.send_media_group, chat_id, media)
                for tid, m in zip(order, msgs):
                    await self._remember(tid, m)
                sent += len(media)
            await self.ctx.db.inc_downloads(uid, len(media))
            if status:
                try:
                    await status.edit_text(f"📤 Отправлено {sent}/{total}\n{bar(sent / max(total, 1))}")
                except TelegramBadRequest:
                    pass
            await asyncio.sleep(1.2)
        return sent, failed

    @staticmethod
    async def _noop(v):
        return v

    async def notify(self, uid: int, text: str, markup=None) -> None:
        try:
            await self.bot.send_message(uid, text, reply_markup=markup)
        except Exception as ex:  # noqa: BLE001
            log.info("notify %s failed: %s", uid, ex)
