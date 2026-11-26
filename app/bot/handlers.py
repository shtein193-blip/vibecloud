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

import re
import time

from .. import social, yandex
from ..context import Ctx
from ..db import new_id
from ..downloader import DownloadError
from ..importer import NeedChoice
from ..listimport import parse_tracklist
from ..soundcloud import SoundCloudError, extract_links
from ..tasks import spawn
from . import keyboards as kb
from .texts import HELP, WELCOME, bar, e, fmt_long, plural, tracks_word

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
    if payload.startswith("duo_"):
        return await message.answer("Парный плейлист 💞", reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[kb.open_app(ctx, "💞 Открыть", open=f"duo:{payload[4:]}")]]))
    if payload in ("leaders", "ewa", "yandex", "import", "library", "mixtapes"):
        return await message.answer("Открываю 👇", reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[kb.open_app(ctx, "🎧 Открыть VibeCloud", open=payload)]]))
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


# ---------------- Ewa, statuses, leaderboard ----------------
EWA_PREFIX = re.compile(r"^\s*(ewa|эва|ева|эwa)[\s,:!]+", re.I)


async def _ewa_answer(message: Message, ctx: Ctx, uid: int, text: str):
    await ctx.bot.send_chat_action(message.chat.id, "typing")
    res = await ctx.ewa.reply(uid, text)
    rows = [[InlineKeyboardButton(text=f"➕ {t['artist']} — {t['title']}"[:60], callback_data=f"ewa:{t['id']}")]
            for t in res["tracks"][:6]]
    labels = {"import": "📥 Перенос", "yandex": "🟡 Из Яндекс Музыки", "library": "🎧 Медиатека",
              "mixtapes": "🎁 Микстейпы", "leaders": "🏆 Топ", "home": "🎧 VibeCloud"}
    for a in res["actions"][:2]:
        rows.append([kb.open_app(ctx, labels.get(a["open"], "🎧 Открыть"), open=a["open"])])
    await message.answer(f"💜 <b>Ewa</b>\n{e(res['text'])}",
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)


@router.message(Command("ewa"))
async def cmd_ewa(message: Message, command: CommandObject, ctx: Ctx):
    uid = await _user(ctx, message)
    q = (command.args or "").strip()
    if not q:
        return await message.answer("💜 Я Ewa — помогу найти музыку и разобраться в VibeCloud.\n"
                                    "Напиши, например: <code>/ewa что послушать под дождь</code> "
                                    "или начни сообщение с «Ewa,».")
    await _ewa_answer(message, ctx, uid, q)


@router.callback_query(F.data.startswith("ewa:"))
async def cb_ewa_add(cb: CallbackQuery, ctx: Ctx):
    tid = int(cb.data.split(":", 1)[1])
    uid = await _user(ctx, cb)
    row = await ctx.db.get_track(tid)
    if not row:
        return await cb.answer("Трек не найден", show_alert=True)
    await ctx.db.add_to_library(uid, tid)
    await cb.answer(f"✓ «{row['title'][:40]}» в медиатеке — отправляю")
    spawn(ctx.tg.send_track(cb.message.chat.id, tid, uid), "ewa-send")


@router.message(Command("myid"))
async def cmd_myid(message: Message, ctx: Ctx):
    await message.answer(f"Твой Telegram ID: <code>{message.from_user.id}</code>\n"
                         "Администратор указывает такие ID в переменной ADMIN_IDS.")


@router.message(Command("top"))
async def cmd_top(message: Message, ctx: Ctx):
    uid = await _user(ctx, message)
    data = await social.leaderboard(ctx, uid, "week")
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"{medals[i] if i < 3 else f'{i + 1}.'} <b>{e(u['name'])}</b>"
             f"{' · ' + e(u['status']) if u['status'] else ''} — {u['minutes']} мин"
             for i, u in enumerate(data["users"][:10])]
    me = data["me"]
    tail = f"\n\nТы: {me['rank']}-е место · {me['minutes']} мин" if me.get("rank") else "\n\nВключай музыку — и попадёшь в топ 🎧"
    await message.answer("🏆 <b>Топ слушателей недели</b>\n\n" + ("\n".join(lines) or "Пока пусто") + tail,
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=[[kb.open_app(ctx, "🏆 Весь топ", open="leaders")]]))


@router.message(Command("status"))
async def cmd_status(message: Message, command: CommandObject, ctx: Ctx):
    if not ctx.is_admin(message.from_user.id):
        return await message.answer("Статусы выдаёт администратор VibeCloud ✨")
    args = (command.args or "").strip()
    if not args and message.reply_to_message:  # reply to a forwarded message of the user
        return await message.answer("Формат: <code>/status @username Текст статуса</code>")
    parts = args.split(maxsplit=1)
    if len(parts) < 2:
        return await message.answer("Формат: <code>/status @username Текст</code> или <code>/status 123456789 Текст</code>\n"
                                    "Снять статус: <code>/status @username -</code>\nСписок: /statuses")
    user = await ctx.db.find_user(parts[0])
    if not user:
        return await message.answer("Не нашёл такого пользователя. Он должен хотя бы раз открыть бота. "
                                    "ID можно узнать командой /myid у него.")
    status = None if parts[1].strip() in ("-", "—", "нет", "off") else parts[1]
    await social.set_status(ctx, user["id"], status, by=message.from_user.id)
    name = e(user.get("first_name") or user.get("username") or str(user["id"]))
    await message.answer(f"✓ {name}: статус {'«' + e(status[:40]) + '»' if status else 'снят'}")


@router.message(Command("statuses"))
async def cmd_statuses(message: Message, ctx: Ctx):
    if not ctx.is_admin(message.from_user.id):
        return
    rows = await ctx.db.users_with_status()
    text = "\n".join(f"• {e(r['first_name'] or '')} (@{e(r['username'] or '—')}, <code>{r['id']}</code>): {e(r['status'])}"
                     for r in rows) or "Статусов пока нет"
    await message.answer("<b>Выданные статусы</b>\n" + text)


# ---------------- track lists (Yandex Music & co) ----------------
async def _run_list(message: Message, ctx: Ctx, uid: int, text: str, title: str | None = None,
                    items: list[dict] | None = None):
    items = items if items is not None else parse_tracklist(text)
    n = min(len(items), ctx.settings.list_import_max)
    status = await message.answer(f"🔎 Ищу {tracks_word(n)} на SoundCloud…\n{bar(0)} 0/{n}")
    last = {"t": 0.0}

    async def progress(done: int, total: int, found: int):
        if time.monotonic() - last["t"] < 3 and done < total:
            return
        last["t"] = time.monotonic()
        try:
            await status.edit_text(f"🔎 Ищу на SoundCloud: {done}/{total}\n{bar(done / max(total, 1))} найдено {found}")
        except TelegramBadRequest:
            pass
    try:
        res = await ctx.lists.run(uid, text, title, on_progress=progress, items=items)
    except SoundCloudError as ex:
        return await status.edit_text(f"😕 {e(str(ex))}")
    miss = res["missing"]
    text_out = (f"✓ <b>{e(res['title'])}</b>: нашёл {res['found']} из {res['total']} — уже в медиатеке и в плейлисте.")
    if miss:
        sample = "\n".join(f"• {e(m['artist'])} — {e(m['title'])}" for m in miss[:10])
        more = f"\n…и ещё {len(miss) - 10}" if len(miss) > 10 else ""
        text_out += f"\n\nНе нашлось на SoundCloud ({len(miss)}):\n{sample}{more}"
    await status.edit_text(text_out, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [kb.open_app(ctx, "🎧 Открыть плейлист", open=f"pl:{res['playlist_id']}")]]))


@router.message(F.chat.type == ChatType.PRIVATE, F.document)
async def on_document(message: Message, ctx: Ctx):
    doc = message.document
    name = (doc.file_name or "").lower()
    if not name.endswith((".txt", ".csv", ".json", ".tsv", ".m3u", ".m3u8")):
        return await message.answer("Пришли список треков файлом .txt, .csv или .json — найду их на SoundCloud.")
    if doc.file_size and doc.file_size > 3 * 1024 * 1024:
        return await message.answer("Файл слишком большой — до 3 МБ, пожалуйста.")
    uid = await _user(ctx, message)
    f = await ctx.bot.get_file(doc.file_id)
    buf = await ctx.bot.download_file(f.file_path)
    raw = buf.read()
    text = next((raw.decode(enc) for enc in ("utf-8-sig", "cp1251") if _decodes(raw, enc)), raw.decode(errors="ignore"))
    if name.endswith((".m3u", ".m3u8")):  # #EXTINF:123,Artist - Title
        text = "\n".join(l.split(",", 1)[1] for l in text.splitlines() if l.startswith("#EXTINF") and "," in l)
    if not parse_tracklist(text):
        return await message.answer("Не нашёл в файле треков 😕 Нужно «Исполнитель — Название» по строке или CSV "
                                    "с колонками Artist / Track.")
    title = (doc.file_name or "Яндекс Музыка").rsplit(".", 1)[0][:60]
    await _run_list(message, ctx, uid, text, title if title.lower() not in ("export", "tracks", "playlist") else None)


def _decodes(raw: bytes, enc: str) -> bool:
    try:
        raw.decode(enc)
        return True
    except UnicodeDecodeError:
        return False


@router.callback_query(F.data.startswith("list:"))
async def cb_list(cb: CallbackQuery, ctx: Ctx):
    text = ctx.pending_links.pop(cb.data.split(":", 1)[1])
    if not text:
        return await cb.answer("Список устарел — пришли его ещё раз", show_alert=True)
    await cb.answer("Ищу на SoundCloud…")
    uid = await _user(ctx, cb)
    await _run_list(cb.message, ctx, uid, text)


# ---------------- links ----------------
@router.message(F.chat.type == ChatType.PRIVATE, F.text | F.caption)
async def on_links(message: Message, ctx: Ctx):
    links = _links(message)
    if not links:
        if message.via_bot:
            return
        text = message.text or message.caption or ""
        uid = await _user(ctx, message)
        if (ylink := yandex.find_link(text)) and len(parse_tracklist(text.replace(ylink, ""))) < 2:
            status = await message.answer("🔎 Читаю плейлист Яндекс Музыки…")
            try:
                pl = await yandex.fetch(ylink, ctx.settings.list_import_max)
            except yandex.YandexError as ex:
                return await status.edit_text(f"😕 {e(str(ex))}")
            await _safe_delete(status)
            return await _run_list(message, ctx, uid, "", pl["title"], items=pl["items"])
        if m := EWA_PREFIX.match(text):
            return await _ewa_answer(message, ctx, uid, text[m.end():])
        items = parse_tracklist(text) if text.count("\n") >= 1 else []
        if len(items) >= 2:
            token = new_id(6)
            ctx.pending_links[token] = text
            sample = "\n".join(f"• {e(i['artist'])} — {e(i['title'])}" for i in items[:3])
            return await message.answer(
                f"Похоже на список из {tracks_word(len(items))}:\n{sample}\n\nНайти их на SoundCloud и добавить в медиатеку?",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="🔎 Найти и добавить", callback_data=f"list:{token}")]]))
        if len(text) > 3 and not text.startswith("/"):
            return await _ewa_answer(message, ctx, uid, text)
        return await message.answer(
            "Кинь ссылку на SoundCloud — трек, плейлист, альбом или профиль.\n"
            "Например: <code>soundcloud.com/forss/sets/soulhack</code>\n\n"
            "Список треков из Яндекс Музыки — пришли текстом или файлом. Вопросы — Ewa: /ewa", reply_markup=kb.welcome(ctx))
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
        spawn(_live_progress(ctx, card, head, tids, pid), "live-progress")


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


@router.callback_query(F.data.startswith("duo:no:"))
async def cb_duo_no(cb: CallbackQuery, ctx: Ctx):
    res = await social.respond(ctx, cb.from_user.id, cb.data.split(":", 2)[2], False)
    await cb.answer("Приглашение отклонено" if res else "Уже неактуально")
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass


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
