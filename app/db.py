from __future__ import annotations

import secrets
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import aiosqlite

PRAGMAS = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;      -- WAL + NORMAL: durable enough, no fsync on every commit
PRAGMA foreign_keys=ON;
PRAGMA temp_store=MEMORY;
PRAGMA cache_size=-8000;        -- ~8 MB page cache, enough for the whole DB of a small bot
PRAGMA busy_timeout=5000;
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY,
    first_name TEXT, username TEXT, photo_url TEXT, lang TEXT,
    downloads INTEGER DEFAULT 0,
    created_at INTEGER, last_seen INTEGER
);

CREATE TABLE IF NOT EXISTS tracks(
    id INTEGER PRIMARY KEY,            -- SoundCloud track id
    title TEXT, artist TEXT, artwork TEXT, duration_ms INTEGER,
    permalink TEXT, waveform_url TEXT, genre TEXT, plays INTEGER, policy TEXT,
    status TEXT DEFAULT 'cloud',       -- cloud | queued | downloading | ready | unavailable | error
    file_path TEXT, file_size INTEGER, tg_file_id TEXT, error TEXT,
    last_access INTEGER, updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS library(
    user_id INTEGER, track_id INTEGER,
    added_at INTEGER, liked INTEGER DEFAULT 0,
    play_count INTEGER DEFAULT 0, last_played INTEGER,
    PRIMARY KEY(user_id, track_id)
);
CREATE INDEX IF NOT EXISTS library_track ON library(track_id);

CREATE TABLE IF NOT EXISTS playlists(
    id TEXT PRIMARY KEY, user_id INTEGER, sc_id INTEGER,
    kind TEXT,                          -- playlist | album | likes | user_tracks | mixtape
    title TEXT, artist TEXT, artwork TEXT, permalink TEXT, source_url TEXT,
    watch INTEGER DEFAULT 0, created_at INTEGER, synced_at INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS playlists_user_src ON playlists(user_id, source_url);

CREATE TABLE IF NOT EXISTS playlist_tracks(
    playlist_id TEXT, track_id INTEGER, pos INTEGER,
    PRIMARY KEY(playlist_id, track_id)
);

CREATE TABLE IF NOT EXISTS jobs(
    id TEXT PRIMARY KEY, user_id INTEGER, url TEXT, kind TEXT,
    title TEXT, artwork TEXT, playlist_id TEXT,
    total INTEGER DEFAULT 0, added INTEGER DEFAULT 0,
    status TEXT, error TEXT, created_at INTEGER, finished_at INTEGER
);
CREATE INDEX IF NOT EXISTS jobs_user ON jobs(user_id, created_at);

CREATE TABLE IF NOT EXISTS mixtapes(
    code TEXT PRIMARY KEY, owner_id INTEGER, title TEXT, theme TEXT, note TEXT,
    created_at INTEGER, claims INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS mixtape_tracks(
    code TEXT, track_id INTEGER, pos INTEGER, PRIMARY KEY(code, track_id)
);

CREATE TABLE IF NOT EXISTS listens(user_id INTEGER, track_id INTEGER, ts INTEGER);
CREATE INDEX IF NOT EXISTS listens_user ON listens(user_id, ts);
CREATE INDEX IF NOT EXISTS playlist_tracks_track ON playlist_tracks(track_id);
CREATE INDEX IF NOT EXISTS playlists_watch ON playlists(watch) WHERE watch=1;

-- real listening time (seconds), one row per user per UTC day → leaderboard
CREATE TABLE IF NOT EXISTS listen_time(
    user_id INTEGER, day TEXT, seconds INTEGER DEFAULT 0,
    PRIMARY KEY(user_id, day)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS listen_time_day ON listen_time(day);

-- paired ("duo") playlists between two users
CREATE TABLE IF NOT EXISTS duos(
    id TEXT PRIMARY KEY, title TEXT, a INTEGER, b INTEGER,
    state TEXT DEFAULT 'pending',       -- pending | active
    created_at INTEGER, updated_at INTEGER
);
CREATE INDEX IF NOT EXISTS duos_a ON duos(a);
CREATE INDEX IF NOT EXISTS duos_b ON duos(b);
CREATE TABLE IF NOT EXISTS duo_tracks(
    duo_id TEXT, track_id INTEGER, added_by INTEGER, added_at INTEGER,
    PRIMARY KEY(duo_id, track_id)
) WITHOUT ROWID;

-- "artist — title" → SoundCloud track id (NULL = not found), shared by all users of list import
CREATE TABLE IF NOT EXISTS match_cache(
    key TEXT PRIMARY KEY, track_id INTEGER, score REAL, ts INTEGER
) WITHOUT ROWID;
"""

# columns added after the first release: (table, column, declaration)
MIGRATIONS = [
    ("users", "status", "TEXT"),
    ("users", "hidden", "INTEGER DEFAULT 0"),
    ("jobs", "report", "TEXT"),
]

TRACK_META_FIELDS = (
    "title", "artist", "artwork", "duration_ms", "permalink",
    "waveform_url", "genre", "plays", "policy",
)


def now() -> int:
    return int(time.time())


def new_id(n: int = 8) -> str:
    return secrets.token_urlsafe(n)[:n].replace("-", "x").replace("_", "z")


class DB:
    def __init__(self, path: Path):
        self.path = path
        self.conn: aiosqlite.Connection | None = None
        self._seen: dict[int, tuple[int, tuple]] = {}

    async def connect(self) -> None:
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(PRAGMAS)
        await self.conn.executescript(SCHEMA)
        for table, col, decl in MIGRATIONS:
            cols = {r[1] for r in await (await self.conn.execute(f"PRAGMA table_info({table})")).fetchall()}
            if col not in cols:
                await self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        await self.conn.commit()
        await self._backfill_listen_time()

    async def _backfill_listen_time(self) -> None:
        """First start with the leaderboard: estimate past listening from the old `listens` log."""
        if await self.one("SELECT 1 AS x FROM listen_time LIMIT 1") or not await self.one("SELECT 1 AS x FROM listens LIMIT 1"):
            return
        await self.run(
            """INSERT OR IGNORE INTO listen_time(user_id, day, seconds)
               SELECT l.user_id, date(l.ts, 'unixepoch'), SUM(MIN(COALESCE(t.duration_ms, 180000) / 1000, 600))
               FROM listens l LEFT JOIN tracks t ON t.id=l.track_id GROUP BY 1, 2""")

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    # ---------- low level ----------
    async def run(self, sql: str, params: Iterable[Any] = ()) -> int:
        cur = await self.conn.execute(sql, tuple(params))
        await self.conn.commit()
        return cur.rowcount

    async def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        cur = await self.conn.execute(sql, tuple(params))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def all(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        cur = await self.conn.execute(sql, tuple(params))
        return [dict(r) for r in await cur.fetchall()]

    # ---------- users ----------
    async def upsert_user(self, u: dict) -> None:
        # every API call authenticates the user — write only when something changed or every 5 min
        key = (u.get("first_name"), u.get("username"), u.get("photo_url"), u.get("language_code"))
        seen = self._seen.get(u["id"])
        if seen and seen[1] == key and now() - seen[0] < 300:
            return
        self._seen[u["id"]] = (now(), key)
        if len(self._seen) > 5000:
            self._seen.clear()
        await self.run(
            """INSERT INTO users(id, first_name, username, photo_url, lang, created_at, last_seen)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET first_name=excluded.first_name,
                 username=excluded.username,
                 photo_url=COALESCE(excluded.photo_url, users.photo_url),
                 lang=excluded.lang, last_seen=excluded.last_seen""",
            (u["id"], u.get("first_name"), u.get("username"), u.get("photo_url"),
             u.get("language_code"), now(), now()),
        )

    async def get_user(self, uid: int) -> dict | None:
        return await self.one("SELECT * FROM users WHERE id=?", (uid,))

    async def inc_downloads(self, uid: int, n: int = 1) -> None:
        await self.run("UPDATE users SET downloads=downloads+? WHERE id=?", (n, uid))

    # ---------- tracks ----------
    async def upsert_track(self, meta: dict) -> None:
        cols = ", ".join(TRACK_META_FIELDS)
        marks = ", ".join("?" for _ in TRACK_META_FIELDS)
        updates = ", ".join(f"{c}=excluded.{c}" for c in TRACK_META_FIELDS)
        await self.run(
            f"""INSERT INTO tracks(id, {cols}, status, updated_at) VALUES(?, {marks}, ?, ?)
                ON CONFLICT(id) DO UPDATE SET {updates}, updated_at=excluded.updated_at""",
            (meta["id"], *(meta.get(c) for c in TRACK_META_FIELDS),
             "unavailable" if not meta.get("available", True) else "cloud", now()),
        )

    async def get_track(self, tid: int) -> dict | None:
        return await self.one("SELECT * FROM tracks WHERE id=?", (tid,))

    async def update_track(self, tid: int, **fields: Any) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        await self.run(f"UPDATE tracks SET {sets}, updated_at=? WHERE id=?",
                       (*fields.values(), now(), tid))

    async def owners(self, tid: int) -> list[int]:
        rows = await self.all("SELECT user_id FROM library WHERE track_id=?", (tid,))
        return [r["user_id"] for r in rows]

    async def touch_track(self, tid: int) -> None:
        await self.run("UPDATE tracks SET last_access=? WHERE id=?", (now(), tid))

    async def cached_files(self) -> list[dict]:
        return await self.all(
            "SELECT id, file_path, file_size, last_access, tg_file_id FROM tracks "
            "WHERE file_path IS NOT NULL ORDER BY (tg_file_id IS NULL), COALESCE(last_access, updated_at) ASC")

    # ---------- library ----------
    async def add_to_library(self, uid: int, tid: int) -> bool:
        n = await self.run(
            "INSERT OR IGNORE INTO library(user_id, track_id, added_at) VALUES(?,?,?)",
            (uid, tid, now()))
        return n > 0

    async def remove_from_library(self, uid: int, tid: int) -> None:
        await self.run("DELETE FROM library WHERE user_id=? AND track_id=?", (uid, tid))
        await self.run(
            "DELETE FROM playlist_tracks WHERE track_id=? AND playlist_id IN "
            "(SELECT id FROM playlists WHERE user_id=?)", (tid, uid))

    async def library(self, uid: int, limit: int = 2000) -> list[dict]:
        return await self.all(
            """SELECT t.*, l.added_at, l.liked, l.play_count, l.last_played
               FROM library l JOIN tracks t ON t.id=l.track_id
               WHERE l.user_id=? ORDER BY l.added_at DESC, l.rowid ASC LIMIT ?""", (uid, limit))

    async def library_entry(self, uid: int, tid: int) -> dict | None:
        return await self.one(
            """SELECT t.*, l.added_at, l.liked, l.play_count, l.last_played
               FROM library l JOIN tracks t ON t.id=l.track_id
               WHERE l.user_id=? AND l.track_id=?""", (uid, tid))

    async def upsert_tracks(self, metas: list[dict]) -> None:
        """Batch upsert in one transaction (a 500-track playlist = 1 commit instead of 500)."""
        if not metas:
            return
        cols = ", ".join(TRACK_META_FIELDS)
        marks = ", ".join("?" for _ in TRACK_META_FIELDS)
        updates = ", ".join(f"{c}=excluded.{c}" for c in TRACK_META_FIELDS)
        ts = now()
        await self.conn.executemany(
            f"""INSERT INTO tracks(id, {cols}, status, updated_at) VALUES(?, {marks}, ?, ?)
                ON CONFLICT(id) DO UPDATE SET {updates}, updated_at=excluded.updated_at""",
            [(m["id"], *(m.get(c) for c in TRACK_META_FIELDS),
              "unavailable" if not m.get("available", True) else "cloud", ts) for m in metas])
        await self.conn.commit()

    async def add_many_to_library(self, uid: int, tids: list[int]) -> set[int]:
        """Returns the ids that were not in the library before."""
        if not tids:
            return set()
        have = await self.track_ids_in_library(uid, tids)
        ts = now()
        await self.conn.executemany(
            "INSERT OR IGNORE INTO library(user_id, track_id, added_at) VALUES(?,?,?)",
            [(uid, t, ts) for t in tids])
        await self.conn.commit()
        return set(tids) - have

    async def track_ids_in_library(self, uid: int, tids: list[int]) -> set[int]:
        out: set[int] = set()
        for i in range(0, len(tids), 500):
            chunk = tids[i:i + 500]
            rows = await self.all(f"SELECT track_id FROM library WHERE user_id=? AND track_id IN "
                                  f"({','.join('?' * len(chunk))})", (uid, *chunk))
            out |= {r["track_id"] for r in rows}
        return out

    async def library_entries(self, uid: int, tids: list[int]) -> dict[int, dict]:
        out: dict[int, dict] = {}
        for i in range(0, len(tids), 500):
            chunk = tids[i:i + 500]
            for r in await self.all(
                    f"""SELECT t.*, l.added_at, l.liked, l.play_count, l.last_played
                        FROM tracks t LEFT JOIN library l ON l.track_id=t.id AND l.user_id=?
                        WHERE t.id IN ({','.join('?' * len(chunk))})""", (uid, *chunk)):
                out[r["id"]] = r
        return out

    async def set_like(self, uid: int, tid: int, liked: bool) -> None:
        await self.run("UPDATE library SET liked=? WHERE user_id=? AND track_id=?",
                       (int(liked), uid, tid))

    async def add_listen(self, uid: int, tid: int) -> None:
        await self.conn.execute("INSERT INTO listens(user_id, track_id, ts) VALUES(?,?,?)", (uid, tid, now()))
        await self.run("UPDATE library SET play_count=play_count+1, last_played=? "
                       "WHERE user_id=? AND track_id=?", (now(), uid, tid))

    async def add_listen_time(self, uid: int, seconds: int) -> None:
        if seconds <= 0:
            return
        day = datetime.now(timezone.utc).date().isoformat()
        await self.run("INSERT INTO listen_time(user_id, day, seconds) VALUES(?,?,?) "
                       "ON CONFLICT(user_id, day) DO UPDATE SET seconds=seconds+excluded.seconds",
                       (uid, day, int(seconds)))

    async def search_library(self, uid: int, q: str, limit: int = 20) -> list[dict]:
        like = f"%{q.lower()}%"
        return await self.all(
            """SELECT t.* FROM library l JOIN tracks t ON t.id=l.track_id
               WHERE l.user_id=? AND (lower(t.title) LIKE ? OR lower(t.artist) LIKE ?)
               ORDER BY l.last_played DESC, l.added_at DESC LIMIT ?""", (uid, like, like, limit))

    # ---------- playlists ----------
    async def upsert_playlist(self, uid: int, p: dict) -> str:
        existing = await self.one("SELECT id FROM playlists WHERE user_id=? AND source_url=?",
                                  (uid, p["source_url"]))
        if existing:
            await self.run(
                "UPDATE playlists SET title=?, artist=?, artwork=?, synced_at=? WHERE id=?",
                (p.get("title"), p.get("artist"), p.get("artwork"), now(), existing["id"]))
            return existing["id"]
        pid = new_id()
        await self.run(
            """INSERT INTO playlists(id, user_id, sc_id, kind, title, artist, artwork, permalink,
               source_url, created_at, synced_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (pid, uid, p.get("sc_id"), p.get("kind", "playlist"), p.get("title"), p.get("artist"),
             p.get("artwork"), p.get("permalink"), p["source_url"], now(), now()))
        return pid

    async def set_playlist_tracks(self, pid: str, track_ids: list[int]) -> list[int]:
        """Replace order; returns track ids that are new for this playlist."""
        old = {r["track_id"] for r in await self.all(
            "SELECT track_id FROM playlist_tracks WHERE playlist_id=?", (pid,))}
        await self.conn.execute("DELETE FROM playlist_tracks WHERE playlist_id=?", (pid,))
        await self.conn.executemany(
            "INSERT OR IGNORE INTO playlist_tracks(playlist_id, track_id, pos) VALUES(?,?,?)",
            [(pid, tid, i) for i, tid in enumerate(track_ids)])
        await self.conn.commit()
        return [t for t in track_ids if t not in old]

    async def get_playlist(self, pid: str) -> dict | None:
        return await self.one(
            """SELECT p.*, COUNT(pt.track_id) AS track_count,
                      COALESCE(SUM(t.duration_ms),0) AS duration_ms
               FROM playlists p LEFT JOIN playlist_tracks pt ON pt.playlist_id=p.id
               LEFT JOIN tracks t ON t.id=pt.track_id WHERE p.id=? GROUP BY p.id""", (pid,))

    async def playlist_tracks(self, pid: str, uid: int) -> list[dict]:
        return await self.all(
            """SELECT t.*, l.added_at, l.liked, l.play_count, l.last_played
               FROM playlist_tracks pt JOIN tracks t ON t.id=pt.track_id
               LEFT JOIN library l ON l.track_id=t.id AND l.user_id=?
               WHERE pt.playlist_id=? ORDER BY pt.pos""", (uid, pid))

    async def user_playlists(self, uid: int) -> list[dict]:
        return await self.all(
            """SELECT p.*, COUNT(pt.track_id) AS track_count,
                      COALESCE(SUM(t.duration_ms),0) AS duration_ms
               FROM playlists p LEFT JOIN playlist_tracks pt ON pt.playlist_id=p.id
               LEFT JOIN tracks t ON t.id=pt.track_id
               WHERE p.user_id=? GROUP BY p.id ORDER BY p.created_at DESC""", (uid,))

    async def set_watch(self, pid: str, watch: bool) -> None:
        await self.run("UPDATE playlists SET watch=? WHERE id=?", (int(watch), pid))

    async def watched_playlists(self) -> list[dict]:
        return await self.all("SELECT * FROM playlists WHERE watch=1")

    async def delete_playlist(self, pid: str) -> None:
        await self.run("DELETE FROM playlist_tracks WHERE playlist_id=?", (pid,))
        await self.run("DELETE FROM playlists WHERE id=?", (pid,))

    # ---------- jobs ----------
    async def create_job(self, uid: int, url: str, kind: str, title: str | None,
                         artwork: str | None, total: int) -> str:
        jid = new_id(10)
        await self.run(
            """INSERT INTO jobs(id, user_id, url, kind, title, artwork, total, status, created_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (jid, uid, url, kind, title, artwork, total, "running", now()))
        return jid

    async def update_job(self, jid: str, **fields: Any) -> None:
        sets = ", ".join(f"{k}=?" for k in fields)
        await self.run(f"UPDATE jobs SET {sets} WHERE id=?", (*fields.values(), jid))

    async def user_jobs(self, uid: int, limit: int = 15) -> list[dict]:
        return await self.all("SELECT * FROM jobs WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                              (uid, limit))

    # ---------- mixtapes ----------
    async def create_mixtape(self, uid: int, title: str, theme: str, note: str,
                             track_ids: list[int]) -> str:
        code = new_id(8)
        await self.run(
            "INSERT INTO mixtapes(code, owner_id, title, theme, note, created_at) VALUES(?,?,?,?,?,?)",
            (code, uid, title, theme, note, now()))
        await self.conn.executemany(
            "INSERT OR IGNORE INTO mixtape_tracks(code, track_id, pos) VALUES(?,?,?)",
            [(code, t, i) for i, t in enumerate(track_ids)])
        await self.conn.commit()
        return code

    async def get_mixtape(self, code: str) -> dict | None:
        return await self.one(
            """SELECT m.*, u.first_name AS owner_name, u.username AS owner_username,
                      COUNT(mt.track_id) AS track_count, COALESCE(SUM(t.duration_ms),0) AS duration_ms
               FROM mixtapes m LEFT JOIN users u ON u.id=m.owner_id
               LEFT JOIN mixtape_tracks mt ON mt.code=m.code LEFT JOIN tracks t ON t.id=mt.track_id
               WHERE m.code=? GROUP BY m.code""", (code,))

    async def mixtape_tracks(self, code: str) -> list[dict]:
        return await self.all(
            """SELECT t.* FROM mixtape_tracks mt JOIN tracks t ON t.id=mt.track_id
               WHERE mt.code=? ORDER BY mt.pos""", (code,))

    async def user_mixtapes(self, uid: int) -> list[dict]:
        return await self.all(
            """SELECT m.*, COUNT(mt.track_id) AS track_count FROM mixtapes m
               LEFT JOIN mixtape_tracks mt ON mt.code=m.code
               WHERE m.owner_id=? GROUP BY m.code ORDER BY m.created_at DESC""", (uid,))

    async def delete_mixtape(self, uid: int, code: str) -> None:
        await self.run("DELETE FROM mixtape_tracks WHERE code=? AND code IN "
                       "(SELECT code FROM mixtapes WHERE owner_id=?)", (code, uid))
        await self.run("DELETE FROM mixtapes WHERE code=? AND owner_id=?", (code, uid))

    async def inc_claims(self, code: str) -> None:
        await self.run("UPDATE mixtapes SET claims=claims+1 WHERE code=?", (code,))

    # ---------- leaderboard & statuses ----------
    @staticmethod
    def _since(period: str) -> str:
        today = datetime.now(timezone.utc).date()
        if period == "week":
            return (today - timedelta(days=6)).isoformat()
        if period == "month":
            return (today - timedelta(days=29)).isoformat()
        return "0000-00-00"

    async def leaderboard(self, period: str = "week", limit: int = 100) -> list[dict]:
        return await self.all(
            """SELECT u.id, u.first_name, u.username, u.photo_url, u.status, SUM(lt.seconds) AS seconds
               FROM listen_time lt JOIN users u ON u.id=lt.user_id
               WHERE lt.day >= ? AND COALESCE(u.hidden, 0)=0
               GROUP BY u.id HAVING seconds > 0 ORDER BY seconds DESC, u.id LIMIT ?""",
            (self._since(period), limit))

    async def my_rank(self, uid: int, period: str = "week") -> dict:
        since = self._since(period)
        me = await self.one("SELECT COALESCE(SUM(seconds),0) AS s FROM listen_time WHERE user_id=? AND day>=?",
                            (uid, since))
        above = await self.one(
            """SELECT COUNT(*) AS n FROM (SELECT lt.user_id, SUM(lt.seconds) AS s FROM listen_time lt
               JOIN users u ON u.id=lt.user_id WHERE lt.day>=? AND COALESCE(u.hidden,0)=0 AND lt.user_id<>?
               GROUP BY lt.user_id HAVING s > ?)""", (since, uid, me["s"]))
        return {"seconds": me["s"], "rank": above["n"] + 1 if me["s"] else None}

    async def set_status(self, uid: int, status: str | None) -> None:
        await self.run("UPDATE users SET status=? WHERE id=?", (status or None, uid))

    async def set_hidden(self, uid: int, hidden: bool) -> None:
        await self.run("UPDATE users SET hidden=? WHERE id=?", (int(hidden), uid))

    async def find_user(self, ref: str) -> dict | None:
        ref = ref.strip().lstrip("@")
        if ref.lstrip("-").isdigit():
            return await self.get_user(int(ref))
        return await self.one("SELECT * FROM users WHERE lower(username)=lower(?)", (ref,))

    async def users_with_status(self) -> list[dict]:
        return await self.all("SELECT id, first_name, username, status FROM users WHERE status IS NOT NULL "
                              "ORDER BY first_name")

    async def taste_match(self, a: int, b: int) -> int:
        """Share of common artists between two libraries, 0..100."""
        q = "SELECT DISTINCT lower(t.artist) AS a FROM library l JOIN tracks t ON t.id=l.track_id WHERE l.user_id=?"
        sa = {r["a"] for r in await self.all(q, (a,)) if r["a"]}
        sb = {r["a"] for r in await self.all(q, (b,)) if r["a"]}
        if not sa or not sb:
            return 0
        cosine = len(sa & sb) / (len(sa) * len(sb)) ** 0.5
        return min(100, round(100 * cosine ** 0.5))

    # ---------- duo playlists ----------
    async def create_duo(self, a: int, b: int, title: str) -> str:
        did = new_id(8)
        await self.run("INSERT INTO duos(id, title, a, b, state, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
                       (did, title, a, b, "pending", now(), now()))
        return did

    async def get_duo(self, did: str) -> dict | None:
        return await self.one(
            """SELECT d.*, ua.first_name AS a_name, ua.photo_url AS a_photo, ua.status AS a_status,
                      ub.first_name AS b_name, ub.photo_url AS b_photo, ub.status AS b_status,
                      (SELECT COUNT(*) FROM duo_tracks x WHERE x.duo_id=d.id) AS track_count,
                      (SELECT COALESCE(SUM(t.duration_ms),0) FROM duo_tracks x JOIN tracks t ON t.id=x.track_id
                       WHERE x.duo_id=d.id) AS duration_ms,
                      (SELECT t.artwork FROM duo_tracks x JOIN tracks t ON t.id=x.track_id
                       WHERE x.duo_id=d.id AND t.artwork IS NOT NULL ORDER BY x.added_at DESC LIMIT 1) AS artwork
               FROM duos d LEFT JOIN users ua ON ua.id=d.a LEFT JOIN users ub ON ub.id=d.b WHERE d.id=?""", (did,))

    async def user_duos(self, uid: int) -> list[dict]:
        ids = await self.all("SELECT id FROM duos WHERE a=? OR b=? ORDER BY updated_at DESC LIMIT 50", (uid, uid))
        return [d for d in [await self.get_duo(r["id"]) for r in ids] if d]

    async def pending_duo(self, a: int, b: int) -> dict | None:
        return await self.one("SELECT * FROM duos WHERE ((a=? AND b=?) OR (a=? AND b=?)) AND state='pending'",
                              (a, b, b, a))

    async def count_outgoing_pending(self, uid: int) -> int:
        return (await self.one("SELECT COUNT(*) AS n FROM duos WHERE a=? AND state='pending'", (uid,)))["n"]

    async def set_duo_state(self, did: str, state: str) -> None:
        await self.run("UPDATE duos SET state=?, updated_at=? WHERE id=?", (state, now(), did))

    async def delete_duo(self, did: str) -> None:
        await self.run("DELETE FROM duo_tracks WHERE duo_id=?", (did,))
        await self.run("DELETE FROM duos WHERE id=?", (did,))

    async def add_duo_tracks(self, did: str, uid: int, tids: list[int]) -> int:
        before = (await self.one("SELECT COUNT(*) AS n FROM duo_tracks WHERE duo_id=?", (did,)))["n"]
        ts = now()
        await self.conn.executemany(
            "INSERT OR IGNORE INTO duo_tracks(duo_id, track_id, added_by, added_at) VALUES(?,?,?,?)",
            [(did, t, uid, ts + i) for i, t in enumerate(tids)])
        await self.conn.execute("UPDATE duos SET updated_at=? WHERE id=?", (ts, did))
        await self.conn.commit()
        after = (await self.one("SELECT COUNT(*) AS n FROM duo_tracks WHERE duo_id=?", (did,)))["n"]
        return after - before

    async def remove_duo_track(self, did: str, tid: int) -> None:
        await self.run("DELETE FROM duo_tracks WHERE duo_id=? AND track_id=?", (did, tid))

    async def duo_tracks(self, did: str, uid: int) -> list[dict]:
        return await self.all(
            """SELECT t.*, x.added_by, x.added_at AS duo_added_at, l.liked, l.play_count
               FROM duo_tracks x JOIN tracks t ON t.id=x.track_id
               LEFT JOIN library l ON l.track_id=t.id AND l.user_id=?
               WHERE x.duo_id=? ORDER BY x.added_at DESC""", (uid, did))

    async def common_tracks(self, a: int, b: int, limit: int = 30) -> list[int]:
        rows = await self.all(
            """SELECT la.track_id FROM library la JOIN library lb ON lb.track_id=la.track_id AND lb.user_id=?
               JOIN tracks t ON t.id=la.track_id
               WHERE la.user_id=? AND t.status<>'unavailable' ORDER BY la.play_count+lb.play_count DESC LIMIT ?""",
            (b, a, limit))
        return [r["track_id"] for r in rows]

    async def top_tracks(self, uid: int, limit: int = 10) -> list[int]:
        rows = await self.all(
            """SELECT l.track_id FROM library l JOIN tracks t ON t.id=l.track_id
               WHERE l.user_id=? AND t.status<>'unavailable'
               ORDER BY l.play_count DESC, l.liked DESC, l.added_at DESC LIMIT ?""", (uid, limit))
        return [r["track_id"] for r in rows]

    # ---------- list import: match cache ----------
    async def cached_matches(self, keys: list[str], max_age: int = 14 * 86400) -> dict[str, int | None]:
        out: dict[str, int | None] = {}
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            # a miss is retried after 3 days — the track may have been uploaded since
            for r in await self.all(f"SELECT key, track_id FROM match_cache WHERE ts>? "
                                    f"AND (track_id IS NOT NULL OR ts>?) AND key IN "
                                    f"({','.join('?' * len(chunk))})", (now() - max_age, now() - 3 * 86400, *chunk)):
                out[r["key"]] = r["track_id"]
        return out

    async def save_match(self, key: str, tid: int | None, score: float) -> None:
        await self.run("INSERT OR REPLACE INTO match_cache(key, track_id, score, ts) VALUES(?,?,?,?)",
                       (key, tid, score, now()))

    # ---------- stats ----------
    async def stats(self, uid: int, tz_offset_min: int = 0) -> dict:
        lib = await self.one(
            """SELECT COUNT(*) AS tracks, COALESCE(SUM(t.duration_ms),0) AS duration_ms,
                      SUM(l.liked) AS liked, SUM(l.play_count) AS plays
               FROM library l JOIN tracks t ON t.id=l.track_id WHERE l.user_id=?""", (uid,))
        pls = await self.one("SELECT COUNT(*) AS n FROM playlists WHERE user_id=?", (uid,))
        user = await self.get_user(uid) or {}
        genres = await self.all(
            """SELECT t.genre, COUNT(*) AS n FROM library l JOIN tracks t ON t.id=l.track_id
               WHERE l.user_id=? AND t.genre IS NOT NULL AND t.genre<>''
               GROUP BY lower(t.genre) ORDER BY n DESC LIMIT 3""", (uid,))
        days_rows = await self.all(
            "SELECT DISTINCT date(ts + ?, 'unixepoch') AS d FROM listens WHERE user_id=? "
            "ORDER BY d DESC LIMIT 400", (tz_offset_min * 60, uid))
        days = {r["d"] for r in days_rows}
        today = (datetime.now(timezone.utc) + timedelta(minutes=tz_offset_min)).date()
        streak, cursor = 0, today
        if today.isoformat() not in days:
            cursor = today - timedelta(days=1)
        while cursor.isoformat() in days:
            streak += 1
            cursor -= timedelta(days=1)
        week = []
        for i in range(6, -1, -1):
            d: date = today - timedelta(days=i)
            week.append(d.isoformat() in days)
        lt = await self.one("SELECT COALESCE(SUM(seconds),0) AS s FROM listen_time WHERE user_id=?", (uid,))
        return {
            "listened_min": round((lt["s"] or 0) / 60),
            "tracks": lib["tracks"] or 0,
            "hours": round((lib["duration_ms"] or 0) / 3_600_000, 1),
            "liked": lib["liked"] or 0,
            "plays": lib["plays"] or 0,
            "playlists": pls["n"] or 0,
            "downloads": user.get("downloads", 0) or 0,
            "streak": streak,
            "week": week,
            "top_genres": [g["genre"] for g in genres],
        }
