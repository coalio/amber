from __future__ import annotations

from dataclasses import dataclass

from src.config.config import Settings


@dataclass(frozen=True)
class AIConfig:
    semantic_retry_budget: int
    max_draft_chars: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "AIConfig":
        return cls(
            semantic_retry_budget=settings.ai_semantic_retry_budget,
            max_draft_chars=settings.ai_max_draft_chars,
        )

