from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.events.attention import AttentionClassificationPayload, MemoryCardPayload
from src.events.base import BaseEvent
from src.events.codex import CodexCandidatePersonPayload
from src.events.linear import LinearTaskPayload


class ContextFrameMessagePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: int
    sender_id: str
    sender_name: str
    is_self: bool = False
    content: str
    timestamp: datetime
    reply_to_message_id: int | None = None
    reply_to_sender_id: str | None = None
    reply_to_sender_name: str | None = None
    reply_to_content: str | None = None
    reply_to_raw_text: str | None = None
    source: str = "surface"


class PendingInterruptionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original_trigger_message_id: int | None = None
    original_reply_to_message_id: int | None = None
    interrupting_message_id: int
    reply_target_sender_id: str
    reply_target_sender_name: str | None = None
    sent_reply_chunks: list[str] = Field(default_factory=list)
    remaining_reply_chunks: list[str] = Field(default_factory=list)


class OpenQuestionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_server_id: str
    task_id: str
    tool_call_id: str
    questions: list[str] = Field(default_factory=list)
    task_description: str
    context: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    candidate_people: list[CodexCandidatePersonPayload] = Field(default_factory=list)
    selected_sender_id: str | None = None
    selected_sender_name: str | None = None
    user_replies: list[str] = Field(default_factory=list)


class CodexNotificationFramePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_server_id: str
    task_id: str
    notification_id: str
    message: str
    task_description: str
    context: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    candidate_people: list[CodexCandidatePersonPayload] = Field(default_factory=list)


class LinearTaskListFramePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tasks: list[LinearTaskPayload] = Field(default_factory=list)
    generated_at: datetime
    window_start_date: str
    window_end_date: str
    queue_hash: str


class ContextFramePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    chat_id: int | str
    trigger_message_id: int
    current_message: ContextFrameMessagePayload
    recent_messages: list[ContextFrameMessagePayload]
    conversation_window_messages: list[ContextFrameMessagePayload] = Field(default_factory=list)
    topic_summary: str
    open_loops: list[str] = Field(default_factory=list)
    participants: list[str] = Field(default_factory=list)
    relevant_memories: list[MemoryCardPayload] = Field(default_factory=list)
    attention_classification: AttentionClassificationPayload | None = None
    mood: str
    fatigue_notice: str | None = None
    recommended_reply_candidate: int | None = None
    response_required: bool = False
    response_required_reason: str | None = None
    engaged_user_ids: list[str] = Field(default_factory=list)
    compacted_facts: list[str] = Field(default_factory=list)
    expanded_memory_ids: list[str] = Field(default_factory=list)
    pending_interruption: PendingInterruptionPayload | None = None
    open_question: OpenQuestionPayload | None = None
    open_questions: list[OpenQuestionPayload] = Field(default_factory=list)
    codex_notification: CodexNotificationFramePayload | None = None
    linear_task_list: LinearTaskListFramePayload | None = None
    frame_created_at: datetime | None = None
    visible_read_not_before: datetime | None = None
    visible_surfaced_message_ids: list[int] = Field(default_factory=list)
    visible_surfaced_until_message_id: int | None = None
    visible_read_through_message_id: int | None = None


class ContextFrameReadyEvent(BaseEvent):
    name: str = "ContextFrameReadyEvent"
    payload: ContextFramePayload
