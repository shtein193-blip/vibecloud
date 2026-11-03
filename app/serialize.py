from __future__ import annotations

from .security import Signer

# LITE_MODE: a track that already lives in Telegram (file_id) is "ready" for the user even when the
# server keeps no local copy — sending is instant and the player streams from SoundCloud's CDN.
LITE = {"on": False}


def configure(lite: bool) -> None:
    LITE["on"] = lite


def _status(row: dict) -> str:
    st = row.get("status") or "cloud"
    if LITE["on"] and row.get("tg_file_id") and st in ("cloud", "queued", "downloading", "ready"):
        return "ready"
    return st


def track_out(row: dict, uid: int, signer: Signer, base: str = "") -> dict:
    tid = row["id"]
    q = signer.sign(uid, tid)
    return {
        "id": tid,
        "title": row.get("title"),
        "artist": row.get("artist"),
        "artwork": row.get("artwork"),
        "duration": round((row.get("duration_ms") or 0) / 1000),
        "permalink": row.get("permalink"),
        "genre": row.get("genre"),
        "status": _status(row),
        "in_telegram": bool(row.get("tg_file_id")),
        "liked": bool(row.get("liked")),
        "play_count": row.get("play_count") or 0,
        "added_at": row.get("added_at"),
        "stream": f"{base}/api/stream/{tid}?{q}",
        "file": f"{base}/api/file/{tid}?{q}",
    }


def playlist_out(row: dict) -> dict:
    return {
        "id": row["id"],
        "kind": row.get("kind"),
        "title": row.get("title"),
        "artist": row.get("artist"),
        "artwork": row.get("artwork"),
        "permalink": row.get("permalink"),
        "watch": bool(row.get("watch")),
        "track_count": row.get("track_count") or 0,
        "duration": round((row.get("duration_ms") or 0) / 1000),
        "created_at": row.get("created_at"),
        "synced_at": row.get("synced_at"),
    }


def mixtape_out(row: dict, tracks: list[dict] | None = None) -> dict:
    return {
        "code": row["code"],
        "title": row.get("title"),
        "theme": row.get("theme") or "cobalt",
        "note": row.get("note") or "",
        "owner": row.get("owner_name"),
        "owner_id": row.get("owner_id"),
        "track_count": row.get("track_count") or (len(tracks) if tracks else 0),
        "duration": round((row.get("duration_ms") or 0) / 1000),
        "claims": row.get("claims") or 0,
        "created_at": row.get("created_at"),
        "covers": [t.get("artwork") for t in (tracks or []) if t.get("artwork")][:4],
        "tracks": [{"id": t["id"], "title": t["title"], "artist": t["artist"], "artwork": t["artwork"],
                    "duration": round((t.get("duration_ms") or 0) / 1000)} for t in (tracks or [])],
    }
