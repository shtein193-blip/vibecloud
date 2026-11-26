"""Download queue: SoundCloud -> MP3 (ID3 tags + cover), disk cache with LRU eviction.

LITE_MODE (Telegram-first): once a track has a Telegram file_id the local MP3 is deleted shortly
after upload; if the server ever needs the bytes again it pulls them back from Telegram (getFile)
instead of re-downloading and re-encoding from SoundCloud.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path
from typing import Awaitable, Callable

from .config import Settings
from .db import DB
from .events import EventBus
from .tasks import spawn

log = logging.getLogger(__name__)


class DownloadError(Exception):
    pass


def pick_bitrate(duration_s: float, max_bytes: int, cap: int = 320) -> int:
    """Highest CBR bitrate (kbps, <= cap) that keeps the file under the Telegram upload limit."""
    if duration_s <= 0:
        return min(192, cap)
    budget = min(int(max_bytes * 0.97 * 8 / duration_s / 1000), cap)
    for kbps in (320, 256, 192, 160, 128, 96, 64, 48, 32):
        if kbps <= budget:
            return kbps
    return 32


class Downloader:
    def __init__(self, settings: Settings, db: DB, bus: EventBus):
        self.s = settings
        self.db = db
        self.bus = bus
        self.queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self.pending: dict[int, asyncio.Future] = {}
        self.progress: dict[int, int] = {}
        self.active: set[int] = set()
        self.workers: list[asyncio.Task] = []
        self.express = asyncio.Semaphore(2)  # extra lane for tracks a user is waiting on right now
        self._seq = 0
        self.tmp = settings.data_dir / "tmp"
        self.tmp.mkdir(exist_ok=True)
        # set by the bot: pull an already-uploaded file back from Telegram (getFile, <= 20 MB on cloud API)
        self.tg_fetch: Callable[[str, Path], Awaitable[bool]] | None = None

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        for i in range(self.s.download_workers):
            self.workers.append(asyncio.create_task(self._worker(i)))
        # resume queued downloads after restart
        for row in await self.db.all("SELECT id FROM tracks WHERE status IN ('queued','downloading')"):
            await self.db.update_track(row["id"], status="cloud")
            self.prefetch(row["id"])

    async def stop(self) -> None:
        for w in self.workers:
            w.cancel()

    # ---------- public api ----------
    def prefetch(self, tid: int, priority: int = 5) -> asyncio.Future:
        """Schedule a download; returns a future resolving to the file path."""
        fut = self.pending.get(tid)
        if fut and not fut.done():
            if priority < 5:  # bump: user is waiting
                self._put(tid, priority)
            return fut
        fut = self.loop.create_future()
        fut.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
        self.pending[tid] = fut
        self._put(tid, priority)
        spawn(self._mark(tid, "queued"))
        return fut

    def release_later(self, tid: int, delay: float = 90) -> None:
        """LITE: the track is in Telegram now — drop the local copy soon (kept a bit for an active listener)."""
        if self.s.lite_mode:
            self.loop.call_later(delay, lambda: spawn(self._release(tid)))

    async def _release(self, tid: int) -> None:
        if tid in self.pending or tid in self.active:
            return self.release_later(tid, 120)
        row = await self.db.get_track(tid)
        if not row or not row["tg_file_id"] or not row["file_path"]:
            return
        if row["last_access"] and time.time() - row["last_access"] < 600:
            return self.release_later(tid, 300)  # someone is streaming it right now
        Path(row["file_path"]).unlink(missing_ok=True)
        await self.db.update_track(tid, file_path=None, file_size=None, status="cloud")

    async def ensure(self, tid: int) -> Path:
        """Return a local MP3 path, downloading with top priority if needed."""
        row = await self.db.get_track(tid)
        if not row:
            raise DownloadError("Трек не найден")
        if row["file_path"] and Path(row["file_path"]).exists():
            await self.db.touch_track(tid)
            return Path(row["file_path"])
        if row["status"] == "unavailable":
            raise DownloadError("Трек недоступен для скачивания (Go+ / регион / удалён)")
        fut = self.prefetch(tid, priority=0)
        if tid not in self.active and len(self.active) >= self.s.download_workers:
            # every worker is busy with background prefetch — don't make the listener wait in line
            spawn(self._express(tid))
        return await asyncio.shield(fut)

    async def _express(self, tid: int) -> None:
        async with self.express:
            fut = self.pending.get(tid)
            if fut is None or fut.done() or tid in self.active:
                return
            await self._run(tid, fut)

    # ---------- internals ----------
    def _put(self, tid: int, priority: int) -> None:
        self._seq += 1
        self.queue.put_nowait((priority, self._seq, tid))

    def _quiet(self, row: dict | None) -> bool:
        """LITE: a track that already lives in Telegram stays "ready" for users while we touch its bytes."""
        return bool(self.s.lite_mode and row and row["tg_file_id"])

    async def _mark(self, tid: int, status: str, **extra) -> None:
        row = await self.db.get_track(tid)
        if not row or (row["status"] == "ready" and status == "queued"):
            return
        if status == "queued":
            # atomic: a fast download (e.g. from Telegram) may already have finished meanwhile
            await self.db.run("UPDATE tracks SET status='queued', updated_at=? WHERE id=? "
                              "AND status NOT IN ('ready', 'downloading', 'unavailable')", (int(time.time()), tid))
            if (await self.db.get_track(tid))["status"] != "queued":
                return
        else:
            await self.db.update_track(tid, status=status, **{k: v for k, v in extra.items() if k in ("error",)})
        if not self._quiet(row) or status in ("unavailable",):
            self.bus.publish_many(await self.db.owners(tid), "track_status",
                                  {"id": tid, "status": status, **extra})

    async def _worker(self, n: int) -> None:
        while True:
            _, _, tid = await self.queue.get()
            fut = self.pending.get(tid)
            if fut is None or fut.done() or tid in self.active:
                continue  # duplicate queue entry (priority bump) — already handled
            await self._run(tid, fut)

    async def _run(self, tid: int, fut: asyncio.Future) -> None:
        self.active.add(tid)
        try:
            path = await self._download(tid)
            fut.set_result(path)
        except Exception as e:  # noqa: BLE001
            log.warning("download %s failed: %s", tid, e)
            if "DRM" in str(e) or "geo" in str(e).lower():
                await self._mark(tid, "unavailable", error="Защищён правообладателем — слушай в SoundCloud")
                e = DownloadError("Трек защищён правообладателем (DRM) — доступен только в SoundCloud")
            else:
                await self._mark(tid, "error", error=str(e)[:200])
            if not fut.done():
                fut.set_exception(DownloadError(str(e)))
        finally:
            self.active.discard(tid)
            self.pending.pop(tid, None)
            self.progress.pop(tid, None)

    async def _download(self, tid: int) -> Path:
        row = await self.db.get_track(tid)
        if row and row["file_path"] and Path(row["file_path"]).exists():
            return Path(row["file_path"])
        if not row or not row["permalink"]:
            raise DownloadError("Нет ссылки на трек")
        if row["tg_file_id"] and self.tg_fetch:
            final = self.s.cache_dir / f"{tid}.mp3"
            try:
                if await self.tg_fetch(row["tg_file_id"], final):
                    await self.db.update_track(tid, status="ready", file_path=str(final),
                                               file_size=final.stat().st_size, error=None,
                                               last_access=int(time.time()))
                    self.release_later(tid, 900)
                    spawn(self._evict())
                    return final
            except Exception as ex:  # noqa: BLE001
                log.info("telegram fetch %s failed, falling back to SoundCloud: %s", tid, ex)
        await self._mark(tid, "downloading", progress=0)
        kbps = pick_bitrate((row["duration_ms"] or 0) / 1000, self.s.max_upload_bytes, self.s.mp3_max_kbps)
        last = {"t": 0.0, "p": -1}

        def hook(d: dict) -> None:
            if d.get("status") != "downloading":
                return
            if d.get("fragment_count"):
                p = (d.get("fragment_index") or 0) / d["fragment_count"]
            elif d.get("total_bytes") or d.get("total_bytes_estimate"):
                p = d.get("downloaded_bytes", 0) / (d.get("total_bytes") or d["total_bytes_estimate"])
            else:
                return
            pct = int(min(p, 1) * 95)
            t = time.monotonic()
            if pct != last["p"] and t - last["t"] > 0.35:
                last.update(t=t, p=pct)
                self.loop.call_soon_threadsafe(self._progress, tid, pct)

        work = self.tmp / str(tid)
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        try:
            path = await asyncio.to_thread(self._yt_dlp, row["permalink"], work, kbps, hook)
            final = self.s.cache_dir / f"{tid}.mp3"
            shutil.move(str(path), final)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        await asyncio.to_thread(self._tag, final, row)
        await self.db.update_track(tid, status="ready", file_path=str(final),
                                   file_size=final.stat().st_size, error=None, last_access=int(time.time()))
        self.bus.publish_many(await self.db.owners(tid), "track_status",
                              {"id": tid, "status": "ready", "progress": 100})
        if row["tg_file_id"]:
            self.release_later(tid, 900)
        spawn(self._evict())
        return final

    def _progress(self, tid: int, pct: int) -> None:
        spawn(self._emit_progress(tid, pct))

    async def _emit_progress(self, tid: int, pct: int) -> None:
        self.progress[tid] = pct
        if self._quiet(await self.db.get_track(tid)):
            return
        self.bus.publish_many(await self.db.owners(tid), "track_status",
                              {"id": tid, "status": "downloading", "progress": pct})

    @staticmethod
    def _yt_dlp(url: str, work: Path, kbps: int, hook) -> Path:
        opts = {
            "format": "bestaudio/best",
            "outtmpl": str(work / "audio.%(ext)s"),
            "quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True,
            "writethumbnail": True,
            "retries": 3, "fragment_retries": 5, "socket_timeout": 20,
            "concurrent_fragment_downloads": 4,
            "progress_hooks": [hook],
            "postprocessors": [
                {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"},
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": str(kbps)},
                {"key": "FFmpegMetadata", "add_metadata": True},
                {"key": "EmbedThumbnail"},
            ],
        }
        import yt_dlp  # heavy (~30 MB RAM): loaded on the first real download, not at startup
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        mp3 = next(work.glob("*.mp3"), None)
        if not mp3:
            raise DownloadError("yt-dlp не вернул MP3")
        return mp3

    @staticmethod
    def _tag(path: Path, row: dict) -> None:
        from mutagen.id3 import COMM, ID3, TALB, TIT2, TPE1, ID3NoHeaderError
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        tags.setall("TIT2", [TIT2(encoding=3, text=row["title"] or "")])
        tags.setall("TPE1", [TPE1(encoding=3, text=row["artist"] or "")])
        if not tags.getall("TALB"):
            tags.add(TALB(encoding=3, text="VibeCloud"))
        tags.setall("COMM", [COMM(encoding=3, lang="eng", desc="", text=row["permalink"] or "")])
        tags.save(path)

    async def _evict(self) -> None:
        files = await self.db.cached_files()
        total = sum(f["file_size"] or 0 for f in files)
        for f in files:
            if total <= self.s.cache_max_bytes:
                break
            if f["id"] in self.pending:
                continue
            Path(f["file_path"]).unlink(missing_ok=True)
            total -= f["file_size"] or 0
            # file_id in Telegram survives, so the track stays "ready" for sending
            await self.db.update_track(f["id"], file_path=None, file_size=None, status="cloud")
            if not self._quiet(f):
                self.bus.publish_many(await self.db.owners(f["id"]), "track_status",
                                      {"id": f["id"], "status": "cloud"})
