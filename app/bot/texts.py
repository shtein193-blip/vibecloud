from __future__ import annotations

from html import escape


def plural(n: int, one: str, few: str, many: str) -> str:
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return one
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return few
    return many


def tracks_word(n: int) -> str:
    return f"{n} {plural(n, 'трек', 'трека', 'треков')}"


def fmt_dur(seconds: float) -> str:
    s = int(seconds or 0)
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def fmt_long(seconds: float) -> str:
    s = int(seconds or 0)
    h, m = s // 3600, s % 3600 // 60
    return f"{h} ч {m} мин" if h else f"{m} мин"


def bar(p: float, n: int = 10) -> str:
    p = max(0.0, min(1.0, p))
    full = round(p * n)
    return "▰" * full + "▱" * (n - full)


def e(s: str | None) -> str:
    return escape(s or "")


def track_caption(row: dict, bot_username: str) -> str:
    link = f'<a href="{e(row.get("permalink"))}">SoundCloud</a>' if row.get("permalink") else ""
    return (f"<b>{e(row['title'])}</b>\n{e(row['artist'])} · {fmt_dur((row.get('duration_ms') or 0) / 1000)}\n"
            f"{link} · @{bot_username}")


WELCOME = (
    "<b>VIBECLOUD</b>\n"
    "Музыка из SoundCloud — прямо в Telegram.\n\n"
    "<b>Как перенести</b>\n"
    "→ Кинь ссылку: трек, плейлист, альбом или профиль\n"
    "→ Или в SoundCloud нажми «Поделиться» → Telegram → этот чат\n"
    "→ Можно несколько ссылок в одном сообщении\n\n"
    "<b>Что дальше</b>\n"
    "Всё сразу появляется в приложении: плеер, плейлисты, скачивание, микстейпы для друзей.\n"
    "В любом чате набери <code>@{bot} название</code>, чтобы отправить трек другу."
)

HELP = (
    "<b>Ссылки, которые я понимаю</b>\n"
    "• <code>soundcloud.com/artist/track</code> — трек, сразу пришлю MP3\n"
    "• <code>soundcloud.com/artist/sets/name</code> — плейлист или альбом целиком\n"
    "• <code>soundcloud.com/artist/likes</code> — все лайки\n"
    "• <code>soundcloud.com/artist</code> — профиль: выберешь, что забрать\n"
    "• короткие <code>on.soundcloud.com/…</code> из приложения\n\n"
    "<b>Команды</b>\n"
    "/library — открыть медиатеку\n"
    "/search <i>запрос</i> — найти трек\n"
    "/help — эта подсказка\n\n"
    "Треки принадлежат авторам — поддерживай артистов на SoundCloud ♥"
)
