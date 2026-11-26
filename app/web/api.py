from __future__ import annotations

import gzip
import hashlib
import json
import re
import logging
import time
from pathlib import Path
from urllib.parse import quote

from aiohttp import web

import asyncio

from .. import social, yandex
from ..context import Ctx
from ..downloader import DownloadError
from ..importer import NeedChoice
from ..listimport import parse_tracklist
from ..serialize import configure as configure_serializer, mixtape_out, playlist_out, track_out
from ..soundcloud import SoundCloudError, extract_links, track_meta
from ..tasks import TTLCache, spawn

log = logging.getLogger(__name__)
WEBAPP_DIR = Path(__file__).resolve().parents[2] / "webapp"
CTX = web.AppKey("ctx", Ctx)
THEMES = {"cobalt", "red", "smoke", "lime", "paper"}
INIT_MAX_AGE = 48 * 3600  # initData older than this is rejected (re-open the Mini App)


# ---------------- auth ----------------
def _auth_user(request: web.Request) -> dict:
    ctx: Ctx = request.app[CTX]
    header = request.headers.get("Authorization", "")
    init_data = header[4:] if header.startswith("tma ") else request.query.get("auth", "")
    if init_data and ctx.settings.bot_token:
        from aiogram.utils.web_app import safe_parse_webapp_init_data
        try:
            data = safe_parse_webapp_init_data(ctx.settings.bot_token, init_data)
        except ValueError:
            raise web.HTTPUnauthorized(text="bad initData")
        if time.time() - data.auth_date.timestamp() > INIT_MAX_AGE:
            raise web.HTTPUnauthorized(text="Сессия устарела — закрой и снова открой приложение")
        u = data.user
        return {"id": u.id, "first_name": u.first_name, "username": u.username,
                "language_code": u.language_code, "photo_url": u.photo_url,
                "start_param": data.start_param}
    if ctx.settings.dev_user_id:
        return {"id": ctx.settings.dev_user_id, "first_name": "Dev", "username": "dev"}
    raise web.HTTPUnauthorized(text="open from Telegram")


PUBLIC_API = ("/api/stream/", "/api/file/", "/api/health", "/api/events")


@web.middleware
async def auth_mw(request: web.Request, handler):
    path = request.path
    ctx: Ctx = request.app[CTX]
    if path.startswith("/api/") and not path.startswith("/api/health") and not ctx.ready:
        return web.json_response({"error": "Сервер просыпается — ещё пару секунд…", "retry": True}, status=503,
                                 headers={"Retry-After": "3"})
    if path.startswith("/api/") and not path.startswith(PUBLIC_API):
        try:
            user = _auth_user(request)
        except web.HTTPUnauthorized as ex:
            return web.json_response({"error": ex.text or "Открой приложение из Telegram"}, status=401)
        request["user"] = user
        await ctx.db.upsert_user(user)
    try:
        return await handler(request)
    except (SoundCloudError, DownloadError, ValueError) as ex:
        return web.json_response({"error": str(ex)}, status=400)


def uid_of(request: web.Request) -> int:
    return request["user"]["id"]


def ok(data=None, **kw):
    return web.json_response(data if data is not None else {"ok": True, **kw})


# ---------------- bootstrap ----------------
async def bootstrap(request: web.Request):
    ctx: Ctx = request.app[CTX]
    uid = uid_of(request)
    tz = int(request.query.get("tz", "0") or 0)
    tracks = [track_out(r, uid, ctx.signer) for r in await ctx.db.library(uid)]
    playlists = [playlist_out(p) for p in await ctx.db.user_playlists(uid)]
    mixtapes = [mixtape_out(m, await ctx.db.mixtape_tracks(m["code"])) for m in await ctx.db.user_mixtapes(uid)]
    jobs = await ctx.db.user_jobs(uid)
    me = await ctx.db.get_user(uid) or {}
    duos = [social.duo_out(d, uid) for d in await ctx.db.user_duos(uid)]
    return ok({
        "user": {**request["user"], "status": me.get("status"), "hidden": bool(me.get("hidden")),
                 "admin": ctx.is_admin(uid)},
        "stats": {**await ctx.db.stats(uid, tz), "rank": (await ctx.db.my_rank(uid, "week"))["rank"]},
        "tracks": tracks, "playlists": playlists, "mixtapes": mixtapes, "duos": duos,
        "events_token": ctx.signer.event_token(uid),
        "jobs": [{k: j[k] for k in ("id", "kind", "title", "artwork", "total", "added", "status", "created_at",
                                     "playlist_id")} for j in jobs],
        "config": {"bot": ctx.bot_username, "app_link": ctx.app_link(""),
                   "storage": bool(ctx.settings.storage_chat_id),
                   # lite: play straight from SoundCloud's CDN, "download" = MP3 into the chat from Telegram
                   "lite": ctx.settings.lite_mode, "direct": ctx.settings.lite_mode,
                   "ai": ctx.settings.ai_enabled, "list_max": ctx.settings.list_import_max},
    })


# ---------------- events (SSE) ----------------
async def events(request: web.Request):
    ctx: Ctx = request.app[CTX]
    uid = ctx.signer.verify_event(request.query.get("t", ""))
    if uid is None:  # old clients still send initData
        try:
            uid = _auth_user(request)["id"]
        except web.HTTPUnauthorized:
            raise web.HTTPUnauthorized(text="bad token")
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache",
                                       "X-Accel-Buffering": "no"})
    await resp.prepare(request)
    q = ctx.bus.subscribe(uid)
    try:
        await resp.write(b"event: hello\ndata: {}\n\n")
        while True:
            try:
                name, data = await asyncio.wait_for(q.get(), timeout=15)
                await resp.write(f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode())
            except asyncio.TimeoutError:
                await resp.write(b": ping\n\n")
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        ctx.bus.unsubscribe(uid, q)
    return resp


# ---------------- import ----------------
async def _body(request: web.Request) -> dict:
    try:
        return await request.json()
    except Exception:  # noqa: BLE001
        return {}


async def inspect(request: web.Request):
    ctx: Ctx = request.app[CTX]
    body = await _body(request)
    links = extract_links(body.get("url", ""))
    if not links:
        return web.json_response({"error": "Это не похоже на ссылку SoundCloud"}, status=400)
    return ok(await ctx.importer.inspect(links[0]))


async def start_import(request: web.Request):
    ctx: Ctx = request.app[CTX]
    uid = uid_of(request)
    body = await _body(request)
    links = extract_links(body.get("url", ""))
    if not links:
        return web.json_response({"error": "Это не похоже на ссылку SoundCloud"}, status=400)

    async def runner(url: str):
        try:
            await ctx.importer.run(uid, url, mode=body.get("mode"))
        except NeedChoice as nc:
            ctx.bus.publish(uid, "need_choice", nc.preview)
        except Exception as ex:  # noqa: BLE001
            log.info("import %s failed: %s", url, ex)
            ctx.bus.publish(uid, "import_error", {"url": url, "error": str(ex)})

    for url in links[:5]:
        spawn(runner(url), "import")
    return ok(started=len(links[:5]))


# ---------------- tracks ----------------
async def _owned_track(request: web.Request) -> tuple[Ctx, int, dict]:
    ctx: Ctx = request.app[CTX]
    uid = uid_of(request)
    tid = int(request.match_info["tid"])
    row = await ctx.db.library_entry(uid, tid) or await ctx.db.get_track(tid)
    if not row:
        raise web.HTTPNotFound(text="track not found")
    return ctx, uid, row


async def track_like(request: web.Request):
    ctx, uid, row = await _owned_track(request)
    liked = bool((await _body(request)).get("liked", True))
    await ctx.db.add_to_library(uid, row["id"])
    await ctx.db.set_like(uid, row["id"], liked)
    return ok(liked=liked)


async def track_played(request: web.Request):
    ctx, uid, row = await _owned_track(request)
    await ctx.db.add_listen(uid, row["id"])
    return ok()


async def track_add(request: web.Request):
    ctx, uid, row = await _owned_track(request)
    await ctx.db.add_to_library(uid, row["id"])
    entry = await ctx.db.library_entry(uid, row["id"])
    out = track_out(entry, uid, ctx.signer)
    ctx.bus.publish(uid, "track_added", {"job": None, "playlist": None, "track": out})
    return ok(out)


async def track_delete(request: web.Request):
    ctx, uid, row = await _owned_track(request)
    await ctx.db.remove_from_library(uid, row["id"])
    return ok()


async def track_prefetch(request: web.Request):
    ctx, uid, row = await _owned_track(request)
    if row["status"] not in ("ready", "unavailable"):
        ctx.dl.prefetch(row["id"], priority=1)
    return ok()


async def track_send(request: web.Request):
    ctx, uid, row = await _owned_track(request)
    if not ctx.tg:
        return web.json_response({"error": "Бот не запущен"}, status=503)

    async def go():
        try:
            await ctx.tg.send_track(uid, row["id"], uid)
        except Exception as ex:  # noqa: BLE001
            ctx.bus.publish(uid, "toast", {"text": f"Не вышло отправить: {ex}"})
    spawn(go(), "send")
    return ok()


async def track_share(request: web.Request):
    """Returns a prepared inline message id for Telegram.WebApp.shareMessage()."""
    ctx, uid, row = await _owned_track(request)
    result = {"inline_query": f"tr_{row['id']}", "link": ctx.app_link(f"tr_{row['id']}")}
    if not ctx.tg:
        return ok(result)
    try:
        from aiogram.types import InlineQueryResultCachedAudio
        from ..bot import keyboards as kb
        from ..bot.texts import track_caption
        file_id = await ctx.tg.ensure_file_id(row["id"], uid)
        row = await ctx.db.get_track(row["id"])
        prepared = await ctx.bot.save_prepared_inline_message(
            user_id=uid,
            result=InlineQueryResultCachedAudio(id=f"s{row['id']}", audio_file_id=file_id,
                                                caption=track_caption(row, ctx.bot_username),
                                                reply_markup=kb.shared_track(ctx, row)),
            allow_user_chats=True, allow_group_chats=True, allow_channel_chats=True)
        result["prepared_id"] = prepared.id
    except Exception as ex:  # noqa: BLE001
        log.info("prepare share failed: %s", ex)
    return ok(result)


async def track_direct(request: web.Request):
    """Signed SoundCloud CDN url for the player — audio bytes never touch our server."""
    ctx: Ctx = request.app[CTX]
    row = await ctx.db.get_track(int(request.match_info["tid"]))
    if not row:
        raise web.HTTPNotFound(text="track not found")
    if row["status"] == "unavailable" or not ctx.settings.lite_mode:
        return ok({"url": None})
    try:
        info = await ctx.sc.stream_url(row["id"])
    except SoundCloudError as ex:
        log.info("direct stream %s: %s", row["id"], ex)
        info = None
    return ok(info or {"url": None})


async def track_waveform(request: web.Request):
    ctx, uid, row = await _owned_track(request)
    cache: TTLCache = request.app["wave_cache"]
    bars = max(16, min(int(request.query.get("bars", 96) or 96), 200))
    key = (row["id"], bars)
    packed = cache.get(key)
    if packed is None:  # store as bytes (1 byte per bar) — 25× smaller than a list of floats
        packed = bytes(round(v * 255) for v in await ctx.sc.waveform(row.get("waveform_url"), bars))
        cache[key] = packed
    return ok({"bars": [round(b / 255, 3) for b in packed]})


async def _signed_path(request: web.Request) -> tuple[Path, dict]:
    ctx: Ctx = request.app[CTX]
    tid = int(request.match_info["tid"])
    try:
        uid, exp, sig = int(request.query["u"]), int(request.query["e"]), request.query["s"]
    except (KeyError, ValueError):
        raise web.HTTPForbidden()
    if not ctx.signer.verify(uid, tid, exp, sig):
        raise web.HTTPForbidden()
    try:
        path = await ctx.dl.ensure(tid)
    except DownloadError as ex:
        raise web.HTTPNotFound(text=str(ex))
    return path, await ctx.db.get_track(tid)


async def stream(request: web.Request):
    path, _ = await _signed_path(request)
    return web.FileResponse(path, headers={"Content-Type": "audio/mpeg", "Cache-Control": "private, max-age=86400",
                                           "Accept-Ranges": "bytes"})


async def file_download(request: web.Request):
    from ..bot.sender import safe_name
    path, row = await _signed_path(request)
    name = safe_name(row)
    return web.FileResponse(path, headers={
        "Content-Type": "audio/mpeg",
        "Content-Disposition": f"attachment; filename=\"track.mp3\"; filename*=UTF-8''{quote(name)}"})


# ---------------- playlists ----------------
async def _owned_playlist(request: web.Request) -> tuple[Ctx, int, dict]:
    ctx: Ctx = request.app[CTX]
    uid = uid_of(request)
    pl = await ctx.db.get_playlist(request.match_info["pid"])
    if not pl or pl["user_id"] != uid:
        raise web.HTTPNotFound(text="playlist not found")
    return ctx, uid, pl


async def playlist_get(request: web.Request):
    ctx, uid, pl = await _owned_playlist(request)
    rows = await ctx.db.playlist_tracks(pl["id"], uid)
    return ok({**playlist_out(pl), "tracks": [track_out(r, uid, ctx.signer) for r in rows]})


async def playlist_watch(request: web.Request):
    ctx, uid, pl = await _owned_playlist(request)
    watch = bool((await _body(request)).get("watch", True))
    await ctx.db.set_watch(pl["id"], watch)
    return ok(watch=watch)


async def playlist_send(request: web.Request):
    ctx, uid, pl = await _owned_playlist(request)
    if not ctx.tg:
        return web.json_response({"error": "Бот не запущен"}, status=503)
    rows = await ctx.db.playlist_tracks(pl["id"], uid)
    tids = [r["id"] for r in rows if r["status"] != "unavailable"]

    async def go():
        from ..bot.texts import e
        status = await ctx.bot.send_message(uid, f"📤 Готовлю <b>{e(pl['title'])}</b>: 0/{len(tids)}")
        sent, failed = await ctx.tg.send_many(uid, tids, uid, status=status)
        await status.edit_text(f"✓ Отправил {sent} из {len(tids)}" + (f" · не вышло: {failed}" if failed else ""))
    spawn(go(), "send-playlist")
    return ok(count=len(tids))


async def playlist_sync(request: web.Request):
    ctx, uid, pl = await _owned_playlist(request)
    if pl["kind"] == "mixtape":
        return ok(new=0)
    fresh = await ctx.importer.sync_playlist(pl)
    return ok(new=len(fresh))


async def playlist_delete(request: web.Request):
    ctx, uid, pl = await _owned_playlist(request)
    await ctx.db.delete_playlist(pl["id"])
    return ok()


# ---------------- search ----------------
async def search(request: web.Request):
    ctx: Ctx = request.app[CTX]
    uid = uid_of(request)
    q = request.query.get("q", "").strip()
    if len(q) < 2:
        return ok({"results": []})
    raw = await ctx.sc.search(q, 20)
    metas = [m for m in (track_meta(t) for t in raw) if m["available"]]
    await ctx.db.upsert_tracks(metas)
    rows = await ctx.db.library_entries(uid, [m["id"] for m in metas])
    out = [{**track_out(rows[m["id"]], uid, ctx.signer), "in_library": rows[m["id"]].get("added_at") is not None}
           for m in metas if m["id"] in rows]
    return ok({"results": out})


# ---------------- mixtapes ----------------
async def mixtapes_create(request: web.Request):
    ctx: Ctx = request.app[CTX]
    uid = uid_of(request)
    body = await _body(request)
    title = (body.get("title") or "").strip()[:60] or "Микстейп"
    theme = body.get("theme") if body.get("theme") in THEMES else "cobalt"
    tids = [int(t) for t in body.get("track_ids", [])][:100]
    if not tids:
        return web.json_response({"error": "Добавь хотя бы один трек"}, status=400)
    code = await ctx.db.create_mixtape(uid, title, theme, (body.get("note") or "")[:140], tids)
    mx = await ctx.db.get_mixtape(code)
    return ok({**mixtape_out(mx, await ctx.db.mixtape_tracks(code)), "link": ctx.app_link(f"mx_{code}")})


async def mixtape_get(request: web.Request):
    ctx: Ctx = request.app[CTX]
    uid = uid_of(request)
    code = request.match_info["code"]
    mx = await ctx.db.get_mixtape(code)
    if not mx:
        raise web.HTTPNotFound(text="mixtape not found")
    rows = await ctx.db.mixtape_tracks(code)
    data = mixtape_out(mx, rows)
    data["tracks"] = [track_out(r, uid, ctx.signer) for r in rows]
    data["link"] = ctx.app_link(f"mx_{code}")
    data["mine"] = mx["owner_id"] == uid
    return ok(data)


async def claim_mixtape(ctx: Ctx, uid: int, code: str) -> tuple[str, int]:
    mx = await ctx.db.get_mixtape(code)
    if not mx:
        raise LookupError(code)
    rows = await ctx.db.mixtape_tracks(code)
    pid = await ctx.db.upsert_playlist(uid, {
        "kind": "mixtape", "title": mx["title"], "artist": f"от {mx['owner_name'] or 'друга'}",
        "artwork": next((r["artwork"] for r in rows if r.get("artwork")), None),
        "permalink": ctx.app_link(f"mx_{code}"), "source_url": f"mixtape:{code}"})
    budget = ctx.settings.prefetch_per_import
    for r in rows:
        await ctx.db.add_to_library(uid, r["id"])
        entry = await ctx.db.library_entry(uid, r["id"])
        if budget > 0 and entry["status"] not in ("ready", "unavailable") and not entry["tg_file_id"]:
            budget -= 1
            ctx.dl.prefetch(r["id"])
        ctx.bus.publish(uid, "track_added", {"job": None, "playlist": pid,
                                             "track": track_out(entry, uid, ctx.signer)})
    await ctx.db.set_playlist_tracks(pid, [r["id"] for r in rows])
    if mx["owner_id"] != uid:
        await ctx.db.inc_claims(code)
    pl = await ctx.db.get_playlist(pid)
    ctx.bus.publish(uid, "playlist", playlist_out(pl))
    return pid, len(rows)


async def mixtape_claim(request: web.Request):
    ctx: Ctx = request.app[CTX]
    try:
        pid, n = await claim_mixtape(ctx, uid_of(request), request.match_info["code"])
    except LookupError:
        raise web.HTTPNotFound(text="mixtape not found")
    return ok(playlist_id=pid, count=n)


async def mixtape_share(request: web.Request):
    ctx: Ctx = request.app[CTX]
    uid = uid_of(request)
    code = request.match_info["code"]
    mx = await ctx.db.get_mixtape(code)
    if not mx:
        raise web.HTTPNotFound()
    link = ctx.app_link(f"mx_{code}")
    result = {"link": link, "text": f"🎁 Микстейп «{mx['title']}» — {mx['track_count']} треков. Забери себе:"}
    if ctx.bot:
        try:
            from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle,
                                       InputTextMessageContent)
            from ..bot.texts import e, tracks_word
            rows = await ctx.db.mixtape_tracks(code)
            cover = next((r["artwork"] for r in rows if r.get("artwork")), None)
            listing = "\n".join(f"{i + 1}. {e(r['artist'])} — {e(r['title'])}" for i, r in enumerate(rows[:8]))
            more = f"\n…и ещё {len(rows) - 8}" if len(rows) > 8 else ""
            note = f"\n\n<i>«{e(mx['note'])}»</i>" if mx.get("note") else ""
            prepared = await ctx.bot.save_prepared_inline_message(
                user_id=uid,
                result=InlineQueryResultArticle(
                    id=f"m{code}", title=f"Микстейп «{mx['title']}»", description=tracks_word(len(rows)),
                    thumbnail_url=(cover or "").replace("-t500x500.", "-t300x300.") or None,
                    input_message_content=InputTextMessageContent(
                        message_text=f"🎁 <b>{e(mx['title'])}</b> · {tracks_word(len(rows))}{note}\n\n{listing}{more}"),
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text="🎧 Открыть и забрать", url=link)]])),
                allow_user_chats=True, allow_group_chats=True, allow_channel_chats=True)
            result["prepared_id"] = prepared.id
        except Exception as ex:  # noqa: BLE001
            log.info("prepare mixtape share failed: %s", ex)
    return ok(result)


async def mixtape_delete(request: web.Request):
    ctx: Ctx = request.app[CTX]
    await ctx.db.delete_mixtape(uid_of(request), request.match_info["code"])
    return ok()


# ---------------- listening time (leaderboard) ----------------
async def listen(request: web.Request):
    """Heartbeat from the player: real seconds listened since the previous beat (server caps cheating)."""
    ctx: Ctx = request.app[CTX]
    uid = uid_of(request)
    body = await _body(request)
    try:
        sec = int(float(body.get("seconds") or 0))
    except (TypeError, ValueError):
        sec = 0
    last: TTLCache = request.app["listen_last"]
    prev = last.get(uid)
    nowm = time.monotonic()
    cap = 75 if prev is None else min(nowm - prev + 10, 330)
    sec = max(0, min(sec, int(cap)))
    last[uid] = nowm
    if sec:
        await ctx.db.add_listen_time(uid, sec)
    return ok(counted=sec)


# ---------------- list import (Yandex Music & co) ----------------
async def import_list(request: web.Request):
    ctx: Ctx = request.app[CTX]
    uid = uid_of(request)
    body = await _body(request)
    if uid in ctx.lists.running:
        return web.json_response({"error": "Предыдущий список ещё переносится", "job_id": ctx.lists.running[uid]},
                                 status=409)
    text = str(body.get("text") or "")
    link = yandex.find_link(text)
    if link and len(parse_tracklist(text.replace(link, ""))) < 2:  # a Yandex Music link instead of a list
        try:
            pl = await yandex.fetch(link, ctx.settings.list_import_max)
        except yandex.YandexError as ex:
            return web.json_response({"error": str(ex)}, status=400)
        job_id, count = await ctx.lists.start(uid, "", body.get("title") or pl["title"], items=pl["items"])
        return ok(job_id=job_id, count=count, title=pl["title"])
    job_id, count = await ctx.lists.start(uid, text, body.get("title"))
    return ok(job_id=job_id, count=count)


async def list_preview(request: web.Request):
    body = await _body(request)
    text = str(body.get("text") or "")
    link = yandex.find_link(text)
    if link and len(parse_tracklist(text.replace(link, ""))) < 2:
        try:
            pl = await yandex.fetch(link)
        except yandex.YandexError as ex:
            return ok(count=0, sample=[], error=str(ex), link=True)
        return ok(count=len(pl["items"]), sample=pl["items"][:5], title=pl["title"], link=True)
    items = parse_tracklist(text)
    return ok(count=len(items), sample=items[:5])


async def job_get(request: web.Request):
    ctx: Ctx = request.app[CTX]
    j = await ctx.db.one("SELECT * FROM jobs WHERE id=? AND user_id=?", (request.match_info["jid"], uid_of(request)))
    if not j:
        raise web.HTTPNotFound(text="job not found")
    return ok({k: j[k] for k in ("id", "kind", "title", "total", "added", "found", "status", "error", "playlist_id",
                                 "created_at")}
              | {"missing": json.loads(j["report"] or "[]")})


# ---------------- Ewa ----------------
async def ewa(request: web.Request):
    ctx: Ctx = request.app[CTX]
    body = await _body(request)
    hist = body.get("history") if isinstance(body.get("history"), list) else []
    return ok(await ctx.ewa.reply(uid_of(request), str(body.get("text") or ""), hist))


# ---------------- leaderboard, statuses, profiles ----------------
async def leaders(request: web.Request):
    ctx: Ctx = request.app[CTX]
    return ok(await social.leaderboard(ctx, uid_of(request), request.query.get("period", "week")))


async def me_hidden(request: web.Request):
    ctx: Ctx = request.app[CTX]
    hidden = bool((await _body(request)).get("hidden"))
    await ctx.db.set_hidden(uid_of(request), hidden)
    return ok(hidden=hidden)


async def admin_status(request: web.Request):
    ctx: Ctx = request.app[CTX]
    if not ctx.is_admin(uid_of(request)):
        raise web.HTTPForbidden(text="только для администратора")
    body = await _body(request)
    user = await social.set_status(ctx, int(body.get("user_id") or 0), body.get("status"), by=uid_of(request))
    if not user:
        raise web.HTTPNotFound(text="user not found")
    return ok(status=user["status"])


async def user_profile(request: web.Request):
    ctx: Ctx = request.app[CTX]
    uid = uid_of(request)
    tid = int(request.match_info["uid"])
    u = await ctx.db.get_user(tid)
    if not u or (u.get("hidden") and tid != uid and not ctx.is_admin(uid)):
        raise web.HTTPNotFound(text="user not found")
    week, total = await ctx.db.my_rank(tid, "week"), await ctx.db.my_rank(tid, "all")
    st = await ctx.db.stats(tid)
    duo = next((social.duo_out(d, uid) for d in await ctx.db.user_duos(uid)
                if tid in (d["a"], d["b"])), None)
    return ok({**social.user_out(u), "week_minutes": round(week["seconds"] / 60),
               "total_minutes": round(total["seconds"] / 60), "rank": week["rank"], "tracks": st["tracks"],
               "top_genres": st["top_genres"], "match": await ctx.db.taste_match(uid, tid) if tid != uid else None,
               "duo": duo, "me": tid == uid})


# ---------------- duo playlists ----------------
async def _duo(request: web.Request) -> tuple[Ctx, int, dict]:
    ctx: Ctx = request.app[CTX]
    uid = uid_of(request)
    d = await social.member_duo(ctx, uid, request.match_info["did"])
    if not d:
        raise web.HTTPNotFound(text="Парный плейлист не найден")
    return ctx, uid, d


async def duos_list(request: web.Request):
    ctx: Ctx = request.app[CTX]
    uid = uid_of(request)
    return ok({"duos": [social.duo_out(d, uid) for d in await ctx.db.user_duos(uid)]})


async def duos_create(request: web.Request):
    ctx: Ctx = request.app[CTX]
    body = await _body(request)
    return ok(await social.invite(ctx, uid_of(request), int(body.get("user_id") or 0), str(body.get("title") or "")))


async def duo_get(request: web.Request):
    ctx, uid, d = await _duo(request)
    return ok(await social.duo_detail(ctx, uid, d))


async def duo_respond(request: web.Request):
    ctx: Ctx = request.app[CTX]
    accept = request.match_info["action"] == "accept"
    res = await social.respond(ctx, uid_of(request), request.match_info["did"], accept)
    if not res:
        raise web.HTTPNotFound(text="Приглашение уже неактуально")
    return ok(res)


async def duo_add(request: web.Request):
    ctx, uid, d = await _duo(request)
    if d["state"] != "active":
        return web.json_response({"error": "Сначала друг должен принять приглашение"}, status=400)
    ids = [int(x) for x in (await _body(request)).get("track_ids", []) if str(x).isdigit()]
    return ok(added=await social.add_tracks(ctx, uid, d, ids))


async def duo_fill(request: web.Request):
    ctx, uid, d = await _duo(request)
    if d["state"] != "active":
        return web.json_response({"error": "Сначала друг должен принять приглашение"}, status=400)
    return ok(added=await social.fill_common(ctx, uid, d))


async def duo_remove_track(request: web.Request):
    ctx, uid, d = await _duo(request)
    await ctx.db.remove_duo_track(d["id"], int(request.match_info["tid"]))
    return ok()


async def duo_delete(request: web.Request):
    ctx, uid, d = await _duo(request)
    await ctx.db.delete_duo(d["id"])
    other = d["b"] if d["a"] == uid else d["a"]
    ctx.bus.publish(other, "duo_gone", {"id": d["id"]})
    return ok()


# ---------------- static ----------------
_INDEX: dict[str, str] = {}


def _index_html() -> str:
    """index.html with ?v=<content hash> on every css/js link: after a deploy Telegram's webview loads the new
    code at once instead of a cached old app.js (static files are cached for an hour)."""
    if "html" not in _INDEX:
        h = hashlib.sha1()
        for f in sorted(WEBAPP_DIR.rglob("*")):
            if f.suffix in (".js", ".css") and f.is_file():
                h.update(f.read_bytes())
        v = h.hexdigest()[:10]
        html = (WEBAPP_DIR / "index.html").read_text("utf-8")
        _INDEX["html"] = re.sub(r'(href|src)="((?:css|js)/[^"?]+\.(?:css|js))"', rf'\1="\2?v={v}"', html)
    return _INDEX["html"]


async def index(request: web.Request):
    return web.Response(text=_index_html(), content_type="text/html",
                        headers={"Cache-Control": "no-cache, must-revalidate"})


async def health(request: web.Request):
    ctx: Ctx = request.app[CTX]
    return web.json_response({"ok": True, "status": "ok", "instance": request.app["instance"], "ready": ctx.ready},
                             headers={"Cache-Control": "no-store"})


@web.middleware
async def cache_mw(request: web.Request, handler):
    """Let Telegram's webview keep static assets: fonts/images for a month, code for an hour (+ETag).
    JSON answers are gzipped: a 500-track library shrinks ~8× on mobile networks."""
    resp = await handler(request)
    if (type(resp) is web.Response and resp.body is not None and len(resp.body) > 1400
            and "gzip" in request.headers.get("Accept-Encoding", "")
            and (resp.content_type or "").startswith(("application/json", "text/"))):
        resp.enable_compression(web.ContentCoding.gzip)
    path = request.path
    if path.startswith(("/fonts/", "/img/", "/js/vendor/")):
        resp.headers.setdefault("Cache-Control", "public, max-age=2592000, immutable")
    elif path.startswith(("/css/", "/js/")):
        resp.headers.setdefault("Cache-Control", "public, max-age=3600")
    return resp


def _precompress_static() -> None:
    """aiohttp serves `file.js.gz` instead of `file.js` to clients that accept gzip — do it once at start."""
    for ext in ("*.js", "*.css", "*.html", "*.svg"):
        for f in WEBAPP_DIR.rglob(ext):
            gz = f.with_name(f.name + ".gz")
            try:
                if not gz.exists() or gz.stat().st_mtime < f.stat().st_mtime:
                    gz.write_bytes(gzip.compress(f.read_bytes(), 9, mtime=0))
            except OSError:
                return  # read-only filesystem — just serve uncompressed


def create_web_app(ctx: Ctx, instance: str = "dev") -> web.Application:
    configure_serializer(ctx.settings.lite_mode)
    _precompress_static()
    app = web.Application(middlewares=[cache_mw, auth_mw], client_max_size=4 * 1024 * 1024)
    app[CTX] = ctx
    app["instance"] = instance
    app["wave_cache"] = TTLCache(maxsize=3000, ttl=7 * 86400)
    app["listen_last"] = TTLCache(maxsize=20000, ttl=3600)
    r = app.router
    r.add_get("/", index)
    r.add_get("/api/health", health)
    r.add_get("/api/bootstrap", bootstrap)
    r.add_get("/api/events", events)
    r.add_post("/api/import/inspect", inspect)
    r.add_post("/api/import", start_import)
    r.add_get("/api/search", search)
    r.add_post("/api/track/{tid}/like", track_like)
    r.add_post("/api/track/{tid}/played", track_played)
    r.add_post("/api/track/{tid}/add", track_add)
    r.add_post("/api/track/{tid}/prefetch", track_prefetch)
    r.add_post("/api/track/{tid}/send", track_send)
    r.add_post("/api/track/{tid}/share", track_share)
    r.add_get("/api/track/{tid}/waveform", track_waveform)
    r.add_get("/api/track/{tid}/direct", track_direct)
    r.add_delete("/api/track/{tid}", track_delete)
    r.add_get("/api/stream/{tid}", stream)
    r.add_get("/api/file/{tid}", file_download)
    r.add_get("/api/playlist/{pid}", playlist_get)
    r.add_post("/api/playlist/{pid}/watch", playlist_watch)
    r.add_post("/api/playlist/{pid}/send", playlist_send)
    r.add_post("/api/playlist/{pid}/sync", playlist_sync)
    r.add_delete("/api/playlist/{pid}", playlist_delete)
    r.add_post("/api/mixtapes", mixtapes_create)
    r.add_get("/api/mixtape/{code}", mixtape_get)
    r.add_post("/api/mixtape/{code}/claim", mixtape_claim)
    r.add_post("/api/mixtape/{code}/share", mixtape_share)
    r.add_delete("/api/mixtape/{code}", mixtape_delete)
    r.add_post("/api/listen", listen)
    r.add_post("/api/import/list", import_list)
    r.add_post("/api/import/list/preview", list_preview)
    r.add_get("/api/job/{jid}", job_get)
    r.add_post("/api/ewa", ewa)
    r.add_get("/api/leaders", leaders)
    r.add_post("/api/me/hidden", me_hidden)
    r.add_post("/api/admin/status", admin_status)
    r.add_get("/api/user/{uid}", user_profile)
    r.add_get("/api/duos", duos_list)
    r.add_post("/api/duos", duos_create)
    r.add_get("/api/duo/{did}", duo_get)
    r.add_post("/api/duo/{did}/add", duo_add)
    r.add_post("/api/duo/{did}/fill", duo_fill)
    r.add_post("/api/duo/{did}/{action:accept|decline}", duo_respond)
    r.add_delete("/api/duo/{did}/track/{tid}", duo_remove_track)
    r.add_delete("/api/duo/{did}", duo_delete)
    for sub in ("css", "js", "fonts", "img"):
        r.add_static(f"/{sub}/", WEBAPP_DIR / sub, append_version=False)
    return app
