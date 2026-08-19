from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SemanticDecisionSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["ignore", "reply", "sleep", "expand_memory", "disengage"]
    work_intent: Literal["none", "answer", "delegate"] = "none"
    codex_work_dispatched: bool = False
    codex_task_started: bool = False
    reply_to_message_id: int | None = None
    chat_id: int | str
    reply_text: str | None = None
    referenced_memory_ids: list[str] = Field(default_factory=list)
    confidence: float
    notes: list[str] = Field(default_factory=list)
    trigger_message_id: int | None = None
    session_id: str | None = None
    disengage_sender_id: str | None = None
    disengage_reason: str | None = None
    ignore_for_seconds: int | None = Field(default=None, ge=0)
    create_bad_memory: bool = False
    bad_memory_sender_id: str | None = None
    bad_memory_text: str | None = None
    memory_mutation: Literal["none", "rewrite", "forget"] = "none"
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


class InterruptionMessageSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: int
    sender_id: str
    sender_name: str
    is_self: bool = False
    content: str
    reply_to_message_id: int | None = None
    reply_to_sender_id: str | None = None
    reply_to_sender_name: str | None = None


class InterruptionRequestSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chat_id: int | str
    session_id: str | None = None
    trigger_message_id: int | None = None
    reply_to_message_id: int | None = None
    reply_target_sender_id: str
    reply_target_sender_name: str | None = None
    current_chunk_index: int
    chunk_count: int
    sent_reply_chunks: list[str] = Field(default_factory=list)
    remaining_reply_chunks: list[str] = Field(default_factory=list)
    conversation_window_messages: list[InterruptionMessageSchema] = Field(default_factory=list)
    interrupting_message: InterruptionMessageSchema


class InterruptionDecisionSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interrupt_decision: Literal["accept", "decline"]
    action: Literal["ignore", "reply", "sleep", "expand_memory", "disengage"]
    work_intent: Literal["none", "answer", "delegate"] = "none"
    codex_work_dispatched: bool = False
    codex_task_started: bool = False
    codex_app_server_id: str | None = None
    codex_task_id: str | None = None
    reply_to_message_id: int | None = None
    reply_text: str | None = None
    referenced_memory_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    reason: str
    notes: list[str] = Field(default_factory=list)
    disengage_sender_id: str | None = None
    disengage_reason: str | None = None
    ignore_for_seconds: int | None = Field(default=None, ge=0)
    create_bad_memory: bool = False
    bad_memory_sender_id: str | None = None
    bad_memory_text: str | None = None
    memory_mutation: Literal["none", "rewrite", "forget"] = "none"
    target_memory_id: str | None = None
    target_memory_sender_id: str | None = None
    rewritten_memory_text: str | None = None
    rewritten_memory_tags: list[str] = Field(default_factory=list)
