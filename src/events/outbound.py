from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.events.base import BaseEvent


class OutboundMessagePreparedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chat_id: int | str
    session_id: str | None = None
    trigger_message_id: int | None = None
    ordered_messages: list[str] = Field(default_factory=list)
    reply_to_message_id: int | None = None
    mood: str
    raw_reply_text: str = ""
    no_send: bool = False
    frame_created_at: datetime | None = None
    visible_read_not_before: datetime | None = None
    visible_surfaced_message_ids: list[int] = Field(default_factory=list)
    visible_surfaced_until_message_id: int | None = None
    visible_read_through_message_id: int | None = None


class OutboundMessagePreparedEvent(BaseEvent):
    name: str = "OutboundMessagePreparedEvent"
    payload: OutboundMessagePreparedPayload
