"""Turns any SoundCloud link into library entries (+ playlist) and schedules downloads."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Awaitable, Callable

from .config import Settings
from .db import DB, now
from .downloader import Downloader
from .events import EventBus
from .security import Signer
from .serialize import playlist_out, track_out
from .soundcloud import Resolved, SoundCloud, SoundCloudError, track_meta

log = logging.getLogger(__name__)

MODE_SUFFIX = {"likes": "/likes", "user_tracks": "/tracks", "user_sets": "/sets"}
KIND_LABEL = {"track": "Трек", "playlist": "Плейлист", "album": "Альбом", "likes": "Лайки",
              "user_tracks": "Треки артиста", "user_sets": "Все плейлисты", "user": "Профиль"}


class NeedChoice(Exception):
    """A profile link: the user must choose what to import."""

    def __init__(self, preview: dict):
        self.preview = preview


class Importer:
    def __init__(self, s: Settings, db: DB, sc: SoundCloud, dl: Downloader, bus: EventBus, signer: Signer):
        self.s, self.db, self.sc, self.dl, self.bus, self.signer = s, db, sc, dl, bus, signer
        self.running: dict[int, int] = defaultdict(int)

    # ---------- preview ----------
    def _preview(self, r: Resolved) -> dict:
        d = r.data
        p = {"kind": r.kind, "label": KIND_LABEL.get(r.kind, r.kind), "url": r.url,
             "title": r.title, "artist": r.artist, "artwork": r.artwork, "count": 1, "duration": 0}
        if r.kind == "track":
            p["duration"] = round((d.get("full_duration") or d.get("duration") or 0) / 1000)
        elif r.kind in ("playlist", "album"):
            p["count"] = d.get("track_count") or len(d.get("tracks") or [])
            p["duration"] = round((d.get("duration") or 0) / 1000)
        elif r.kind == "likes":
            p["count"] = d.get("likes_count") or d.get("public_favorites_count") or 0
        elif r.kind == "user_tracks":
            p["count"] = d.get("track_count") or 0
        elif r.kind == "user_sets":
            p["count"] = d.get("playlist_count") or 0
        elif r.kind == "user":
            p["count"] = 0
            p["options"] = [
                {"mode": "likes", "label": "Лайки", "count": d.get("likes_count") or d.get("public_favorites_count") or 0},
                {"mode": "user_tracks", "label": "Треки", "count": d.get("track_count") or 0},
                {"mode": "user_sets", "label": "Плейлисты", "count": d.get("playlist_count") or 0},
            ]
        return p

    async def inspect(self, url: str) -> dict:
        return self._preview(await self.sc.resolve(url))

    # ---------- import ----------
    async def run(self, uid: int, url: str, mode: str | None = None,
                  on_tracks: Callable[[dict], Awaitable[None]] | None = None) -> dict:
        if self.running[uid] >= 3:
            raise SoundCloudError("Уже идут 3 переноса — дождись их окончания")
        self.running[uid] += 1
        try:
            r = await self.sc.resolve(url)
            if r.kind == "user":
                if mode not in MODE_SUFFIX:
                    raise NeedChoice(self._preview(r))
                r = await self.sc.resolve(r.url + MODE_SUFFIX[mode])
            if r.kind == "user_sets":
                return await self._import_sets(uid, r, on_tracks)
            return await self._import(uid, r, on_tracks)
        finally:
            self.running[uid] -= 1

    async def _fetch_tracks(self, r: Resolved) -> list[dict]:
        limit = self.s.max_import_tracks
        if r.kind == "track":
            return [r.data]
        if r.kind in ("playlist", "album"):
            return await self.sc.playlist_tracks(r.data, limit)
        if r.kind == "likes":
            return await self.sc.user_likes(r.data["id"], limit)
        if r.kind == "user_tracks":
            return await self.sc.user_tracks(r.data["id"], limit)
        raise SoundCloudError("Не знаю, как перенести эту ссылку")

    async def _import(self, uid: int, r: Resolved, on_tracks=None, job_id: str | None = None) -> dict:
        own_job = job_id is None
        if own_job:
            job_id = await self.db.create_job(uid, r.url, r.kind, r.title, r.artwork, 0)
            self.bus.publish(uid, "job", {"id": job_id, "status": "running", "kind": r.kind,
                                          "title": r.title, "artwork": r.artwork, "total": 0, "added": 0})
        try:
            raw = await self._fetch_tracks(r)
        except Exception as e:
            if own_job:
                await self.db.update_job(job_id, status="error", error=str(e)[:200])
                self.bus.publish(uid, "job", {"id": job_id, "status": "error", "error": str(e)})
            raise
        metas = [track_meta(t) for t in raw]
        await self.db.update_job(job_id, total=len(metas))
        if own_job:
            self.bus.publish(uid, "job", {"id": job_id, "status": "running", "kind": r.kind, "title": r.title,
                                          "artwork": r.artwork, "total": len(metas), "added": 0})

        playlist_id = None
        if r.kind != "track":
            playlist_id = await self.db.upsert_playlist(uid, {
                "sc_id": r.data.get("id"), "kind": r.kind, "title": r.title, "artist": r.artist,
                "artwork": r.artwork or (metas[0]["artwork"] if metas else None),
                "permalink": r.data.get("permalink_url") or r.url, "source_url": r.url,
            })

        added_rows, new_count = [], 0
        prefetch_budget = self.s.prefetch_per_import
        for m in metas:
            await self.db.upsert_track(m)
            if await self.db.add_to_library(uid, m["id"]):
                new_count += 1
            row = await self.db.library_entry(uid, m["id"])
            added_rows.append(row)
            if m["available"] and prefetch_budget > 0 and row["status"] not in ("ready",) \
                    and not row["tg_file_id"]:
                prefetch_budget -= 1
                self.dl.prefetch(m["id"])
            self.bus.publish(uid, "track_added", {"job": job_id, "playlist": playlist_id,
                                                  "track": track_out(row, uid, self.signer)})
        if playlist_id:
            await self.db.set_playlist_tracks(playlist_id, [m["id"] for m in metas])
            await self.db.update_job(job_id, playlist_id=playlist_id)

        result = {"job_id": job_id, "kind": r.kind, "title": r.title, "artist": r.artist,
                  "artwork": r.artwork, "url": r.url, "playlist_id": playlist_id,
                  "track_ids": [m["id"] for m in metas], "total": len(metas), "new": new_count,
                  "unavailable": sum(1 for m in metas if not m["available"])}
        if own_job:
            await self.db.update_job(job_id, status="done", added=len(metas), finished_at=now())
            pl = await self.db.get_playlist(playlist_id) if playlist_id else None
            self.bus.publish(uid, "job", {"id": job_id, "status": "done", "kind": r.kind, "title": r.title,
                                          "artwork": r.artwork, "total": len(metas), "added": len(metas),
                                          "new": new_count, "playlist": playlist_out(pl) if pl else None})
        if on_tracks:
            await on_tracks(result)
        return result

    async def _import_sets(self, uid: int, r: Resolved, on_tracks=None) -> dict:
        job_id = await self.db.create_job(uid, r.url, r.kind, r.title, r.artwork, 0)
        self.bus.publish(uid, "job", {"id": job_id, "status": "running", "kind": r.kind, "title": r.title,
                                      "artwork": r.artwork, "total": 0, "added": 0})
        playlists = await self.sc.user_playlists(r.data["id"])
        total, new, ids, pids = 0, 0, [], []
        for p in playlists:
            kind = "album" if p.get("is_album") else "playlist"
            sub = Resolved(kind, p.get("permalink_url"), p)
            res = await self._import(uid, sub, None, job_id=job_id)
            total += res["total"]
            new += res["new"]
            ids += res["track_ids"]
            pids.append(res["playlist_id"])
        await self.db.update_job(job_id, status="done", total=total, added=total, finished_at=now())
        self.bus.publish(uid, "job", {"id": job_id, "status": "done", "kind": r.kind, "title": r.title,
                                      "artwork": r.artwork, "total": total, "added": total, "new": new})
        result = {"job_id": job_id, "kind": r.kind, "title": r.title, "artist": r.artist, "artwork": r.artwork,
                  "url": r.url, "playlist_id": None, "playlist_ids": pids, "track_ids": ids,
                  "total": total, "new": new, "unavailable": 0, "playlists": len(playlists)}
        if on_tracks:
            await on_tracks(result)
        return result

    # ---------- watched playlists ----------
    async def sync_playlist(self, pl: dict) -> list[dict]:
        """Re-read a watched playlist; returns newly appeared tracks (library rows)."""
        try:
            r = await self.sc.resolve(pl["source_url"])
            raw = await self._fetch_tracks(r)
        except Exception as e:  # noqa: BLE001
            log.warning("sync %s failed: %s", pl["id"], e)
            return []
        metas = [track_meta(t) for t in raw]
        uid = pl["user_id"]
        for m in metas:
            await self.db.upsert_track(m)
        fresh = await self.db.set_playlist_tracks(pl["id"], [m["id"] for m in metas])
        rows = []
        for tid in fresh:
            await self.db.add_to_library(uid, tid)
            row = await self.db.library_entry(uid, tid)
            rows.append(row)
            if row["status"] != "unavailable" and self.s.prefetch_per_import > 0 and not row["tg_file_id"]:
                self.dl.prefetch(tid)
            self.bus.publish(uid, "track_added", {"job": None, "playlist": pl["id"],
                                                  "track": track_out(row, uid, self.signer)})
        await self.db.run("UPDATE playlists SET synced_at=strftime('%s','now') WHERE id=?", (pl["id"],))
        return rows
