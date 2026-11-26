"""Ewa — the VibeCloud music assistant.

Works on any free OpenAI-compatible API (Groq, Gemini, OpenRouter, Cerebras — see AI_PROVIDER in
.env.example). Without a key she still answers app questions from a built-in FAQ and searches music,
so the feature is free in every setup.

Tool protocol (works with any model, no function calling needed): the model may put
[[search: query]] (find tracks on SoundCloud) and [[open: view]] (a button to a screen) into its answer.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

import aiohttp

from .config import Settings
from .db import DB
from .security import Signer
from .serialize import track_out
from .soundcloud import SoundCloud, SoundCloudError, track_meta
from .tasks import TTLCache

log = logging.getLogger(__name__)

VIEWS = {"home", "library", "import", "mixtapes", "leaders", "yandex"}
TAG_RE = re.compile(r"\[\[\s*(search|open)\s*:\s*([^\]]{1,120})\]\]", re.I)

APP_FACTS = """\
VibeCloud — Telegram-бот и мини-приложение: переносит музыку из SoundCloud в Telegram.
• Перенос: кинуть боту или вставить в приложении (вкладка «Перенос») ссылку SoundCloud на трек, плейлист, альбом,
  лайки или профиль. Можно «Поделиться» из приложения SoundCloud прямо в чат с ботом. Плейлист до 500 треков.
• Из Яндекс Музыки: в Яндекс Музыке открыть плейлист → ⋯ → «Поделиться» → «Скопировать ссылку» и вставить ссылку
  на вкладке «Перенос» в блок «Из Яндекс Музыки» (или просто отправить ссылку боту). Плейлист должен быть открытым.
  VibeCloud читает только названия треков и находит те же песни на SoundCloud; ненайденные показываются списком.
  Ещё можно вставить список «Исполнитель — Название» или файл .txt/.csv.
  Формат «Исполнитель — Название» по строке тоже подходит. VibeCloud ищет те же песни среди публичных треков
  SoundCloud и добавляет в медиатеку и плейлист «Яндекс Музыка». Не найденные показываются списком с кнопкой поиска.
  Можно и прислать боту файл со списком.
• Медиатека: все треки, лайки, плейлисты; поиск по медиатеке и по SoundCloud.
• Плеер: свайп по обложке — следующий/предыдущий трек, перемешать, повтор, волна для перемотки.
• «Скачать» / «В чат»: MP3 приходит в чат с ботом. «Другу»: отправить трек в любой чат.
• Инлайн: в любом чате набрать @бот название — трек уйдёт аудиофайлом.
• Микстейпы: собрать подборку, выбрать постер, отправить другу — он заберёт все треки в один тап.
• Слежение 🔔 за плейлистом: новые треки добавляются сами.
• Топ (вкладка с кубком): рейтинг слушателей по минутам прослушивания за неделю/месяц/всё время. Статусы рядом с
  именем выдаёт администратор. Можно скрыть себя из топа переключателем.
• Парный плейлист: в Топе нажать на человека → «Парный плейлист». Он получит приглашение; после принятия оба
  добавляют треки, есть кнопка «Общие вкусы» — добавит треки, которые есть у обоих.
• Треки Go+/с DRM помечены замком 🔒 — их можно слушать только в SoundCloud.
• Музыка принадлежит артистам: поддерживай их на SoundCloud.
"""

SYSTEM = """Ты — Ewa (Эва), музыкальная ведущая и помощница в приложении VibeCloud. Отвечай по-русски \
(или на языке пользователя), коротко, тепло и по делу: 1–4 предложения, без markdown-заголовков и таблиц. \
Ты разбираешься в музыке любых жанров, подбираешь треки под настроение, объясняешь, как пользоваться приложением.

Инструменты (вставляй прямо в ответ, пользователь их не увидит, увидит результат):
[[search: исполнитель название]] — найти треки на SoundCloud, покажутся карточками с кнопкой «добавить». \
Используй, когда просят найти/посоветовать/включить музыку; максимум 3 поиска, каждый — конкретный запрос \
(«Massive Attack Teardrop», а не «грустная музыка»). Не выдумывай ссылки.
[[open: import|yandex|library|mixtapes|leaders]] — кнопка перехода на экран приложения.

Факты о приложении (отвечай строго по ним, не придумывай функций):
{facts}
О пользователе: {about}"""

FAQ = [
    (r"яндекс|yandex", "Проще простого: в Яндекс Музыке открой плейлист → ⋯ → «Поделиться» → «Скопировать ссылку» и "
     "вставь её на вкладке «Перенос» (или просто пришли ссылку боту). Плейлист должен быть открытым — я найду те же "
     "песни на SoundCloud. [[open: yandex]]"),
    (r"перен|импорт|ссылк|добав.*(трек|плейлист)|как.*(залить|загрузить)",
     "Кинь ссылку SoundCloud на трек, плейлист, альбом, лайки или профиль — в чат с ботом или на вкладке «Перенос». "
     "Из приложения SoundCloud можно сразу «Поделиться» → Telegram → бот. [[open: import]]"),
    (r"скача|mp3|сохран|офлайн", "Нажми «Скачать» или «В чат» в плеере — MP3 придёт в чат с ботом, оттуда его можно "
     "сохранить и слушать офлайн в Telegram."),
    (r"микстейп|подборк|другу", "Микстейп — подборка для друга с постером: выбери треки, стиль и записку, отправь. "
     "Друг заберёт всё в один тап. [[open: mixtapes]]"),
    (r"топ|рейтинг|лидер|статус", "В Топе — рейтинг слушателей по минутам за неделю, месяц и всё время. Статусы рядом "
     "с именами выдаёт администратор. Если не хочешь светиться — там же можно скрыть себя. [[open: leaders]]"),
    (r"парн|дуэт|совмест|вдвоём|вдвоем", "Парный плейлист: открой Топ, нажми на человека и выбери «Парный плейлист». "
     "После того как он примет приглашение, вы оба сможете добавлять треки, а «Общие вкусы» подкинут то, что есть у "
     "обоих. [[open: leaders]]"),
    (r"замок|go\+|недоступ|drm|🔒", "Замок 🔒 — трек только для подписчиков SoundCloud Go+ или закрыт в регионе. "
     "Его можно открыть в SoundCloud, но скачать нельзя."),
    (r"следи|уведомл|🔔|обновл", "Включи 🔔 у плейлиста — когда в нём на SoundCloud появятся новые треки, они сами "
     "добавятся в медиатеку, а бот напишет."),
    (r"привет|здравств|хай|hello|hi\b|кто ты", "Привет! Я Ewa — помогу найти музыку и разобраться в VibeCloud. "
     "Спроси, что послушать, или напиши исполнителя."),
]
MUSIC_HINT = re.compile(r"найд|найти|поищи|включи|постав|посоветуй|подбери|хочу послушать|трек|песн|музык|альбом|"
                        r"song|track|play", re.I)


class Ewa:
    def __init__(self, s: Settings, db: DB, sc: SoundCloud, signer: Signer):
        self.s, self.db, self.sc, self.signer = s, db, sc, signer
        self.http: aiohttp.ClientSession | None = None
        self.limits: TTLCache[int, list[float]] = TTLCache(maxsize=5000, ttl=900)

    async def close(self) -> None:
        if self.http:
            await self.http.close()

    def _allow(self, uid: int) -> bool:
        hits = [t for t in (self.limits.get(uid) or []) if time.monotonic() - t < 600]
        if len(hits) >= 25:
            return False
        hits.append(time.monotonic())
        self.limits[uid] = hits
        return True

    async def _about(self, uid: int) -> str:
        st = await self.db.stats(uid)
        artists = await self.db.all(
            """SELECT t.artist, COUNT(*) AS n FROM library l JOIN tracks t ON t.id=l.track_id WHERE l.user_id=?
               GROUP BY lower(t.artist) ORDER BY SUM(l.play_count) DESC, n DESC LIMIT 6""", (uid,))
        user = await self.db.get_user(uid) or {}
        bits = [f"зовут {user.get('first_name') or 'друг'}", f"в медиатеке {st['tracks']} треков",
                f"послушал {st['listened_min']} мин"]
        if artists:
            bits.append("любимые артисты: " + ", ".join(a["artist"] for a in artists if a["artist"]))
        if st["top_genres"]:
            bits.append("жанры: " + ", ".join(st["top_genres"]))
        return "; ".join(bits)

    async def _llm(self, messages: list[dict]) -> str:
        if not self.http:
            self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        headers = {"Content-Type": "application/json"}
        if self.s.ai_api_key:
            headers["Authorization"] = f"Bearer {self.s.ai_api_key}"
        if "openrouter" in (self.s.ai_base_url or ""):
            headers |= {"HTTP-Referer": self.s.public_url, "X-Title": "VibeCloud"}
        body = {"model": self.s.ai_model, "messages": messages, "temperature": 0.6, "max_tokens": 600}
        for attempt in range(2):
            async with self.http.post(f"{self.s.ai_base_url}/chat/completions", json=body, headers=headers) as r:
                if r.status == 429 and attempt == 0:
                    await asyncio.sleep(2)
                    continue
                data = await r.json(content_type=None)
                if r.status >= 400:
                    raise RuntimeError(f"AI {r.status}: {str(data)[:200]}")
                text = (data["choices"][0]["message"].get("content") or "").strip()
                # reasoning models may leak their thoughts in <think> tags
                return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
        raise RuntimeError("AI rate limited")

    def _faq(self, text: str) -> str | None:
        low = text.lower()
        for pattern, answer in FAQ:
            if re.search(pattern, low):
                return answer
        return None

    async def _search(self, uid: int, queries: list[str], per: int = 4) -> list[dict]:
        out, seen = [], set()
        for q in queries[:3]:
            try:
                raw = await self.sc.search(q, 10)
            except SoundCloudError as ex:
                log.info("ewa search %r: %s", q, ex)
                continue
            metas = [m for m in (track_meta(t) for t in raw) if m["available"] and m["id"] not in seen][:per]
            await self.db.upsert_tracks(metas)
            rows = await self.db.library_entries(uid, [m["id"] for m in metas])
            for m in metas:
                seen.add(m["id"])
                row = rows.get(m["id"])
                if row:
                    out.append({**track_out(row, uid, self.signer), "in_library": row.get("added_at") is not None})
        return out

    async def reply(self, uid: int, text: str, history: list[dict] | None = None) -> dict:
        text = (text or "").strip()[:800]
        if not text:
            return {"text": "Напиши, что ищешь — исполнителя, трек или настроение ✨", "tracks": [], "actions": []}
        if not self._allow(uid):
            return {"text": "Давай чуть передохнём — слишком много вопросов подряд. Через пару минут я снова тут 💜",
                    "tracks": [], "actions": []}
        answer, used_ai = None, False
        if self.s.ai_enabled:
            msgs = [{"role": "system", "content": SYSTEM.format(facts=APP_FACTS, about=await self._about(uid))}]
            for h in (history or [])[-10:]:
                role = "assistant" if h.get("role") in ("assistant", "ewa") else "user"
                content = str(h.get("text") or h.get("content") or "")[:800]
                if content:
                    msgs.append({"role": role, "content": content})
            msgs.append({"role": "user", "content": text})
            try:
                answer, used_ai = await self._llm(msgs), True
            except Exception as ex:  # noqa: BLE001
                log.warning("Ewa LLM failed, using FAQ: %s", ex)
        if not answer:
            answer = self._faq(text)
            if not answer and (MUSIC_HINT.search(text) or len(text.split()) <= 6):
                q = re.sub(r"(?i)\b(найди|найти|поищи|включи|поставь|хочу послушать|пожалуйста|мне|трек|песню|"
                           r"песня|ewa|эва)\b", " ", text)
                q = re.sub(r"\s+", " ", q).strip(" ,.!?") or text
                answer = f"Вот что нашла на SoundCloud по запросу «{q}» 👇 [[search: {q}]]"
            if not answer:
                answer = ("Я пока отвечаю на вопросы о VibeCloud и ищу музыку. Напиши исполнителя или название трека — "
                          "найду на SoundCloud.")
        queries, actions = [], []
        for kind, arg in TAG_RE.findall(answer):
            arg = arg.strip()
            if kind.lower() == "search":
                queries.append(arg)
            elif arg.lower() in VIEWS and arg.lower() not in [a["open"] for a in actions]:
                actions.append({"open": arg.lower()})
        clean = re.sub(r"\s{2,}", " ", TAG_RE.sub("", answer)).strip()
        tracks = await self._search(uid, queries) if queries else []
        if queries and not tracks:
            clean += "\n\nХм, на SoundCloud ничего подходящего не нашлось — попробуй уточнить запрос."
        return {"text": clean, "tracks": tracks, "actions": actions, "ai": used_ai}
