"""VibeCloud entrypoint: Telegram bot (long polling) + Mini App web server in one process."""
from __future__ import annotations

import asyncio
import logging

from aiohttp import web

from .config import load_settings
from .context import Ctx
from .db import DB
from .downloader import Downloader
from .events import EventBus
from .importer import Importer
from .security import Signer
from .soundcloud import SoundCloud
from .web.api import create_web_app

log = logging.getLogger("vibecloud")


async def sync_loop(ctx: Ctx) -> None:
    """Watched playlists: pull new tracks and notify the owner."""
    from .bot import keyboards as kb
    from .bot.texts import e, tracks_word
    await asyncio.sleep(30)
    while True:
        for pl in await ctx.db.watched_playlists():
            fresh = await ctx.importer.sync_playlist(pl)
            if fresh and ctx.tg:
                names = "\n".join(f"• {e(r['artist'])} — {e(r['title'])}" for r in fresh[:5])
                await ctx.tg.notify(
                    pl["user_id"],
                    f"🔔 В «{e(pl['title'])}» {tracks_word(len(fresh))} новых — уже в медиатеке\n\n{names}",
                    kb.playlist(ctx, pl["id"], True, len(fresh)))
            await asyncio.sleep(1)
        await asyncio.sleep(ctx.settings.sync_interval_min * 60)


async def setup_bot(ctx: Ctx):
    from aiogram import Bot, Dispatcher
    from aiogram.client.default import DefaultBotProperties
    from aiogram.client.session.aiohttp import AiohttpSession
    from aiogram.client.telegram import TelegramAPIServer
    from aiogram.enums import ParseMode
    from aiogram.types import BotCommand, LinkPreviewOptions, MenuButtonWebApp, WebAppInfo

    from .bot import handlers, inline
    from .bot.sender import TelegramIO

    s = ctx.settings
    session = AiohttpSession(api=TelegramAPIServer.from_base(s.telegram_api_url, is_local=True)) \
        if s.telegram_api_url else AiohttpSession()
    session.timeout = 600  # big uploads
    bot = Bot(s.bot_token, session=session, default=DefaultBotProperties(
        parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    me = await bot.get_me()
    ctx.bot, ctx.bot_username = bot, me.username
    ctx.tg = TelegramIO(ctx)

    await bot.set_my_commands([
        BotCommand(command="start", description="Начать"),
        BotCommand(command="library", description="Моя медиатека"),
        BotCommand(command="search", description="Найти трек"),
        BotCommand(command="help", description="Какие ссылки я понимаю"),
    ])
    await bot.set_my_short_description("Переношу треки и плейлисты из SoundCloud в Telegram 🎧")
    if s.public_url.startswith("https://"):
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(
            text="VibeCloud", web_app=WebAppInfo(url=s.public_url + "/")))

    dp = Dispatcher()
    dp["ctx"] = ctx
    dp.include_router(inline.router)
    dp.include_router(handlers.router)
    return bot, dp


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = load_settings()
    backup_on = bool(s.bot_enabled and s.storage_chat_id and s.db_backup_min > 0)
    if backup_on:  # fresh container on an ephemeral disk → bring the library back first
        from aiogram import Bot as _Bot

        from .backup import restore
        tmp_bot = _Bot(s.bot_token)
        await restore(tmp_bot, s)
        await tmp_bot.session.close()
    db = DB(s.db_path)
    await db.connect()
    bus, signer = EventBus(), Signer(s.secret)
    sc = SoundCloud(s.sc_client_id)
    await sc.start()
    dl = Downloader(s, db, bus)
    await dl.start()
    ctx = Ctx(settings=s, db=db, bus=bus, signer=signer, sc=sc, dl=dl,
              importer=Importer(s, db, sc, dl, bus, signer))

    bot = dp = None
    if s.bot_enabled:
        bot, dp = await setup_bot(ctx)
    else:
        log.warning("BOT_TOKEN is empty — running web/API only (set DEV_USER_ID for local testing)")

    runner = web.AppRunner(create_web_app(ctx))
    await runner.setup()
    await web.TCPSite(runner, s.host, s.port).start()
    log.info("Mini App: %s  (listening on %s:%s)", s.public_url, s.host, s.port)
    sync_task = asyncio.create_task(sync_loop(ctx))
    backup = backup_task = None
    if backup_on and bot:
        from .backup import Backup
        backup = Backup(bot, db, s)
        backup_task = asyncio.create_task(backup.loop())
    try:
        if dp:
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
        else:
            await asyncio.Event().wait()
    finally:
        sync_task.cancel()
        if backup:  # Render sends SIGTERM on redeploy/restart — save the latest state before exit
            backup_task.cancel()
            try:
                await asyncio.wait_for(backup.snapshot(), 20)
            except Exception as ex:  # noqa: BLE001
                log.warning("final DB snapshot failed: %s", ex)
        await dl.stop()
        await runner.cleanup()
        await sc.close()
        await db.close()
        if bot:
            await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
