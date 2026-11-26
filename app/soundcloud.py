"""Thin async client for SoundCloud's public web API (api-v2)."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit, urlunsplit

import aiohttp

from .tasks import TTLCache

log = logging.getLogger(__name__)

API = "https://api-v2.soundcloud.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

LINK_RE = re.compile(
    r"(?:https?://)?(?:(?:www|m|on|w)\.)?soundcloud\.com/[^\s<>\"'\])]+"
    r"|(?:https?://)?soundcloud\.app\.goo\.gl/[^\s<>\"'\])]+",
    re.IGNORECASE,
)
RESERVED = {"discover", "stream", "search", "you", "upload", "charts", "pages", "terms-of-use",
            "settings", "messages", "notifications", "people", "stations", "feed"}


class SoundCloudError(Exception):
    pass


@dataclass
class Resolved:
    kind: str                 # track | playlist | album | likes | user_tracks | user_sets | user
    url: str                  # canonical url
    data: dict                # raw api object (track / playlist / user)

    @property
    def title(self) -> str:
        d = self.data
        if self.kind in ("track", "playlist", "album"):
            return d.get("title") or "Без названия"
        name = d.get("username") or "SoundCloud"
        return {"likes": f"Лайки · {name}", "user_tracks": f"Треки · {name}",
                "user_sets": f"Плейлисты · {name}"}.get(self.kind, name)

    @property
    def artist(self) -> str | None:
        if self.kind in ("track", "playlist", "album"):
            return (self.data.get("user") or {}).get("username")
        return self.data.get("username")

    @property
    def artwork(self) -> str | None:
        return big_art(self.data.get("artwork_url") or self.data.get("avatar_url")
                       or (self.data.get("user") or {}).get("avatar_url"))


def extract_links(text: str | None) -> list[str]:
    if not text:
        return []
    seen, out = set(), []
    for m in LINK_RE.findall(text):
        url = m if m.lower().startswith("http") else "https://" + m
        url = url.rstrip(".,;!?")
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def clean_url(url: str) -> str:
    parts = urlsplit(url)
    host = parts.netloc.lower().replace("m.soundcloud.com", "soundcloud.com")
    host = host.replace("www.soundcloud.com", "soundcloud.com")
    path = re.sub(r"/+$", "", parts.path) or "/"
    return urlunsplit(("https", host, path, "", ""))


def big_art(url: str | None) -> str | None:
    if not url:
        return None
    return re.sub(r"-(large|t\d+x\d+|crop|small|badge|tiny|mini)\.", "-t500x500.", url)


def track_meta(t: dict) -> dict:
    pub = t.get("publisher_metadata") or {}
    user = t.get("user") or {}
    policy = t.get("policy")
    transcodings = ((t.get("media") or {}).get("transcodings")) or []
    protocols = {(tc.get("format") or {}).get("protocol") for tc in transcodings}
    drm_only = bool(transcodings) and not protocols & {"hls", "progressive"}
    available = bool(t.get("streamable", True)) and policy not in ("BLOCK", "SNIP") and not drm_only
    return {
        "id": int(t["id"]),
        "title": t.get("title") or "Без названия",
        "artist": pub.get("artist") or user.get("username") or "Unknown",
        "artwork": big_art(t.get("artwork_url") or user.get("avatar_url")),
        "duration_ms": t.get("full_duration") or t.get("duration") or 0,
        "permalink": t.get("permalink_url"),
        "waveform_url": t.get("waveform_url"),
        "genre": (t.get("genre") or "").strip() or None,
        "plays": t.get("playback_count"),
        "policy": policy,
        "available": available,
    }


SLIM_KEYS = ("id", "kind", "title", "artwork_url", "duration", "full_duration", "permalink_url", "waveform_url",
             "genre", "playback_count", "policy", "streamable")


def _slim(t: dict) -> dict:
    """Only what track_meta() and list matching need — search results are cached in RAM."""
    out = {k: t.get(k) for k in SLIM_KEYS if k in t}
    u, pub = t.get("user") or {}, t.get("publisher_metadata") or {}
    out["user"] = {"username": u.get("username"), "avatar_url": u.get("avatar_url")}
    if pub.get("artist"):
        out["publisher_metadata"] = {"artist": pub["artist"]}
    tcs = (t.get("media") or {}).get("transcodings") or []
    out["media"] = {"transcodings": [{"format": {"protocol": (tc.get("format") or {}).get("protocol")}} for tc in tcs]}
    return out


def _url_expiry(url: str) -> int:
    """CloudFront signed url: the playlist itself is valid only a few minutes (Policy), segments ~2 h."""
    q = parse_qs(urlsplit(url).query)
    try:
        if pol := (q.get("Policy") or [None])[0]:
            raw = base64.b64decode(pol.replace("-", "+").replace("_", "=").replace("~", "/") + "===")
            return int(json.loads(raw)["Statement"][0]["Condition"]["DateLessThan"]["AWS:EpochTime"])
        return int((q.get("expires") or q.get("Expires"))[0])
    except Exception:  # noqa: BLE001
        return int(time.time()) + 240


def _stream_rank(tc: dict) -> int | None:
    """Lower is better. None = not playable in a browser (DRM, opus, abr master)."""
    fmt = tc.get("format") or {}
    proto, mime = fmt.get("protocol"), (fmt.get("mime_type") or "")
    if tc.get("snipped") or proto not in ("progressive", "hls"):
        return None
    if proto == "progressive":
        return 0
    if mime.startswith("audio/mpeg"):
        return 2
    if mime.startswith("audio/mp4"):
        return 1 if "160" in (tc.get("preset") or "") or tc.get("quality") == "sq" else 3
    return None


class SoundCloud:
    def __init__(self, client_id: str | None = None):
        self._client_id = client_id
        self._lock = asyncio.Lock()
        self.session: aiohttp.ClientSession | None = None
        self._streams: dict[int, dict] = {}
        # repeated searches (Ewa, list import, inline mode) — slim objects, ~1 KB per track
        self._search_cache: TTLCache = TTLCache(maxsize=400, ttl=900)

    async def start(self) -> None:
        self.session = aiohttp.ClientSession(
            headers={"User-Agent": UA, "Accept": "application/json"},
            timeout=aiohttp.ClientTimeout(total=30))

    async def close(self) -> None:
        if self.session:
            await self.session.close()

    # ---------- client id ----------
    async def client_id(self, refresh: bool = False) -> str:
        async with self._lock:
            if self._client_id and not refresh:
                return self._client_id
            async with self.session.get("https://soundcloud.com/") as r:
                html = await r.text()
            scripts = re.findall(r'<script crossorigin src="(https://a-v2\.sndcdn\.com/assets/[^"]+\.js)"', html)
            for src in reversed(scripts):
                async with self.session.get(src) as r:
                    js = await r.text()
                m = re.search(r'client_id\s*:\s*"([0-9a-zA-Z]{32})"', js)
                if m:
                    self._client_id = m.group(1)
                    log.info("SoundCloud client_id refreshed")
                    return self._client_id
            raise SoundCloudError("Не удалось получить client_id SoundCloud")

    async def api(self, path: str, **params) -> dict | list:
        url = path if path.startswith("http") else API + path
        for attempt in range(3):
            params["client_id"] = await self.client_id(refresh=attempt == 1)
            async with self.session.get(url, params=params) as r:
                if r.status in (401, 403) and attempt == 0:
                    continue
                if r.status == 404:
                    raise SoundCloudError("Не нашёл это на SoundCloud — ссылка приватная или удалена")
                if r.status == 429:
                    await asyncio.sleep(2 + attempt * 3)
                    continue
                if r.status >= 400:
                    raise SoundCloudError(f"SoundCloud ответил {r.status}")
                return await r.json(content_type=None)
        raise SoundCloudError("SoundCloud временно недоступен")

    # ---------- resolving ----------
    async def expand(self, url: str) -> str:
        host = urlsplit(url).netloc.lower()
        if host.startswith("on.") or "goo.gl" in host:
            async with self.session.get(url, allow_redirects=True) as r:
                url = str(r.url)
        return clean_url(url)

    async def resolve(self, url: str) -> Resolved:
        url = await self.expand(url)
        segs = [s for s in urlsplit(url).path.split("/") if s]
        if not segs or segs[0] in RESERVED:
            raise SoundCloudError("Это не ссылка на трек, плейлист или профиль")
        suffix = segs[1] if len(segs) == 2 else None
        if suffix in ("likes", "tracks", "sets", "albums", "popular-tracks", "reposts"):
            user = await self.api("/resolve", url=f"https://soundcloud.com/{segs[0]}")
            kind = {"likes": "likes", "sets": "user_sets", "albums": "user_sets"}.get(suffix, "user_tracks")
            return Resolved(kind, url, user)
        data = await self.api("/resolve", url=url)
        kind = data.get("kind")
        if kind == "track":
            return Resolved("track", url, data)
        if kind == "playlist":
            return Resolved("album" if data.get("is_album") else "playlist", url, data)
        if kind == "user":
            return Resolved("user", url, data)
        raise SoundCloudError("Такой тип ссылки пока не поддерживается")

    async def tracks_by_ids(self, ids: list[int]) -> list[dict]:
        out: dict[int, dict] = {}
        for i in range(0, len(ids), 50):
            chunk = ids[i:i + 50]
            data = await self.api("/tracks", ids=",".join(map(str, chunk)))
            for t in data:
                out[int(t["id"])] = t
        return [out[i] for i in ids if i in out]

    async def playlist_tracks(self, playlist: dict, limit: int) -> list[dict]:
        stubs = (playlist.get("tracks") or [])[:limit]
        missing = [int(t["id"]) for t in stubs if "title" not in t]
        full = {int(t["id"]): t for t in stubs if "title" in t}
        if missing:
            for t in await self.tracks_by_ids(missing):
                full[int(t["id"])] = t
        return [full[int(t["id"])] for t in stubs if int(t["id"]) in full]

    async def _paginate(self, path: str, limit: int, pick=lambda x: x) -> list[dict]:
        items: list[dict] = []
        data = await self.api(path, limit=min(200, limit), linked_partitioning=1)
        while True:
            for it in data.get("collection", []):
                v = pick(it)
                if v:
                    items.append(v)
            nxt = data.get("next_href")
            if not nxt or len(items) >= limit:
                break
            data = await self.api(nxt)
        return items[:limit]

    async def user_likes(self, user_id: int, limit: int) -> list[dict]:
        return await self._paginate(f"/users/{user_id}/track_likes", limit, lambda x: x.get("track"))

    async def user_tracks(self, user_id: int, limit: int) -> list[dict]:
        return await self._paginate(f"/users/{user_id}/tracks", limit,
                                    lambda x: x if x.get("kind") == "track" else None)

    async def user_playlists(self, user_id: int, limit: int = 50) -> list[dict]:
        return await self._paginate(f"/users/{user_id}/playlists", limit,
                                    lambda x: x if x.get("kind") == "playlist" else None)

    async def search(self, q: str, limit: int = 20) -> list[dict]:
        key = (q.strip().lower(), limit)
        hit = self._search_cache.get(key)
        if hit is not None:
            return hit
        data = await self.api("/search/tracks", q=q, limit=limit)
        out = [_slim(t) for t in data.get("collection", []) if t.get("kind") == "track"]
        self._search_cache[key] = out
        return out

    async def stream_url(self, tid: int) -> dict | None:
        """Signed CDN url the Mini App can play directly (no audio bytes pass through our server).

        SoundCloud serves HLS (AAC 160 in fMP4) with `Access-Control-Allow-Origin: *`; the signature
        is not bound to an IP, so the url resolved here works on the listener's phone.
        """
        hit = self._streams.get(tid)
        if hit and hit["expires"] - time.time() > 90:
            return hit
        t = await self.api(f"/tracks/{tid}")
        tcs = [tc for tc in ((t.get("media") or {}).get("transcodings") or []) if _stream_rank(tc) is not None]
        for tc in sorted(tcs, key=_stream_rank):
            try:
                data = await self.api(tc["url"], track_authorization=t.get("track_authorization"))
            except SoundCloudError:
                continue
            url = data.get("url") if isinstance(data, dict) else None
            if not url:
                continue
            info = {"url": url, "type": tc["format"]["protocol"], "mime": tc["format"].get("mime_type"),
                    "expires": _url_expiry(url)}
            self._streams[tid] = info
            if len(self._streams) > 3000:
                self._streams.pop(next(iter(self._streams)))
            return info
        return None

    async def waveform(self, url: str | None, bars: int = 96) -> list[float]:
        if not url:
            return []
        url = url.replace(".png", ".json")
        try:
            async with self.session.get(url) as r:
                data = await r.json(content_type=None)
        except Exception:  # noqa: BLE001
            return []
        samples, height = data.get("samples") or [], data.get("height") or 140
        if not samples:
            return []
        step = len(samples) / bars
        raw = [max(samples[int(i * step):max(int((i + 1) * step), int(i * step) + 1)]) / height
               for i in range(bars)]
        lo, hi = min(raw), max(raw)
        span = (hi - lo) or 1
        # stretch dynamics so loud masters don't look like a flat line
        return [round(0.18 + 0.82 * ((v - lo) / span) ** 1.6, 3) for v in raw]
