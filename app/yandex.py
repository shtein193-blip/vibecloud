"""Read the track list (titles only) of a PUBLIC Yandex Music playlist / album / track by its share link.

No audio and no account access: we read the same names the user sees on the playlist page and then look for
the songs among public SoundCloud tracks (see listimport.py). Private playlists simply can't be read.
"""
from __future__ import annotations

import logging
import re
import time

import aiohttp

from .listimport import VARIANT

log = logging.getLogger(__name__)

API = "https://api.music.yandex.net"
HEADERS = {"X-Yandex-Music-Client": "YandexMusicAndroid/24023621", "User-Agent": "Yandex-Music-API",
           "Accept-Language": "ru", "Accept": "application/json"}
LINK_RE = re.compile(r"https?://(?:www\.)?music\.yandex\.(?:ru|com|by|kz|uz|com\.tr)/[^\s<>\"']+", re.I)
_cache: dict[str, tuple[float, dict]] = {}


class YandexError(Exception):
    pass


def find_link(text: str | None) -> str | None:
    m = LINK_RE.search(text or "")
    return m.group(0).rstrip(".,;!?)") if m else None


def parse(url: str) -> tuple[str, tuple]:
    path = re.sub(r"[?#].*$", "", url.split("://", 1)[-1].split("/", 1)[-1]).strip("/")
    seg = path.split("/")
    if len(seg) >= 4 and seg[0] == "users" and seg[2] == "playlists":
        return "user_playlist", (seg[1], seg[3])
    if len(seg) >= 2 and seg[0] == "playlists":
        return "playlist_uuid", (seg[1],)
    if len(seg) >= 4 and seg[0] == "album" and seg[2] == "track":
        return "track", (seg[3],)
    if len(seg) >= 2 and seg[0] == "track":
        return "track", (seg[1],)
    if len(seg) >= 2 and seg[0] == "album":
        return "album", (seg[1],)
    raise YandexError("Это ссылка на Яндекс Музыку, но не на плейлист, альбом или трек. "
                      "В приложении открой плейлист → «Поделиться» → «Скопировать ссылку».")


def _item(t: dict) -> dict | None:
    title = (t.get("title") or "").strip()
    if not title:
        return None
    version = (t.get("version") or "").strip()
    if version and VARIANT.search(version):  # keep "Remix"/"Sped Up" so we don't match the original instead
        title = f"{title} ({version})"
    artists = ", ".join(a.get("name", "") for a in t.get("artists") or [] if a.get("name"))
    dur = t.get("durationMs")
    return {"artist": artists, "title": title, "duration": int(dur / 1000) if dur else None}


async def _get(http: aiohttp.ClientSession, path: str, **kw) -> dict:
    method = "POST" if "data" in kw else "GET"
    async with http.request(method, API + path, headers=HEADERS, **kw) as r:
        data = await r.json(content_type=None)
        if r.status in (401, 403):
            raise YandexError("Плейлист закрытый — в Яндекс Музыке сделай его открытым или пришли список файлом.")
        if r.status == 404:
            raise YandexError("Не нашёл такой плейлист — проверь ссылку (или он закрытый).")
        if r.status >= 400 or not isinstance(data, dict):
            raise YandexError(f"Яндекс Музыка ответила ошибкой {r.status}. Попробуй ещё раз чуть позже.")
        return data.get("result") or {}


async def _full_tracks(http: aiohttp.ClientSession, shorts: list[dict]) -> list[dict]:
    """Big playlists may return only track ids — fetch the names in batches."""
    tracks, missing = [], []
    for s in shorts:
        if isinstance(s.get("track"), dict):
            tracks.append(s["track"])
        elif s.get("id"):
            missing.append(f"{s['id']}:{s['albumId']}" if s.get("albumId") else str(s["id"]))
    for i in range(0, len(missing), 100):
        res = await _get(http, "/tracks", data={"track-ids": ",".join(missing[i:i + 100]), "with-positions": "false"})
        tracks += res if isinstance(res, list) else []
    return tracks


async def fetch(url: str, limit: int = 1500) -> dict:
    """{"title": str, "items": [{"artist","title","duration"}]} for a public playlist/album/track link."""
    hit = _cache.get(url)
    if hit and time.monotonic() - hit[0] < 600:
        return hit[1]
    kind, args = parse(url)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40)) as http:
            if kind == "user_playlist":
                pl = await _get(http, f"/users/{args[0]}/playlists/{args[1]}")
                title, tracks = pl.get("title") or "Плейлист", await _full_tracks(http, pl.get("tracks") or [])
            elif kind == "playlist_uuid":
                pl = await _get(http, f"/playlist/{args[0]}")
                title, tracks = pl.get("title") or "Плейлист", await _full_tracks(http, pl.get("tracks") or [])
            elif kind == "album":
                al = await _get(http, f"/albums/{args[0]}/with-tracks")
                title = al.get("title") or "Альбом"
                tracks = [t for vol in al.get("volumes") or [] for t in vol]
            else:
                res = await _get(http, f"/tracks/{args[0]}")
                tracks = res if isinstance(res, list) else []
                title = tracks[0].get("title", "Трек") if tracks else "Трек"
    except YandexError:
        raise
    except Exception as ex:  # noqa: BLE001
        log.warning("yandex fetch %s failed: %r", url, ex)
        raise YandexError("Не получилось открыть Яндекс Музыку — проверь интернет/VPN и попробуй ещё раз, "
                          "или пришли список файлом.")
    items = [it for it in (_item(t) for t in tracks) if it][:limit]
    if not items:
        raise YandexError("В этом плейлисте не вижу треков — он пустой или закрытый.")
    out = {"title": title[:60], "items": items}
    _cache[url] = (time.monotonic(), out)
    if len(_cache) > 200:
        _cache.pop(next(iter(_cache)))
    return out
