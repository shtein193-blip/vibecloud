from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader (no extra dependency)."""
    p = Path(path)
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _int(name: str, default: int | None = None) -> int | None:
    value = os.getenv(name, "").strip()
    return int(value) if value else default


def _chat_id(name: str) -> int | None:
    """Accepts '-1001234567890', '#-1001234567890' (copied from Telegram Web) or '1234567890'."""
    raw = os.getenv(name, "").strip().strip("'\"").lstrip("#").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        raise SystemExit(f"{name}={raw!r}: нужен числовой ID канала вида -1001234567890, а не @username")
    return value if value < 0 else int(f"-100{value}")


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name, "").strip().lower()
    return default if not value else value in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    bot_token: str
    public_url: str
    webapp_short_name: str | None
    storage_chat_id: int | None
    host: str
    port: int
    data_dir: Path
    cache_max_bytes: int
    download_workers: int
    prefetch_per_import: int
    max_import_tracks: int
    sync_interval_min: int
    sc_client_id: str | None
    telegram_api_url: str | None
    dev_user_id: int | None
    lite_mode: bool
    mp3_max_kbps: int
    db_backup_min: int
    admin_ids: frozenset[int]
    ai_base_url: str | None
    ai_api_key: str | None
    ai_model: str | None
    list_import_max: int
    ytdlp_autoupdate: bool
    on_render: bool
    secret: bytes = field(repr=False)

    @property
    def ai_enabled(self) -> bool:
        return bool(self.ai_base_url and self.ai_model and (self.ai_api_key or "localhost" in self.ai_base_url))

    @property
    def max_upload_bytes(self) -> int:
        # Bot API: 50 MB. Local Bot API server: up to 2000 MB.
        return (1900 if self.telegram_api_url else 49) * 1024 * 1024

    @property
    def bot_enabled(self) -> bool:
        return bool(self.bot_token)

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "vibecloud.db"


# Бесплатные OpenAI-совместимые провайдеры для ассистента Ewa (ключ без карты).
AI_PRESETS = {
    "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.5-flash"),
    "openrouter": ("https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct:free"),
    "cerebras": ("https://api.cerebras.ai/v1", "llama-3.3-70b"),
}


def _ids(name: str) -> frozenset[int]:
    out = set()
    for part in os.getenv(name, "").replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.add(int(part))
    return frozenset(out)


def load_settings() -> Settings:
    _load_dotenv()
    token = os.getenv("BOT_TOKEN", "").strip()
    data_dir = Path(os.getenv("DATA_DIR", "./data")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "audio").mkdir(exist_ok=True)
    secret_seed = token or os.getenv("APP_SECRET", "vibecloud-dev-secret")
    # LITE_MODE: Telegram-first. Player streams straight from SoundCloud's CDN, files live in Telegram
    # (file_id), the server keeps only a tiny temporary cache and downloads nothing in advance.
    lite = _bool("LITE_MODE", True)
    # провайдер ассистента: явный AI_PROVIDER, иначе groq, если задан только ключ
    ai_provider = (os.getenv("AI_PROVIDER") or ("groq" if os.getenv("AI_API_KEY") else "")).strip().lower()
    return Settings(
        bot_token=token,
        # RENDER_EXTERNAL_URL is set automatically on Render (https://<service>.onrender.com)
        public_url=(os.getenv("PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL")
                    or f"http://localhost:{os.getenv('PORT') or 8080}").rstrip("/"),
        webapp_short_name=os.getenv("WEBAPP_SHORT_NAME") or None,
        storage_chat_id=_chat_id("STORAGE_CHAT_ID"),
        host=os.getenv("HOST", "0.0.0.0"),
        port=_int("PORT", 8080),
        data_dir=data_dir,
        cache_max_bytes=int(float(os.getenv("CACHE_MAX_GB") or ("1" if lite else "10")) * 1024**3),
        download_workers=_int("DOWNLOAD_WORKERS", 2 if lite else 3),
        prefetch_per_import=_int("PREFETCH_PER_IMPORT", 0 if lite else 25),
        max_import_tracks=_int("MAX_IMPORT_TRACKS", 500),
        sync_interval_min=_int("SYNC_INTERVAL_MIN", 180),
        sc_client_id=os.getenv("SC_CLIENT_ID") or None,
        telegram_api_url=os.getenv("TELEGRAM_API_URL") or None,
        dev_user_id=_int("DEV_USER_ID"),
        lite_mode=lite,
        # SoundCloud masters are AAC 160 / MP3 128 — encoding above ~192 only inflates the file
        mp3_max_kbps=_int("MP3_MAX_KBPS", 192),
        # opt-in: keep a copy of the SQLite DB in the storage channel (for ephemeral disks like Render free)
        # на Render диск стирается при перезапуске/деплое — там бэкап базы включён по умолчанию
        db_backup_min=_int("DB_BACKUP_MIN", 2 if os.getenv("RENDER") else 0),
        admin_ids=_ids("ADMIN_IDS"),
        ai_base_url=(os.getenv("AI_BASE_URL") or AI_PRESETS.get(ai_provider, (None, None))[0] or "").rstrip("/") or None,
        ai_api_key=os.getenv("AI_API_KEY") or None,
        ai_model=os.getenv("AI_MODEL") or AI_PRESETS.get(ai_provider, (None, None))[1],
        list_import_max=_int("LIST_IMPORT_MAX", 1500),
        ytdlp_autoupdate=_bool("YTDLP_AUTOUPDATE", bool(os.getenv("RENDER"))),
        on_render=bool(os.getenv("RENDER")),
        secret=hashlib.sha256(b"vibecloud:" + secret_seed.encode()).digest(),
    )
