from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.events.base import BaseEvent
from src.events.receiver import TelegramMessagePayload


class MemoryCardPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str
    owner_sender_id: str | None = None
    owner_sender_name: str | None = None
    text: str
    tags: list[str] = Field(default_factory=list)
    salience: float = 0.5
    confidence: float = 0.5
    created_at: datetime | None = None
    updated_at: datetime | None = None


class StickerSignalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sticker_file_id: str | None = None
    sticker_set_id: str | None = None
    inferred_tones: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    preceding_segment: list[str] = Field(default_factory=list)


class AttentionClassificationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: float
    labels: dict[str, float] = Field(default_factory=dict)
    top_labels: list[str] = Field(default_factory=list)
    tone: str
    model_name: str
    model_revision: str | None = None


class AttentionDecisionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str
    message: TelegramMessagePayload
    attention_score: float
    heuristic_score: float
    model_score: float
    reasons: list[str] = Field(default_factory=list)
    memory_cards: list[MemoryCardPayload] = Field(default_factory=list)
    sticker_signal: StickerSignalPayload | None = None
    classification: AttentionClassificationPayload | None = None
    engaged_user_bypass: bool = False
    reply_target_candidate: int | None = None


class AttentionDecisionMadeEvent(BaseEvent):
    name: str = "AttentionDecisionMadeEvent"
    payload: AttentionDecisionPayload
