from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ConversationIgnoreRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chat_id: int | str
    sender_id: str
    sender_name: str | None = None
    created_at: datetime
    ignore_until: datetime | None = None
    reason: str | None = None


class PendingInterruption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chat_id: int | str
    session_id: str | None = None
    original_trigger_message_id: int | None = None
    original_reply_to_message_id: int | None = None
    interrupting_message_id: int
    reply_target_sender_id: str
    reply_target_sender_name: str | None = None
    sent_reply_chunks: list[str] = Field(default_factory=list)
    remaining_reply_chunks: list[str] = Field(default_factory=list)
    created_at: datetime


class OpenQuestionCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sender_id: str
    chat_id: int | str
    display_name: str
    known_aliases: list[str] = Field(default_factory=list)
    expertise_tags: list[str] = Field(default_factory=list)
    project_owner_tags: list[str] = Field(default_factory=list)


class OpenQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chat_id: int | str
    sender_id: str
    sender_name: str
    app_server_id: str
    task_id: str
    tool_call_id: str
    questions: list[str] = Field(default_factory=list)
    task_description: str
    context: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    candidate_people: list[OpenQuestionCandidate] = Field(default_factory=list)
    user_replies: list[str] = Field(default_factory=list)
    user_reply_message_ids: list[int] = Field(default_factory=list)
    created_at: datetime
    expires_at: datetime
    status: str = "open"


class CodexOutboundMessageLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chat_id: int | str
    message_id: int


class CodexTaskLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_server_id: str
    task_id: str
    thread_id: str | None = None
    turn_id: str | None = None
    status: str = "running"
    outbound_messages: list[CodexOutboundMessageLink] = Field(default_factory=list)
    updated_at: datetime


class LinearQueuedTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_id: str
    identifier: str
    title: str
    description_preview: str | None = None
    url: str | None = None
    due_date: str | None = None
    priority: int | None = None
    updated_at: str | None = None
    team_id: str | None = None
    team_key: str | None = None
    team_name: str | None = None
    status: str | None = None
    status_type: str | None = None
    project: str | None = None
    milestone: str | None = None
    cycle: str | None = None
    labels: list[str] = Field(default_factory=list)
    assignee_name: str | None = None
    creator_name: str | None = None
    queue_status: str = "available"
    codex_app_server_id: str | None = None
    codex_task_id: str | None = None
    codex_thread_id: str | None = None
    codex_turn_id: str | None = None
    pr_url: str | None = None
    pr_number: int | None = None
    pr_status: str | None = None
    pr_repository: str | None = None
    pr_branch: str | None = None
    pr_title: str | None = None
    pr_summary: str | None = None
    pr_merged_at: datetime | None = None
    last_error: str | None = None
    selected_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_seen_at: datetime

    def ai_payload(self) -> dict[str, object | None]:
        return {
            "issue_id": self.issue_id,
            "identifier": self.identifier,
            "title": self.title,
            "description_preview": self.description_preview,
            "url": self.url,
            "due_date": self.due_date,
            "priority": self.priority,
            "status": self.status,
            "project": self.project,
            "milestone": self.milestone,
            "cycle": self.cycle,
            "labels": list(self.labels),
            "updated_at": self.updated_at,
            "codex_thread_id": self.codex_thread_id,
            "codex_turn_id": self.codex_turn_id,
            "pr_url": self.pr_url,
            "pr_number": self.pr_number,
            "pr_status": self.pr_status,
            "pr_repository": self.pr_repository,
            "pr_branch": self.pr_branch,
            "pr_title": self.pr_title,
        }


class GlobalState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mood: str = "calm"
    sleep_state: str = "awake"
    woke_at: datetime
    slept_at: datetime | None = None
    energy_level: float = 16.0
    active_chat_id: int | str | None = None
    active_session_id: str | None = None
    pending_chat_id: int | str | None = None
    pending_session_id: str | None = None
    last_engagement_at: datetime | None = None
    fatigue_alert_active: bool = False
    pending_sleep_window: dict[str, str | float | None] = Field(default_factory=dict)
    scheduled_wake_at: datetime | None = None
    conversation_engaged_user_ids: list[str] = Field(default_factory=list)
    pending_engaged_user_ids: list[str] = Field(default_factory=list)
    seen_through_by_chat: dict[str, int] = Field(default_factory=dict)
    conversation_ignore_rules: dict[str, ConversationIgnoreRule] = Field(default_factory=dict)
    delivery_state: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    recent_self_message_ids: list[int] = Field(default_factory=list)
    pending_interruption: PendingInterruption | None = None
    open_questions: dict[str, OpenQuestion] = Field(default_factory=dict)
    codex_tasks: dict[str, CodexTaskLink] = Field(default_factory=dict)
    linear_tasks: dict[str, LinearQueuedTask] = Field(default_factory=dict)
    linear_last_emitted_queue_hash: str | None = None
    linear_last_poll_at: datetime | None = None
