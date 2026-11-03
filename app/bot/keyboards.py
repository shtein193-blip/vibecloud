from __future__ import annotations

from aiogram.types import (InlineKeyboardButton as B, InlineKeyboardMarkup as KB, WebAppInfo)

from ..context import Ctx


def open_app(ctx: Ctx, text: str = "🎧 Открыть VibeCloud", **params) -> B:
    if ctx.settings.public_url.startswith("https://"):
        return B(text=text, web_app=WebAppInfo(url=ctx.webapp_url(**params)))
    return B(text=text, url=ctx.app_link(params.get("open", "home").replace(":", "_")))


def welcome(ctx: Ctx) -> KB:
    return KB(inline_keyboard=[
        [open_app(ctx)],
        [B(text="📤 Отправить трек в чат", switch_inline_query="")],
    ])


def track(ctx: Ctx, row: dict, private: bool = True) -> KB:
    tid = row["id"]
    first = open_app(ctx, "▶️ В плеере", open=f"tr:{tid}") if private else \
        B(text="▶️ Слушать в VibeCloud", url=ctx.app_link(f"tr_{tid}"))
    rows = [[first, B(text="📤 Поделиться", switch_inline_query=f"tr_{tid}")]]
    if row.get("permalink"):
        rows.append([B(text="SoundCloud ↗", url=row["permalink"])])
    return KB(inline_keyboard=rows)


def shared_track(ctx: Ctx, row: dict) -> KB:
    rows = [[B(text="▶️ Слушать в VibeCloud", url=ctx.app_link(f"tr_{row['id']}"))]]
    if row.get("permalink"):
        rows[0].append(B(text="SoundCloud ↗", url=row["permalink"]))
    return KB(inline_keyboard=rows)


def loading(ctx: Ctx, tid: int) -> KB:
    return KB(inline_keyboard=[[B(text="⏳ Загружаю аудио…", url=ctx.app_link(f"tr_{tid}"))]])


def playlist(ctx: Ctx, pid: str, watch: bool, count: int) -> KB:
    return KB(inline_keyboard=[
        [open_app(ctx, "🎧 Открыть плейлист", open=f"pl:{pid}")],
        [B(text=f"⬇️ Скачать все {count}", callback_data=f"dlpl:{pid}"),
         B(text="🔕 Не следить" if watch else "🔔 Следить", callback_data=f"watch:{pid}")],
    ])


def profile(options: list[dict], token: str) -> KB:
    rows = []
    for o in options:
        if o["count"]:
            icon = {"likes": "❤️", "user_tracks": "🎵", "user_sets": "📚"}[o["mode"]]
            rows.append([B(text=f"{icon} {o['label']} · {o['count']}", callback_data=f"imp:{o['mode']}:{token}")])
    return KB(inline_keyboard=rows or [[B(text="Здесь пусто", callback_data="noop")]])


def received_track(ctx: Ctx, tid: int) -> KB:
    return KB(inline_keyboard=[
        [B(text="➕ В мою медиатеку", callback_data=f"add:{tid}"), open_app(ctx, "▶️ Плеер", open=f"tr:{tid}")],
    ])


def mixtape(ctx: Ctx, code: str) -> KB:
    return KB(inline_keyboard=[
        [open_app(ctx, "🎁 Открыть микстейп", open=f"mx:{code}")],
        [B(text="➕ Забрать себе", callback_data=f"claim:{code}")],
    ])
