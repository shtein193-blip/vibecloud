"""Legal import from Yandex Music (or any other service): the user brings their own track list.

We never touch Yandex servers. The user exports/copies the list of their tracks (text, CSV, JSON),
we parse "artist — title" pairs and look for the same songs among public SoundCloud tracks.
Found tracks go to the library and to a playlist; the rest is shown in a report with a search button.
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
import re
import time
from difflib import SequenceMatcher

from .config import Settings
from .db import DB, now
from .events import EventBus
from .security import Signer
from .serialize import playlist_out, track_out
from .soundcloud import SoundCloud, SoundCloudError, track_meta

log = logging.getLogger(__name__)

# ------------------------------------------------------------------ parsing
DUR_RE = re.compile(r"^\s*(\d{1,2}:)?\d{1,2}:\d{2}\s*$")
NUM_RE = re.compile(r"^\s*(\d{1,4})[.)]?\s+")
SEP_RE = re.compile(r"\s+[—–-]\s+|\s+[—–]\s*|\s*[—–]\s+|\t")
JUNK_LINE = re.compile(
    r"^(мне нравится|плейлист|playlist|треки|tracks|исполнитель|artist|альбом|album|длительность|"
    r"слушать|воспроизвести|play|скачать|поделиться|добавить|ещё|еще|explicit|e|\d+ (треков|трека|трек).*|"
    r"\d+ (ч|мин).*|яндекс.?музыка.*|music\.yandex\..*|https?://.*)$", re.I)
TITLE_KEYS = ("title", "track", "track name", "name", "song", "название", "трек", "песня", "track title")
ARTIST_KEYS = ("artist", "artists", "artist name", "artist name(s)", "исполнитель", "исполнители", "артист", "band")
DUR_KEYS = ("duration", "length", "длительность", "time", "duration (ms)", "duration_ms")


def _dur_to_sec(v) -> int | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return int(v / 1000) if v > 10000 else int(v)
    v = str(v).strip()
    if v.isdigit():
        n = int(v)
        return n // 1000 if n > 10000 else n
    if DUR_RE.match(v):
        parts = [int(x) for x in v.split(":")]
        return parts[-1] + parts[-2] * 60 + (parts[0] * 3600 if len(parts) == 3 else 0)
    return None


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace(" ", " ")).strip(" \t\"'«»")


def _item(artist: str, title: str, dur=None) -> dict | None:
    artist, title = _clean(artist), _clean(title)
    if not title or len(title) > 200 or len(artist) > 200:
        return None
    return {"artist": artist, "title": title, "duration": _dur_to_sec(dur)}


def _from_json(data) -> list[dict]:
    if isinstance(data, dict):
        for key in ("tracks", "items", "result", "data", "library", "likes"):
            if isinstance(data.get(key), (list, dict)):
                return _from_json(data[key])
        return []
    out = []
    for x in data if isinstance(data, list) else []:
        if isinstance(x, str):
            out += parse_lines(x)
            continue
        if not isinstance(x, dict):
            continue
        x = x.get("track") if isinstance(x.get("track"), dict) else x
        low = {str(k).lower(): v for k, v in x.items()}
        title = next((low[k] for k in TITLE_KEYS if isinstance(low.get(k), str)), "")
        art = next((low[k] for k in ARTIST_KEYS if k in low), "")
        if isinstance(art, list):  # Yandex-like: [{"name": ...}]
            art = ", ".join(a.get("name", "") if isinstance(a, dict) else str(a) for a in art)
        elif isinstance(art, dict):
            art = art.get("name", "")
        dur = next((low[k] for k in DUR_KEYS if k in low), None) or low.get("durationms")
        if it := _item(str(art), str(title), dur):
            out.append(it)
    return out


def _from_csv(text: str) -> list[dict] | None:
    first = text.lstrip("﻿").splitlines()[0] if text.strip() else ""
    delim = max((";", ",", "\t"), key=first.count)
    if first.count(delim) < 1:
        return None
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿")), delimiter=delim))
    head = [h.strip().lower() for h in rows[0]]
    ti = next((head.index(k) for k in TITLE_KEYS if k in head), None)
    ai = next((head.index(k) for k in ARTIST_KEYS if k in head), None)
    if ti is None:
        return None
    di = next((head.index(k) for k in DUR_KEYS if k in head), None)
    out = []
    for r in rows[1:]:
        if len(r) <= ti:
            continue
        it = _item(r[ai] if ai is not None and ai < len(r) else "", r[ti],
                   r[di] if di is not None and di < len(r) else None)
        if it:
            out.append(it)
    return out


def parse_lines(text: str) -> list[dict]:
    """Plain text: "Artist — Title" per line, or blocks copied from music.yandex.ru (title / artist / 3:45)."""
    lines = [_clean(NUM_RE.sub("", l)) for l in text.splitlines()]
    lines = [l for l in lines if l]
    out: list[dict] = []
    sep_lines = sum(1 for l in lines if SEP_RE.search(l))
    if lines and sep_lines >= max(1, len(lines) * 0.5):
        for l in lines:
            parts = [p for p in SEP_RE.split(l, maxsplit=1) if p.strip()]
            if len(parts) == 2:
                # strip a trailing duration: "Artist — Title 3:45"
                m = re.search(r"\s+((\d{1,2}:)?\d{1,2}:\d{2})$", parts[1])
                dur = m.group(1) if m else None
                title = parts[1][:m.start()] if m else parts[1]
                if it := _item(parts[0], title, dur):
                    out.append(it)
        return out
    # block format: every track ends with a duration line; before it — title, then artist(s)
    if sum(1 for l in lines if DUR_RE.match(l)) >= 1:
        block: list[str] = []
        for l in lines:
            if DUR_RE.match(l):
                block = [b for b in block if not JUNK_LINE.match(b)]
                if len(block) >= 2:
                    if it := _item(block[-1], block[-2], l):
                        out.append(it)
                elif len(block) == 1:
                    if it := _item("", block[0], l):
                        out.append(it)
                block = []
            else:
                block.append(l)
        if out:
            return out
    # no separators, no durations: alternating title / artist lines
    clean = [l for l in lines if not JUNK_LINE.match(l)]
    if len(clean) >= 2 and len(clean) % 2 == 0:
        for i in range(0, len(clean), 2):
            if it := _item(clean[i + 1], clean[i]):
                out.append(it)
    return out


def parse_tracklist(text: str) -> list[dict]:
    text = (text or "").strip()
    if not text:
        return []
    items: list[dict] | None = None
    if text[0] in "[{":
        try:
            items = _from_json(json.loads(text))
        except ValueError:
            items = None
    if items is None and "\n" in text:
        try:
            items = _from_csv(text)
        except csv.Error:
            items = None
    if not items:
        items = parse_lines(text)
    seen, out = set(), []
    for it in items:
        k = match_key(it["artist"], it["title"])
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out


# ------------------------------------------------------------------ matching
TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s",
                     "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))
NOISE = re.compile(r"\b(official|video|audio|lyrics?|clip|клип|hd|hq|mv|prod\.?[^)\]]*|премьера)\b", re.I)
VARIANT = re.compile(r"\b(remix|ремикс|cover|кавер|sped ?up|slowed|nightcore|reverb|8d|edit|instrumental|"
                     r"минус|karaoke|караоке|live|mashup|bootleg|flip|vip|acoustic|acapella|bass ?boost(ed)?)\b", re.I)
FEAT = re.compile(r"\s*[(\[]?\s*\b(feat|ft|featuring|при уч\.?)\b\.?.*?([)\]]|$)", re.I)


def translit(s: str) -> str:
    return "".join(TRANSLIT.get(c, c) for c in s)


def norm(s: str) -> str:
    s = (s or "").lower().replace("ё", "е").replace("&", " and ").replace("$", "s")
    s = FEAT.sub(" ", s)
    s = NOISE.sub(" ", s)
    s = re.sub(r"[(\[].*?[)\]]", lambda m: m.group(0) if VARIANT.search(m.group(0)) else " ", s)
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def match_key(artist: str, title: str) -> str:
    return f"{norm(artist)}|{norm(title)}"


def _sim(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    best = 0.0
    for x in (a, translit(a)):
        for y in (b, translit(b)):
            if x == y:
                return 1.0
            r = SequenceMatcher(None, x, y).ratio()
            # one string fully contains the other (e.g. "title" vs "title original mix")
            if len(x) >= 4 and len(y) >= 4 and (x in y or y in x):
                r = max(r, 0.9)
            ta, tb = set(x.split()), set(y.split())
            if ta and tb:
                r = max(r, len(ta & tb) / len(ta | tb))
            best = max(best, r)
    return best


def score(item: dict, t: dict) -> float:
    """0..1 similarity between a list item and a SoundCloud track object."""
    meta_artist = norm((t.get("publisher_metadata") or {}).get("artist") or "")
    uploader = norm((t.get("user") or {}).get("username") or "")
    sc_title = norm(t.get("title") or "")
    want_t, want_a = norm(item["title"]), norm(item["artist"])
    # SoundCloud titles are often "Artist - Title"
    parts = re.split(r"\s+-\s+|\s+—\s+", (t.get("title") or "").lower(), maxsplit=1)
    alt_title = norm(parts[1]) if len(parts) == 2 else sc_title
    alt_artist = norm(parts[0]) if len(parts) == 2 else ""
    ts = max(_sim(want_t, sc_title), _sim(want_t, alt_title))
    if want_a:
        artists = [p.strip() for p in re.split(r",| and | x | и ", want_a) if p.strip()] or [want_a]
        cands = [x for x in (meta_artist, uploader, alt_artist) if x]
        sa = max((_sim(a, c) for a in artists for c in cands), default=0.0)
        if not sa and want_a and want_a in sc_title:
            sa = 0.85
        s = 0.62 * ts + 0.38 * sa
    else:
        s = ts * 0.9
    # a remix / cover / sped-up version when the original was asked for
    if VARIANT.search((t.get("title") or "").lower()) and not VARIANT.search(item["title"].lower()):
        s -= 0.3
    dur = (t.get("full_duration") or t.get("duration") or 0) / 1000
    if item.get("duration") and dur:
        diff = abs(dur - item["duration"])
        s += 0.06 if diff <= 4 else -0.12 if diff > 45 else 0
    if t.get("policy") == "SNIP" or (t.get("duration") and t.get("full_duration")
                                     and t["duration"] < t["full_duration"] - 5000):
        s -= 0.08  # Go+ preview only
    s += min((t.get("playback_count") or 0) / 5_000_000, 0.03)  # tie-break towards the popular upload
    return s


THRESHOLD = 0.66


class ListImporter:
    def __init__(self, s: Settings, db: DB, sc: SoundCloud, bus: EventBus, signer: Signer):
        self.s, self.db, self.sc, self.bus, self.signer = s, db, sc, bus, signer
        self.sem = asyncio.Semaphore(3)  # SoundCloud search concurrency (rate limits)
        self.running: dict[int, str] = {}

    async def find(self, item: dict) -> tuple[dict | None, float]:
        queries = [f"{item['artist']} {item['title']}".strip()]
        if item["artist"]:
            queries.append(item["title"])
            if re.search("[а-яё]", item["artist"] + item["title"], re.I):
                queries.append(translit(f"{item['artist']} {item['title']}".lower()))
        best, best_s = None, 0.0
        for q in queries:
            async with self.sem:
                try:
                    res = await self.sc.search(q, 12)
                except SoundCloudError as ex:
                    log.info("list search %r failed: %s", q, ex)
                    continue
            for t in res:
                sc_ = score(item, t)
                if sc_ > best_s:
                    best, best_s = t, sc_
            if best_s >= 0.85:
                break
        return (best, best_s) if best_s >= THRESHOLD else (None, best_s)

    async def run(self, uid: int, text: str, title: str | None = None, on_progress=None) -> dict:
        if uid in self.running:
            raise SoundCloudError("Список уже переносится — дождись окончания")
        items = parse_tracklist(text)[: self.s.list_import_max]
        if not items:
            raise SoundCloudError("Не нашёл в тексте треков. Нужен формат «Исполнитель — Название» "
                                  "по строке, CSV или текст, скопированный со страницы плейлиста.")
        title = (title or "").strip()[:60] or "Яндекс Музыка"
        job_id = await self.db.create_job(uid, f"list:{len(items)}", "list", title, None, len(items))
        self.running[uid] = job_id
        self.bus.publish(uid, "job", {"id": job_id, "status": "running", "kind": "list", "title": title,
                                      "artwork": None, "total": len(items), "added": 0, "found": 0})
        try:
            return await self._run(uid, job_id, items, title, on_progress)
        except Exception as ex:
            await self.db.update_job(job_id, status="error", error=str(ex)[:200])
            self.bus.publish(uid, "job", {"id": job_id, "status": "error", "error": str(ex)})
            raise
        finally:
            self.running.pop(uid, None)

    async def _run(self, uid: int, job_id: str, items: list[dict], title: str, on_progress) -> dict:
        keys = [match_key(i["artist"], i["title"]) for i in items]
        cached = await self.db.cached_matches(keys)
        found: dict[int, int] = {}          # item index -> track id
        missing: list[int] = []
        done = 0
        artwork = None
        lock = asyncio.Lock()
        last_emit = 0.0
        sid = hashlib.sha1("\n".join(keys).encode()).hexdigest()[:12]
        pid = await self.db.upsert_playlist(uid, {"kind": "imported", "title": title, "artist": "Перенос по списку",
                                                  "source_url": f"list:{sid}"})

        async def publish(i: int, tid: int) -> None:
            nonlocal artwork
            row = (await self.db.library_entries(uid, [tid]))[tid]
            artwork = artwork or row.get("artwork")
            self.bus.publish(uid, "track_added", {"job": job_id, "playlist": pid,
                                                  "track": track_out(row, uid, self.signer)})

        async def handle(i: int, item: dict) -> None:
            nonlocal done, last_emit
            key = keys[i]
            if key in cached:
                tid = cached[key]
            else:
                t, sc_ = await self.find(item)
                tid = None
                if t:
                    m = track_meta(t)
                    await self.db.upsert_tracks([m])
                    tid = m["id"]
                await self.db.save_match(key, tid, sc_)
            async with lock:
                done += 1
                if tid:
                    found[i] = tid
                    await self.db.add_many_to_library(uid, [tid])
                    await publish(i, tid)
                else:
                    missing.append(i)
                if time.monotonic() - last_emit > 0.7 or done == len(items):
                    last_emit = time.monotonic()
                    self.bus.publish(uid, "job", {"id": job_id, "status": "running", "kind": "list", "title": title,
                                                  "artwork": artwork, "total": len(items), "added": done,
                                                  "found": len(found)})
                    await self.db.update_job(job_id, added=done)
                    if on_progress:
                        await on_progress(done, len(items), len(found))

        # cached hits are instant; network lookups run with limited concurrency
        await asyncio.gather(*(handle(i, it) for i, it in enumerate(items)))
        order = [found[i] for i in sorted(found)]
        await self.db.set_playlist_tracks(pid, list(dict.fromkeys(order)))
        if artwork:
            await self.db.run("UPDATE playlists SET artwork=? WHERE id=?", (artwork, pid))
        report = [{"artist": items[i]["artist"], "title": items[i]["title"]} for i in sorted(missing)]
        await self.db.update_job(job_id, status="done", added=len(items), playlist_id=pid, finished_at=now(),
                                 report=json.dumps(report, ensure_ascii=False))
        pl = await self.db.get_playlist(pid)
        result = {"job_id": job_id, "playlist_id": pid, "title": title, "total": len(items),
                  "found": len(found), "missing": report}
        self.bus.publish(uid, "job", {"id": job_id, "status": "done", "kind": "list", "title": title,
                                      "artwork": artwork, "total": len(items), "added": len(items),
                                      "found": len(found), "missing": len(report),
                                      "playlist": playlist_out(pl) if pl else None})
        return result
