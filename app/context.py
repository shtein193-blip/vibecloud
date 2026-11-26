from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from .assistant import Ewa
from .config import Settings
from .db import DB
from .downloader import Downloader
from .events import EventBus
from .importer import Importer
from .listimport import ListImporter
from .security import Signer
from .soundcloud import SoundCloud
from .tasks import TTLCache

if TYPE_CHECKING:
    from aiogram import Bot

    from .bot.sender import TelegramIO


@dataclass
class Ctx:
    settings: Settings
    db: DB
    bus: EventBus
    signer: Signer
    sc: SoundCloud
    dl: Downloader
    importer: Importer
    lists: ListImporter
    ewa: Ewa
    bot: "Bot | None" = None
    tg: "TelegramIO | None" = None
    bot_username: str = "VibeCloudBot"
    # short-lived choices from bot buttons (profile import mode, track lists) — bounded, expire in 1 h
    pending_links: TTLCache = field(default_factory=lambda: TTLCache(maxsize=500, ttl=3600))
    ready: bool = True

    def is_admin(self, uid: int) -> bool:
        return uid in self.settings.admin_ids

    def app_link(self, payload: str) -> str:
        """Public deep link that opens the Mini App (works in any chat)."""
        if self.settings.webapp_short_name:
            return f"https://t.me/{self.bot_username}/{self.settings.webapp_short_name}?startapp={payload}"
        return f"https://t.me/{self.bot_username}?start={payload}"

    def webapp_url(self, **params: Any) -> str:
        """URL for WebAppInfo buttons (private chat with the bot)."""
        q = urlencode({k: v for k, v in params.items() if v is not None})
        return f"{self.settings.public_url}/" + (f"?{q}" if q else "")
