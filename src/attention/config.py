from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from src.config.config import Settings


@dataclass(frozen=True)
class AttentionConfig:
    surface_threshold: float
    urgent_threshold: float
    memory_limit: int
    disable_sleep_state: bool
    mode: Literal["casual", "work"] = "casual"
    always_surface_telegram_ids: frozenset[str] = frozenset()
    sentiment_multiplier: float = 0.75

    @classmethod
    def from_settings(cls, settings: Settings) -> "AttentionConfig":
        return cls(
            surface_threshold=settings.attention_surface_threshold,
            urgent_threshold=settings.attention_urgent_threshold,
            memory_limit=settings.attention_memory_limit,
            disable_sleep_state=settings.disable_sleep_state,
            mode=settings.mode,
            always_surface_telegram_ids=_normalize_sender_ids(settings.always_surface_telegram_ids),
        )


def _normalize_sender_ids(sender_ids: Iterable[str]) -> frozenset[str]:
    return frozenset(str(sender_id).removeprefix("user") for sender_id in sender_ids if str(sender_id).strip())
