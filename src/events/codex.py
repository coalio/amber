from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.events.base import BaseEvent


CodexNotificationKind = Literal["milestone", "completion", "blocked", "failed"]


class CodexCandidatePersonPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sender_id: str
    chat_id: int | str
    display_name: str
    known_aliases: list[str] = Field(default_factory=list)
    expertise_tags: list[str] = Field(default_factory=list)
    project_owner_tags: list[str] = Field(default_factory=list)


class CodexQuestionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_server_id: str
    task_id: str
    tool_call_id: str
    questions: list[str]
    task_description: str
    context: dict[str, Any] = Field(default_factory=dict)
    candidate_people: list[CodexCandidatePersonPayload] = Field(default_factory=list)
    created_at: datetime


class CodexQuestionReceivedEvent(BaseEvent):
    name: str = "CodexQuestionReceivedEvent"
    payload: CodexQuestionPayload


class CodexNotificationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_server_id: str
    task_id: str
    notification_id: str
    notification_kind: CodexNotificationKind
    message: str
    task_description: str
    context: dict[str, Any] = Field(default_factory=dict)
    candidate_people: list[CodexCandidatePersonPayload] = Field(default_factory=list)
    created_at: datetime


class CodexNotificationReceivedEvent(BaseEvent):
    name: str = "CodexNotificationReceivedEvent"
    payload: CodexNotificationPayload
