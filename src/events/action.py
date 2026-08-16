from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.events.base import BaseEvent


class MessageReadPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chat_id: int | str
    session_id: str | None = None
    trigger_message_id: int | None = None
    surfaced_message_ids: list[int] = Field(default_factory=list)
    surfaced_until_message_id: int | None = None
    read_through_message_id: int
    mark_seen: bool = True
    visible_not_before: datetime | None = None


class MessageReadEvent(BaseEvent):
    name: str = "MessageReadEvent"
    payload: MessageReadPayload


class OutboundDeliveryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chat_id: int | str
    reply_to_message_id: int | None = None
    ordered_messages: list[str] = Field(default_factory=list)
    sent_message_ids: list[int] = Field(default_factory=list)
    planned_message_count: int | None = None
    interrupted: bool = False
    interruption_message_id: int | None = None
    no_send: bool = False
    delivered_at: datetime | None = None
    session_id: str | None = None
    trigger_message_id: int | None = None
    codex_app_server_id: str | None = None
    codex_task_id: str | None = None


class OutboundMessageSentEvent(BaseEvent):
    name: str = "OutboundMessageSentEvent"
    payload: OutboundDeliveryPayload


class OutboundChunkPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chat_id: int | str
    session_id: str | None = None
    trigger_message_id: int | None = None
    reply_to_message_id: int | None = None
    chunk_index: int
    chunk_count: int
    message_text: str
    sent_message_id: int
    typing_duration_seconds: float


class OutboundChunkSentEvent(BaseEvent):
    name: str = "OutboundChunkSentEvent"
    payload: OutboundChunkPayload


class SleepStateChangedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sleep_state: str
    changed_at: datetime
    scheduled_wake_at: datetime | None = None


class SleepStateChangedEvent(BaseEvent):
    name: str = "SleepStateChangedEvent"
    payload: SleepStateChangedPayload


class PresenceStateChangedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    online: bool
    changed_at: datetime
    reason: str
    session_id: str | None = None
    trigger_message_id: int | None = None


class PresenceStateChangedEvent(BaseEvent):
    name: str = "PresenceStateChangedEvent"
    payload: PresenceStateChangedPayload
