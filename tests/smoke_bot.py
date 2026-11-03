"""Smoke test: runs bot handlers against a fake Telegram API (no token needed)."""
import asyncio, itertools, logging, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import shutil; shutil.rmtree("/tmp/vcbot", ignore_errors=True)
os.environ.update(DATA_DIR="/tmp/vcbot", BOT_TOKEN="", PREFETCH_PER_IMPORT="2", PUBLIC_URL="https://vibe.example.com",
                  WEBAPP_SHORT_NAME="app")

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.methods import (AnswerCallbackQuery, AnswerInlineQuery, DeleteMessage, EditMessageCaption,
                             EditMessageMedia, EditMessageReplyMarkup, EditMessageText, GetMe, SendAudio,
                             SendMediaGroup, SendMessage, SendPhoto)
from aiogram.types import Audio, Chat, Message, PhotoSize, Update, User

from app.config import load_settings
from app.context import Ctx
from app.db import DB
from app.downloader import Downloader
from app.events import EventBus
from app.importer import Importer
from app.security import Signer
from app.soundcloud import SoundCloud
from app.bot import handlers, inline
from app.bot.sender import TelegramIO

calls = []
ids = itertools.count(100)


class FakeSession(BaseSession):
    async def close(self): pass
    async def stream_content(self, *a, **k):
        yield b""
    async def make_request(self, bot, method, timeout=None):
        res = await self._make(bot, method)
        if isinstance(res, list):
            return [r.as_(bot) for r in res]
        return res.as_(bot) if hasattr(res, "as_") else res

    async def _make(self, bot, method):
        name = type(method).__name__
        calls.append((name, method))
        chat = Chat(id=getattr(method, "chat_id", 1) if isinstance(getattr(method, "chat_id", 1), int) else 1, type="private")
        def msg(**kw):
            return Message(message_id=next(ids), date=int(time.time()), chat=chat, **kw)
        if isinstance(method, GetMe):
            return User(id=42, is_bot=True, first_name="VibeCloud", username="VibeCloudBot")
        if isinstance(method, SendAudio):
            return msg(audio=Audio(file_id=f"FILE{next(ids)}", file_unique_id="u", duration=1))
        if isinstance(method, SendMediaGroup):
            return [msg(audio=Audio(file_id=f"FILE{next(ids)}", file_unique_id="u", duration=1)) for _ in method.media]
        if isinstance(method, SendPhoto):
            return msg(photo=[PhotoSize(file_id="p", file_unique_id="p", width=1, height=1)], caption=method.caption)
        if isinstance(method, (SendMessage, EditMessageText)):
            return msg(text=method.text)
        if isinstance(method, EditMessageCaption):
            return msg(caption=method.caption)
        return True


def upd(**kw):
    return Update(update_id=next(ids), **kw)


def message(text, uid=7):
    return {"message_id": next(ids), "date": int(time.time()), "chat": {"id": uid, "type": "private"},
            "from": {"id": uid, "is_bot": False, "first_name": "Алекс", "username": "alex"}, "text": text,
            "entities": [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}] if text.startswith("/") else None}


async def main():
    logging.basicConfig(level=logging.WARNING)
    s = load_settings()
    db = DB(s.db_path); await db.connect()
    bus, signer = EventBus(), Signer(s.secret)
    sc = SoundCloud(); await sc.start()
    dl = Downloader(s, db, bus); await dl.start()
    ctx = Ctx(settings=s, db=db, bus=bus, signer=signer, sc=sc, dl=dl, importer=Importer(s, db, sc, dl, bus, signer))
    bot = Bot("42:TEST", session=FakeSession(), default=DefaultBotProperties(parse_mode="HTML"))
    ctx.bot, ctx.bot_username = bot, (await bot.get_me()).username
    ctx.tg = TelegramIO(ctx)
    dp = Dispatcher(); dp["ctx"] = ctx
    dp.include_router(inline.router); dp.include_router(handlers.router)

    async def feed(u, label):
        n = len(calls)
        await dp.feed_update(bot, Update.model_validate(u, context={"bot": bot}))
        got = [c[0] for c in calls[n:]]
        print(f"[{label}] ->", got)
        return calls[n:]

    await feed({"update_id": 1, "message": message("/start")}, "start")
    await feed({"update_id": 2, "message": message("привет")}, "no link")
    out = await feed({"update_id": 3, "message": message("https://soundcloud.com/forss/flickermood")}, "track link")
    audio = [c[1] for c in out if c[0] == "SendAudio"]
    assert audio and audio[0].title == "Flickermood", audio
    print("   caption:", audio[0].caption.replace("\n", " | "))
    out = await feed({"update_id": 4, "message": message("https://soundcloud.com/forss/flickermood")}, "same link again (cached file_id)")
    assert isinstance([c[1] for c in out if c[0] == "SendAudio"][0].audio, str)
    out = await feed({"update_id": 5, "message": message("лови https://on.soundcloud.com/x-bad-link https://soundcloud.com/forss/sets/soulhack")}, "multi links")
    out = await feed({"update_id": 6, "message": message("https://soundcloud.com/forss")}, "profile")
    kb = [c[1] for c in out if c[0] in ("SendPhoto", "SendMessage")][-1].reply_markup
    print("   options:", [b.text for row in kb.inline_keyboard for b in row])
    cbdata = kb.inline_keyboard[1][0].callback_data
    await feed({"update_id": 7, "callback_query": {"id": "c1", "chat_instance": "x", "data": cbdata,
                "from": {"id": 7, "is_bot": False, "first_name": "Алекс"},
                "message": message("x")}}, f"choose {cbdata.split(':')[1]}")
    pl = (await db.user_playlists(7))[0]
    await feed({"update_id": 8, "callback_query": {"id": "c2", "chat_instance": "x", "data": f"watch:{pl['id']}",
                "from": {"id": 7, "is_bot": False, "first_name": "Алекс"}, "message": message("x")}}, "watch toggle")
    small = [p for p in await db.user_playlists(7) if p["title"] == "Soulhack"][0]
    out = await feed({"update_id": 9, "callback_query": {"id": "c3", "chat_instance": "x", "data": f"dlpl:{small['id']}",
                "from": {"id": 7, "is_bot": False, "first_name": "Алекс"}, "message": message("x")}}, "download playlist")
    print("   media groups:", [len(c[1].media) for c in out if c[0] == "SendMediaGroup"])
    out = await feed({"update_id": 10, "inline_query": {"id": "q1", "query": "flickermood", "offset": "",
                "from": {"id": 9, "is_bot": False, "first_name": "Друг"}}}, "inline search")
    res = [c[1] for c in out if c[0] == "AnswerInlineQuery"][0].results
    print("   inline results:", [(type(r).__name__, getattr(r, 'title', None)) for r in res[:4]])
    art = next(r for r in res if r.id.startswith("t"))
    await feed({"update_id": 11, "chosen_inline_result": {"result_id": art.id, "query": "x", "inline_message_id": "IM1",
                "from": {"id": 9, "is_bot": False, "first_name": "Друг"}}}, "chosen inline → swap to audio")
    code = await db.create_mixtape(7, "Для Лёши", "red", "", [r["id"] for r in await db.library(7)][:3])
    await feed({"update_id": 12, "message": message(f"/start mx_{code}", uid=9)}, "open mixtape deep link")
    await feed({"update_id": 13, "callback_query": {"id": "c4", "chat_instance": "x", "data": f"claim:{code}",
                "from": {"id": 9, "is_bot": False, "first_name": "Друг"}, "message": message("x", uid=9)}}, "claim mixtape")
    print("   friend library:", len(await db.library(9)))
    await feed({"update_id": 14, "message": message("/start tr_290", uid=9)}, "open shared track")
    await feed({"update_id": 15, "message": message("/library")}, "library")
    print("stats:", await db.stats(7))
    await dl.stop(); await sc.close(); await db.close()
    print("SMOKE OK")

asyncio.run(main())
