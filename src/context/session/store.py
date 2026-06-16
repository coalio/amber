from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from src.events.attention import AttentionClassificationPayload, MemoryCardPayload
from src.events.context import ContextFrameMessagePayload


@dataclass
class ConversationSession:
    session_id: str
    chat_id: int | str
    last_updated_at: datetime
    recent_messages: list[ContextFrameMessagePayload] = field(default_factory=list)
    compacted_facts: list[str] = field(default_factory=list)
    open_loops: list[str] = field(default_factory=list)
    participant_names: dict[str, str] = field(default_factory=dict)
    engaged_user_ids: set[str] = field(default_factory=set)
    memory_cards: dict[str, MemoryCardPayload] = field(default_factory=dict)
    attention_classification: AttentionClassificationPayload | None = None
    topic_summary: str = "No stable topic yet."
    recommended_reply_candidate: int | None = None
    response_required: bool = False
    response_required_reason: str | None = None
    expanded_memory_ids: set[str] = field(default_factory=set)
    latest_trigger_message_id: int | None = None
    pending_surfaced_messages: dict[int, ContextFrameMessagePayload] = field(default_factory=dict)
    pending_first_surfaced_at: datetime | None = None
    pending_latest_surfaced_at: datetime | None = None
    engagement_pending_since: datetime | None = None
    engagement_delay_until: datetime | None = None
    engagement_committed: bool = False
    read_cooldown_until: datetime | None = None
    idle_expire_at: datetime | None = None
    frame_in_flight: bool = False
    pending_read_through_message_id: int | None = None
    typing_until_by_sender: dict[str, datetime] = field(default_factory=dict)
