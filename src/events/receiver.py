from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.events.base import BaseEvent


class TelegramSenderPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    username: str | None = None
    is_self: bool = False


class TelegramReplySenderPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    name: str | None = None


class TelegramAttachmentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_type: str | None = None
    file_id: str | None = None
    file_name: str | None = None
    mime_type: str | None = None
    sticker_set_id: str | None = None


class TelegramTransportPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    peer_id: int | str
    raw_chat_id: int | str
    raw_message_id: int
    thread_id: int | None = None


class TelegramMessagePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: int
    chat_id: int | str
    thread_id: int | None = None
    sender: TelegramSenderPayload
    timestamp: datetime
    content: str
    raw_text: str | None = None
    reply_to_message_id: int | None = None
    reply_to_sender: TelegramReplySenderPayload = Field(default_factory=TelegramReplySenderPayload)
    reply_to_content: str | None = None
    reply_to_raw_text: str | None = None
    mentions: list[str] = Field(default_factory=list)
    attachment: TelegramAttachmentPayload = Field(default_factory=TelegramAttachmentPayload)
    transport: TelegramTransportPayload
    edited_at: datetime | None = None
    reaction_count: int = 0


class TelegramMessageReceivedEvent(BaseEvent):
    name: str = "TelegramMessageReceivedEvent"
    payload: TelegramMessagePayload


class TelegramTypingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chat_id: int | str
    sender: TelegramSenderPayload
    timestamp: datetime
    active: bool
    activity: str | None = None
    expires_at: datetime | None = None


class TelegramTypingUpdatedEvent(BaseEvent):
    name: str = "TelegramTypingUpdatedEvent"
    payload: TelegramTypingPayload
