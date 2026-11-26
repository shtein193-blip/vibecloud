"""Leaderboard, admin-given statuses and paired ("duo") playlists."""
from __future__ import annotations

import logging

from .context import Ctx
from .serialize import track_out
from .tasks import spawn

log = logging.getLogger(__name__)
MAX_STATUS = 40
MAX_DUOS = 30


def user_out(r: dict) -> dict:
    return {"id": r["id"], "name": r.get("first_name") or (r.get("username") and "@" + r["username"]) or "Слушатель",
            "photo": r.get("photo_url"), "status": r.get("status"),
            "minutes": round((r.get("seconds") or 0) / 60)}


def duo_out(d: dict, uid: int) -> dict:
    mine_a = d["a"] == uid
    return {"id": d["id"], "title": d["title"], "state": d["state"],
            "incoming": d["state"] == "pending" and d["b"] == uid,
            "partner": {"id": d["b"] if mine_a else d["a"], "name": (d["b_name"] if mine_a else d["a_name"]) or "друг",
                        "photo": d["b_photo"] if mine_a else d["a_photo"],
                        "status": d["b_status"] if mine_a else d["a_status"]},
            "me": {"id": uid, "name": (d["a_name"] if mine_a else d["b_name"]) or "я",
                   "photo": d["a_photo"] if mine_a else d["b_photo"]},
            "track_count": d.get("track_count") or 0, "duration": round((d.get("duration_ms") or 0) / 1000),
            "artwork": d.get("artwork"), "created_at": d.get("created_at"), "updated_at": d.get("updated_at")}


async def leaderboard(ctx: Ctx, uid: int, period: str) -> dict:
    period = period if period in ("week", "month", "all") else "week"
    rows = await ctx.db.leaderboard(period, 100)
    me = await ctx.db.get_user(uid) or {}
    rank = await ctx.db.my_rank(uid, period)
    return {"period": period, "users": [user_out(r) for r in rows],
            "me": {**user_out({**me, "seconds": rank["seconds"]}), "rank": rank["rank"], "hidden": bool(me.get("hidden"))},
            "admin": ctx.is_admin(uid)}


async def set_status(ctx: Ctx, target: int, status: str | None, by: int | None = None) -> dict | None:
    user = await ctx.db.get_user(target)
    if not user:
        return None
    status = (status or "").strip()[:MAX_STATUS] or None
    await ctx.db.set_status(target, status)
    if ctx.tg and status and status != user.get("status") and target != by:
        from .bot import keyboards as kb
        from .bot.texts import e
        from aiogram.types import InlineKeyboardMarkup
        spawn(ctx.tg.notify(target, f"✨ Тебе выдан статус в VibeCloud: <b>{e(status)}</b>",
                            InlineKeyboardMarkup(inline_keyboard=[[kb.open_app(ctx, "🏆 Открыть топ", open="leaders")]])))
    ctx.bus.publish(target, "status", {"status": status})
    return {**user, "status": status}


async def invite(ctx: Ctx, a: int, b: int, title: str) -> dict:
    if a == b:
        raise ValueError("Парный плейлист — это с кем-то ещё 🙂")
    if not await ctx.db.get_user(b):
        raise ValueError("Пользователь не найден")
    if await ctx.db.pending_duo(a, b):
        raise ValueError("Приглашение уже отправлено — ждём ответа")
    if await ctx.db.count_outgoing_pending(a) >= 5:
        raise ValueError("Слишком много неотвеченных приглашений — дождись ответов")
    if len(await ctx.db.user_duos(a)) >= MAX_DUOS:
        raise ValueError("У тебя уже много парных плейлистов — удали ненужные")
    me = await ctx.db.get_user(a) or {}
    title = (title or "").strip()[:50] or f"{me.get('first_name') or 'Я'} + {(await ctx.db.get_user(b) or {}).get('first_name') or 'друг'}"
    did = await ctx.db.create_duo(a, b, title)
    d = await ctx.db.get_duo(did)
    ctx.bus.publish(b, "duo", duo_out(d, b))
    if ctx.tg:
        from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup
        from .bot import keyboards as kb
        from .bot.texts import e
        spawn(ctx.tg.notify(b, f"💞 <b>{e(me.get('first_name') or 'Кто-то')}</b> зовёт тебя в парный плейлист "
                               f"<b>«{e(title)}»</b> — будете собирать музыку вдвоём.",
                            InlineKeyboardMarkup(inline_keyboard=[
                                [kb.open_app(ctx, "💞 Открыть и принять", open=f"duo:{did}")],
                                [B(text="Отклонить", callback_data=f"duo:no:{did}")]])))
    return duo_out(d, a)


async def respond(ctx: Ctx, uid: int, did: str, accept: bool) -> dict | None:
    d = await ctx.db.get_duo(did)
    if not d or d["b"] != uid or d["state"] != "pending":
        return None
    if not accept:
        await ctx.db.delete_duo(did)
        ctx.bus.publish(d["a"], "duo_gone", {"id": did})
        return {"id": did, "deleted": True}
    await ctx.db.set_duo_state(did, "active")
    # start the playlist with what both already love
    common = await ctx.db.common_tracks(d["a"], d["b"], 20)
    if common:
        await ctx.db.add_duo_tracks(did, uid, common)
    d = await ctx.db.get_duo(did)
    ctx.bus.publish(d["a"], "duo", duo_out(d, d["a"]))
    if ctx.tg:
        from aiogram.types import InlineKeyboardMarkup
        from .bot import keyboards as kb
        from .bot.texts import e
        spawn(ctx.tg.notify(d["a"], f"💞 <b>{e(d['b_name'] or 'Друг')}</b> принял(а) приглашение в «{e(d['title'])}»",
                            InlineKeyboardMarkup(inline_keyboard=[[kb.open_app(ctx, "🎧 Открыть", open=f"duo:{did}")]])))
    return duo_out(d, uid)


async def member_duo(ctx: Ctx, uid: int, did: str) -> dict | None:
    d = await ctx.db.get_duo(did)
    return d if d and uid in (d["a"], d["b"]) else None


async def duo_detail(ctx: Ctx, uid: int, d: dict) -> dict:
    rows = await ctx.db.duo_tracks(d["id"], uid)
    names = {d["a"]: d["a_name"] or "друг", d["b"]: d["b_name"] or "друг"}
    tracks = [{**track_out(r, uid, ctx.signer), "added_by": r["added_by"],
               "added_by_name": "ты" if r["added_by"] == uid else names.get(r["added_by"], "друг")} for r in rows]
    out = duo_out(d, uid)
    out["tracks"] = tracks
    out["match"] = await ctx.db.taste_match(d["a"], d["b"])
    return out


async def add_tracks(ctx: Ctx, uid: int, d: dict, tids: list[int]) -> int:
    tids = [t for t in tids if isinstance(t, int)][:200]
    known = await ctx.db.library_entries(uid, tids)
    n = await ctx.db.add_duo_tracks(d["id"], uid, [t for t in tids if t in known])
    if n:
        other = d["b"] if d["a"] == uid else d["a"]
        fresh = await ctx.db.get_duo(d["id"])
        ctx.bus.publish(other, "duo", duo_out(fresh, other))
    return n


async def fill_common(ctx: Ctx, uid: int, d: dict) -> int:
    """«Общие вкусы»: tracks both have + the top tracks of each partner."""
    ids = await ctx.db.common_tracks(d["a"], d["b"], 30)
    ids += await ctx.db.top_tracks(d["a"], 8) + await ctx.db.top_tracks(d["b"], 8)
    return await add_tracks(ctx, uid, d, list(dict.fromkeys(ids)))
