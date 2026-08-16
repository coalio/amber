from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.events.base import BaseEvent


class SemanticDecisionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    work_intent: str = "none"
    codex_task_started: bool = False
    reply_to_message_id: int | None = None
    chat_id: int | str
    reply_text: str | None = None
    referenced_memory_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    notes: list[str] = Field(default_factory=list)
    trigger_message_id: int | None = None
    session_id: str | None = None
    disengage_sender_id: str | None = None
    disengage_reason: str | None = None
    ignore_for_seconds: int | None = None
    create_bad_memory: bool = False
    bad_memory_sender_id: str | None = None
    bad_memory_text: str | None = None
    memory_mutation: str = "none"
    target_memory_id: str | None = None
    target_memory_sender_id: str | None = None
    rewritten_memory_text: str | None = None
    rewritten_memory_tags: list[str] = Field(default_factory=list)
    frame_created_at: datetime | None = None
    visible_read_not_before: datetime | None = None
    visible_surfaced_message_ids: list[int] = Field(default_factory=list)
    visible_surfaced_until_message_id: int | None = None
    visible_read_through_message_id: int | None = None
    codex_target_sender_id: str | None = None
    codex_target_sender_name: str | None = None
    codex_app_server_id: str | None = None
    codex_task_id: str | None = None
    codex_tool_call_id: str | None = None


class SemanticDecisionMadeEvent(BaseEvent):
    name: str = "SemanticDecisionMadeEvent"
    payload: SemanticDecisionPayload
