from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.events.base import BaseEvent


class LinearTaskPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_id: str
    identifier: str
    title: str
    description_preview: str | None = None
    url: str | None = None
    due_date: str | None = None
    priority: int | None = None
    updated_at: str | None = None
    status: str | None = None
    project: str | None = None
    milestone: str | None = None
    cycle: str | None = None
    labels: list[str] = Field(default_factory=list)
    codex_thread_id: str | None = None
    codex_turn_id: str | None = None
    pr_url: str | None = None
    pr_number: int | None = None
    pr_status: str | None = None
    pr_repository: str | None = None
    pr_branch: str | None = None
    pr_title: str | None = None


class LinearTaskListPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tasks: list[LinearTaskPayload] = Field(default_factory=list)
    generated_at: datetime
    window_start_date: str
    window_end_date: str
    queue_hash: str


class LinearTaskListReceivedEvent(BaseEvent):
    name: str = "LinearTaskListReceivedEvent"
    payload: LinearTaskListPayload


class LinearQueueWakeRequestedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str
    issue_id: str | None = None
    requested_at: datetime


class LinearQueueWakeRequestedEvent(BaseEvent):
    name: str = "LinearQueueWakeRequestedEvent"
    payload: LinearQueueWakeRequestedPayload
