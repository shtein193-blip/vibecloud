"""Inline mode: @VibeCloudBot <query | link> in any chat → send a track to a friend."""
from __future__ import annotations

import logging
import re

from aiogram import Router
from aiogram.types import (ChosenInlineResult, InlineQuery, InlineQueryResultArticle,
                           InlineQueryResultCachedAudio, InlineQueryResultsButton, InputMediaAudio,
                           InputTextMessageContent, WebAppInfo)

from ..context import Ctx
from ..soundcloud import extract_links, track_meta
from . import keyboards as kb
from .texts import e, fmt_dur, track_caption

log = logging.getLogger(__name__)
router = Router(name="inline")


async def _rows_for_query(ctx: Ctx, uid: int, text: str) -> list[dict]:
    db = ctx.db
    if m := re.fullmatch(r"tr_(\d+)", text):
        row = await db.get_track(int(m.group(1)))
        return [row] if row else []
    if links := extract_links(text):
        r = await ctx.sc.resolve(links[0])
        raw = [r.data] if r.kind == "track" else \
            await ctx.sc.playlist_tracks(r.data, 20) if r.kind in ("playlist", "album") else []
    elif text:
        local = await db.search_library(uid, text, 10)
        raw = await ctx.sc.search(text, 15)
        seen = {x["id"] for x in local}
        for t in raw:
            if t["id"] not in seen:
                await db.upsert_track(track_meta(t))
        return local + [await db.get_track(t["id"]) for t in raw if t["id"] not in seen]
    else:
        return await db.all(
            """SELECT t.* FROM library l JOIN tracks t ON t.id=l.track_id WHERE l.user_id=?
               ORDER BY l.last_played DESC, l.added_at DESC LIMIT 20""", (uid,))
    for t in raw:
        await db.upsert_track(track_meta(t))
    return [await db.get_track(t["id"]) for t in raw]


@router.inline_query()
async def inline(q: InlineQuery, ctx: Ctx):
    uid = q.from_user.id
    await ctx.db.upsert_user({"id": uid, "first_name": q.from_user.first_name,
                              "username": q.from_user.username, "language_code": q.from_user.language_code})
    try:
        rows = await _rows_for_query(ctx, uid, q.query.strip())
    except Exception as ex:  # noqa: BLE001
        log.info("inline failed: %s", ex)
        rows = []
    results = []
    for row in [r for r in rows if r and r.get("status") != "unavailable"][:20]:
        caption = track_caption(row, ctx.bot_username)
        if row.get("tg_file_id"):
            results.append(InlineQueryResultCachedAudio(
                id=f"a{row['id']}", audio_file_id=row["tg_file_id"], caption=caption,
                reply_markup=kb.shared_track(ctx, row)))
        else:
            results.append(InlineQueryResultArticle(
                id=f"t{row['id']}", title=row["title"],
                description=f"{row['artist']} · {fmt_dur((row['duration_ms'] or 0) / 1000)}",
                thumbnail_url=(row.get("artwork") or "").replace("-t500x500.", "-t300x300.") or None,
                input_message_content=InputTextMessageContent(
                    message_text=f"🎧 <b>{e(row['title'])}</b>\n{e(row['artist'])}\n⏳ загружаю аудио…"),
                reply_markup=kb.loading(ctx, row["id"])))
    button = None
    if ctx.settings.public_url.startswith("https://"):
        button = InlineQueryResultsButton(text="🎧 Открыть VibeCloud", web_app=WebAppInfo(url=ctx.webapp_url()))
    await q.answer(results, cache_time=5, is_personal=True, button=button)


@router.chosen_inline_result()
async def chosen(res: ChosenInlineResult, ctx: Ctx):
    """Article was sent → upload audio and swap the message to a real audio (needs /setinlinefeedback)."""
    if not res.result_id.startswith("t") or not res.inline_message_id:
        return
    tid = int(res.result_id[1:])
    try:
        file_id = await ctx.tg.ensure_file_id(tid, res.from_user.id)
        row = await ctx.db.get_track(tid)
        await ctx.bot.edit_message_media(
            inline_message_id=res.inline_message_id,
            media=InputMediaAudio(media=file_id, caption=track_caption(row, ctx.bot_username)),
            reply_markup=kb.shared_track(ctx, row))
    except Exception as ex:  # noqa: BLE001
        log.warning("inline swap failed: %s", ex)
        row = await ctx.db.get_track(tid)
        if row:
            await ctx.bot.edit_message_text(
                inline_message_id=res.inline_message_id,
                text=f"🎧 <b>{e(row['title'])}</b>\n{e(row['artist'])}", reply_markup=kb.shared_track(ctx, row))
