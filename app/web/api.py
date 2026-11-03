from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from urllib.parse import quote

from aiohttp import web

from ..context import Ctx
from ..downloader import DownloadError
from ..importer import NeedChoice
from ..serialize import configure as configure_serializer, mixtape_out, playlist_out, track_out
from ..soundcloud import SoundCloudError, extract_links, track_meta

log = logging.getLogger(__name__)
WEBAPP_DIR = Path(__file__).resolve().parents[2] / "webapp"
CTX = web.AppKey("ctx", Ctx)
THEMES = {"cobalt", "red", "smoke", "lime", "paper"}


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
        u = data.user
        return {"id": u.id, "first_name": u.first_name, "username": u.username,
                "language_code": u.language_code, "photo_url": u.photo_url,
                "start_param": data.start_param}
    if ctx.settings.dev_user_id:
        return {"id": ctx.settings.dev_user_id, "first_name": "Dev", "username": "dev"}
    raise web.HTTPUnauthorized(text="open from Telegram")


@web.middleware
async def auth_mw(request: web.Request, handler):
    path = request.path
    if path.startswith("/api/") and not path.startswith(("/api/stream/", "/api/file/", "/api/health")):
        user = _auth_user(request)
        request["user"] = user
        await request.app[CTX].db.upsert_user(user)
    try:
        return await handler(request)
    except (SoundCloudError, DownloadError) as ex:
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
    return ok({
        "user": request["user"],
        "stats": await ctx.db.stats(uid, tz),
        "tracks": tracks, "playlists": playlists, "mixtapes": mixtapes,
        "jobs": [{k: j[k] for k in ("id", "kind", "title", "artwork", "total", "added", "status", "created_at",
                                     "playlist_id")} for j in jobs],
        "config": {"bot": ctx.bot_username, "app_link": ctx.app_link(""),
                   "storage": bool(ctx.settings.storage_chat_id),
                   # lite: play straight from SoundCloud's CDN, "download" = MP3 into the chat from Telegram
                   "lite": ctx.settings.lite_mode, "direct": ctx.settings.lite_mode},
    })


# ---------------- events (SSE) ----------------
async def events(request: web.Request):
    ctx: Ctx = request.app[CTX]
    uid = uid_of(request)
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
        asyncio.create_task(runner(url))
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
    asyncio.create_task(go())
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
    cache = request.app["wave_cache"]
    if row["id"] not in cache:
        cache[row["id"]] = await ctx.sc.waveform(row.get("waveform_url"), int(request.query.get("bars", 96)))
        if len(cache) > 2000:
            cache.pop(next(iter(cache)))
    return ok({"bars": cache[row["id"]]})


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
    asyncio.create_task(go())
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
    out = []
    for t in raw:
        m = track_meta(t)
        if not m["available"]:
            continue
        await ctx.db.upsert_track(m)
        entry = await ctx.db.library_entry(uid, m["id"])
        row = entry or await ctx.db.get_track(m["id"])
        out.append({**track_out(row, uid, ctx.signer), "in_library": bool(entry)})
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


# ---------------- static ----------------
async def index(request: web.Request):
    return web.FileResponse(WEBAPP_DIR / "index.html", headers={"Cache-Control": "no-cache"})


async def health(request: web.Request):
    return ok(status="ok")


@web.middleware
async def cache_mw(request: web.Request, handler):
    """Let Telegram's webview keep static assets: fonts/images for a month, code for an hour (+ETag)."""
    resp = await handler(request)
    path = request.path
    if path.startswith(("/fonts/", "/img/", "/js/vendor/")):
        resp.headers.setdefault("Cache-Control", "public, max-age=2592000, immutable")
    elif path.startswith(("/css/", "/js/")):
        resp.headers.setdefault("Cache-Control", "public, max-age=3600")
    return resp


def create_web_app(ctx: Ctx) -> web.Application:
    configure_serializer(ctx.settings.lite_mode)
    app = web.Application(middlewares=[cache_mw, auth_mw], client_max_size=2 * 1024 * 1024)
    app[CTX] = ctx
    app["wave_cache"] = {}
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
    for sub in ("css", "js", "fonts", "img"):
        r.add_static(f"/{sub}/", WEBAPP_DIR / sub, append_version=False)
    return app
