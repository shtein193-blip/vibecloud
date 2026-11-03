from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup,
                           Message)

from ..context import Ctx
from ..db import new_id
from ..downloader import DownloadError
from ..importer import NeedChoice
from ..soundcloud import SoundCloudError, extract_links
from . import keyboards as kb
from .texts import HELP, WELCOME, bar, e, fmt_long, tracks_word

log = logging.getLogger(__name__)
router = Router(name="main")
ASSETS = Path(__file__).resolve().parents[2] / "assets"


def _links(message: Message) -> list[str]:
    text = message.text or message.caption or ""
    urls = extract_links(text)
    for ent in (message.entities or message.caption_entities or []):
        if ent.type == "text_link" and ent.url:
            urls += [u for u in extract_links(ent.url) if u not in urls]
    return urls[:5]


async def _user(ctx: Ctx, message_or_cb) -> int:
    u = message_or_cb.from_user
    await ctx.db.upsert_user({"id": u.id, "first_name": u.first_name, "username": u.username,
                              "language_code": u.language_code})
    return u.id


# ---------------- /start & commands ----------------
@router.message(CommandStart())
async def start(message: Message, command: CommandObject, ctx: Ctx):
    uid = await _user(ctx, message)
    payload = (command.args or "").strip()
    if payload.startswith("tr_") and payload[3:].isdigit():
        return await _open_shared_track(message, ctx, int(payload[3:]), uid)
    if payload.startswith("mx_"):
        return await _open_mixtape(message, ctx, payload[3:])
    if payload.startswith("pl_"):
        return await message.answer("Плейлист в приложении 👇", reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[kb.open_app(ctx, "🎧 Открыть плейлист", open=f"pl:{payload[3:]}")]]))
    caption = WELCOME.replace("{bot}", ctx.bot_username)
    banner = ASSETS / "welcome.jpg"
    if banner.exists():
        await message.answer_photo(FSInputFile(banner), caption=caption, reply_markup=kb.welcome(ctx))
    else:
        await message.answer(caption, reply_markup=kb.welcome(ctx))


@router.message(Command("help"))
async def help_(message: Message, ctx: Ctx):
    await message.answer(HELP, reply_markup=kb.welcome(ctx))


@router.message(Command("library"))
async def library(message: Message, ctx: Ctx):
    uid = await _user(ctx, message)
    st = await ctx.db.stats(uid)
    await message.answer(
        f"<b>Твоя медиатека</b>\n{tracks_word(st['tracks'])} · {st['hours']} ч · {st['playlists']} плейлистов",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[kb.open_app(ctx, "🎧 Открыть", open="library")]]))


@router.message(Command("search"))
async def search(message: Message, command: CommandObject, ctx: Ctx):
    q = (command.args or "").strip()
    if not q:
        return await message.answer("Напиши так: <code>/search moderat</code>")
    await message.answer(f"🔎 Ищу «{e(q)}» — нажми кнопку, выбери трек:", reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Показать результаты", switch_inline_query_current_chat=q)]]))


# ---------------- links ----------------
@router.message(F.chat.type == ChatType.PRIVATE, F.text | F.caption)
async def on_links(message: Message, ctx: Ctx):
    links = _links(message)
    if not links:
        if message.via_bot:
            return
        return await message.answer(
            "Кинь ссылку на SoundCloud — трек, плейлист, альбом или профиль.\n"
            "Например: <code>soundcloud.com/forss/sets/soulhack</code>", reply_markup=kb.welcome(ctx))
    uid = await _user(ctx, message)
    for url in links:
        await _handle_link(message, ctx, uid, url)


async def _handle_link(message: Message, ctx: Ctx, uid: int, url: str, mode: str | None = None):
    status = await message.answer("🔍 Смотрю ссылку…")
    try:
        res = await ctx.importer.run(uid, url, mode=mode)
    except NeedChoice as nc:
        p = nc.preview
        token = new_id(6)
        ctx.pending_links[token] = p["url"]
        caption = f"<b>{e(p['title'])}</b>\nПрофиль SoundCloud\n\nЧто перенести в VibeCloud?"
        await _safe_delete(status)
        return await _photo_or_text(message, p.get("artwork"), caption, kb.profile(p["options"], token))
    except (SoundCloudError, DownloadError) as ex:
        return await status.edit_text(f"😕 {e(str(ex))}")
    except Exception:  # noqa: BLE001
        log.exception("import failed")
        return await status.edit_text("😕 Что-то пошло не так. Попробуй ещё раз чуть позже.")

    if res["kind"] == "track":
        tid = res["track_ids"][0]
        row = await ctx.db.get_track(tid)
        if row["status"] == "unavailable":
            return await status.edit_text(
                f"🔒 <b>{e(row['title'])}</b> доступен только подписчикам SoundCloud Go+ или в другом регионе.\n"
                f"Добавил в медиатеку как ссылку.", reply_markup=kb.track(ctx, row))
        try:
            await ctx.tg.send_track(message.chat.id, tid, uid, status=status)
            await _safe_delete(status)
        except DownloadError as ex:
            await status.edit_text(f"😕 Не получилось скачать: {e(str(ex))}")
        return

    await _safe_delete(status)
    await _playlist_card(message, ctx, uid, res)


async def _playlist_card(message: Message, ctx: Ctx, uid: int, res: dict):
    if res["kind"] == "user_sets":
        text = (f"<b>{e(res['title'])}</b>\n✓ Перенёс {res.get('playlists', 0)} плейлистов · "
                f"{tracks_word(res['total'])}")
        return await message.answer(text, reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[kb.open_app(ctx, "🎧 Открыть медиатеку", open="library")]]))
    pid = res["playlist_id"]
    pl = await ctx.db.get_playlist(pid)
    head = (f"<b>{e(res['title'])}</b>\n{e(res['artist'] or '')} · {tracks_word(res['total'])} · "
            f"{fmt_long((pl['duration_ms'] or 0) / 1000)}\n\n✓ В медиатеке VibeCloud")
    if res["new"] != res["total"]:
        head += f" (новых: {res['new']})"
    if res["unavailable"]:
        head += f"\n🔒 {res['unavailable']} — только Go+, сохранены ссылками"
    tids = res["track_ids"][:ctx.settings.prefetch_per_import]
    if not tids:  # LITE: nothing is downloaded in advance — MP3s come straight from Telegram on demand
        head += "\n▶️ Слушай в VibeCloud сразу · «Скачать все» пришлёт MP3 сюда"
    markup = kb.playlist(ctx, pid, bool(pl["watch"]), res["total"])
    card = await _photo_or_text(message, res.get("artwork"), head, markup)
    if tids:
        asyncio.create_task(_live_progress(ctx, card, head, tids, pid))


async def _live_progress(ctx: Ctx, card: Message, head: str, tids: list[int], pid: str):
    """Edits the playlist card while the first tracks are being downloaded."""
    last = ""
    for _ in range(300):  # ~10 min max
        rows = await ctx.db.all(
            f"SELECT status, tg_file_id FROM tracks WHERE id IN ({','.join('?' * len(tids))})", tids) if tids else []
        ready = sum(1 for r in rows if r["status"] == "ready" or r["tg_file_id"])
        skipped = sum(1 for r in rows if r["status"] in ("unavailable", "error"))
        total = max(len(rows) - skipped, 1)
        done = ready >= total
        line = "⚡ Готово к прослушиванию офлайн" if done else f"⬇️ Скачиваю {bar(ready / total)} {ready}/{total}"
        text = f"{head}\n{line}"
        if text != last:
            last = text
            pl = await ctx.db.get_playlist(pid)
            markup = kb.playlist(ctx, pid, bool(pl["watch"]), pl["track_count"])
            try:
                if card.photo:
                    await card.edit_caption(caption=text, reply_markup=markup)
                else:
                    await card.edit_text(text, reply_markup=markup)
            except TelegramBadRequest:
                pass
        if done:
            return
        await asyncio.sleep(2)


# ---------------- callbacks ----------------
@router.callback_query(F.data.startswith("imp:"))
async def cb_import(cb: CallbackQuery, ctx: Ctx):
    _, mode, token = cb.data.split(":", 2)
    url = ctx.pending_links.get(token)
    if not url:
        return await cb.answer("Ссылка устарела — пришли её ещё раз", show_alert=True)
    await cb.answer("Переношу…")
    uid = await _user(ctx, cb)
    await _handle_link(cb.message, ctx, uid, url, mode=mode)


@router.callback_query(F.data.startswith("dlpl:"))
async def cb_download_playlist(cb: CallbackQuery, ctx: Ctx):
    pid = cb.data.split(":", 1)[1]
    pl = await ctx.db.get_playlist(pid)
    if not pl or pl["user_id"] != cb.from_user.id:
        return await cb.answer("Плейлист не найден", show_alert=True)
    rows = await ctx.db.playlist_tracks(pid, cb.from_user.id)
    tids = [r["id"] for r in rows if r["status"] != "unavailable"]
    await cb.answer(f"Отправляю {tracks_word(len(tids))}")
    status = await cb.message.answer(f"📤 Готовлю <b>{e(pl['title'])}</b>: 0/{len(tids)}")
    sent, failed = await ctx.tg.send_many(cb.message.chat.id, tids, cb.from_user.id, status=status)
    note = f"✓ Отправил {sent} из {len(tids)}" + (f" · не вышло: {failed}" if failed else "")
    await status.edit_text(note)


@router.callback_query(F.data.startswith("watch:"))
async def cb_watch(cb: CallbackQuery, ctx: Ctx):
    pid = cb.data.split(":", 1)[1]
    pl = await ctx.db.get_playlist(pid)
    if not pl or pl["user_id"] != cb.from_user.id:
        return await cb.answer("Плейлист не найден", show_alert=True)
    watch = not pl["watch"]
    await ctx.db.set_watch(pid, watch)
    await cb.answer("🔔 Пришлю, когда появятся новые треки" if watch else "Больше не слежу")
    try:
        await cb.message.edit_reply_markup(reply_markup=kb.playlist(ctx, pid, watch, pl["track_count"]))
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("add:"))
async def cb_add(cb: CallbackQuery, ctx: Ctx):
    tid = int(cb.data.split(":", 1)[1])
    uid = await _user(ctx, cb)
    if not await ctx.db.get_track(tid):
        return await cb.answer("Трек не найден", show_alert=True)
    await ctx.db.add_to_library(uid, tid)
    await cb.answer("✓ В медиатеке")


@router.callback_query(F.data.startswith("claim:"))
async def cb_claim(cb: CallbackQuery, ctx: Ctx):
    from ..web.api import claim_mixtape  # local import to avoid cycle
    uid = await _user(ctx, cb)
    code = cb.data.split(":", 1)[1]
    try:
        pid, n = await claim_mixtape(ctx, uid, code)
    except LookupError:
        return await cb.answer("Микстейп не найден", show_alert=True)
    await cb.answer(f"✓ {tracks_word(n)} в твоей медиатеке")
    await cb.message.answer("Микстейп сохранён 🎁", reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[kb.open_app(ctx, "🎧 Открыть", open=f"pl:{pid}")]]))


@router.callback_query(F.data == "noop")
async def cb_noop(cb: CallbackQuery):
    await cb.answer()


# ---------------- deep-link targets ----------------
async def _open_shared_track(message: Message, ctx: Ctx, tid: int, uid: int):
    row = await ctx.db.get_track(tid)
    if not row:
        return await message.answer("Трек не найден 😕", reply_markup=kb.welcome(ctx))
    if row.get("tg_file_id"):
        await message.answer_audio(row["tg_file_id"], reply_markup=kb.received_track(ctx, tid))
    else:
        status = await message.answer("⏳ Загружаю трек…")
        try:
            await ctx.tg.send_track(message.chat.id, tid, uid, status=status)
        finally:
            await _safe_delete(status)


async def _open_mixtape(message: Message, ctx: Ctx, code: str):
    mx = await ctx.db.get_mixtape(code)
    if not mx:
        return await message.answer("Микстейп не найден 😕", reply_markup=kb.welcome(ctx))
    tracks = await ctx.db.mixtape_tracks(code)
    caption = (f"🎁 <b>{e(mx['title'])}</b>\nМикстейп от {e(mx['owner_name'] or 'друга')} · "
               f"{tracks_word(len(tracks))} · {fmt_long((mx['duration_ms'] or 0) / 1000)}")
    if mx.get("note"):
        caption += f"\n\n<i>«{e(mx['note'])}»</i>"
    cover = next((t["artwork"] for t in tracks if t.get("artwork")), None)
    await _photo_or_text(message, cover, caption, kb.mixtape(ctx, code))


# ---------------- utils ----------------
async def _photo_or_text(message: Message, photo: str | None, text: str, markup) -> Message:
    if photo:
        try:
            return await message.answer_photo(photo, caption=text, reply_markup=markup)
        except TelegramBadRequest:
            pass
    return await message.answer(text, reply_markup=markup)


async def _safe_delete(msg: Message | None):
    if msg:
        try:
            await msg.delete()
        except TelegramBadRequest:
            pass
