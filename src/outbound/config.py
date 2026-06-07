from __future__ import annotations

from dataclasses import dataclass

from src.config.config import Settings


@dataclass(frozen=True)
class OutboundPreparationConfig:
    max_chunk_chars: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "OutboundPreparationConfig":
        return cls(max_chunk_chars=settings.outbound_max_chunk_chars)

