from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.config.config import Settings


@dataclass(frozen=True)
class TelegramReceiverConfig:
    api_id: str | None
    api_hash: str | None
    session_path: Path

    @classmethod
    def from_settings(cls, settings: Settings) -> "TelegramReceiverConfig":
        return cls(
            api_id=settings.telegram_api_id,
            api_hash=settings.telegram_api_hash,
            session_path=settings.telegram_session_path,
        )
