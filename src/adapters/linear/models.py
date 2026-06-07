from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class LinearWorkflowState:
    id: str
    name: str
    type: str | None = None


@dataclass(frozen=True)
class LinearIssue:
    id: str
    identifier: str
    title: str
    description: str | None = None
    url: str | None = None
    due_date: date | None = None
    priority: int | None = None
    updated_at: datetime | None = None
    team_id: str | None = None
    team_key: str | None = None
    team_name: str | None = None
    state: LinearWorkflowState | None = None
    project: str | None = None
    milestone: str | None = None
    cycle: str | None = None
    labels: list[str] = field(default_factory=list)
    assignee_name: str | None = None
    creator_name: str | None = None

    @property
    def is_terminal(self) -> bool:
        state_type = (self.state.type if self.state is not None else None) or ""
        return state_type.lower() in {"completed", "canceled", "cancelled"}

    def description_preview(self, *, limit: int = 1200) -> str | None:
        if not self.description:
            return None
        compact = " ".join(self.description.split())
        if len(compact) <= limit:
            return compact
        return f"{compact[: limit - 3]}..."

    def to_queue_payload(self) -> dict[str, object | None]:
        return {
            "issue_id": self.id,
            "identifier": self.identifier,
            "title": self.title,
            "description_preview": self.description_preview(),
            "url": self.url,
            "due_date": self.due_date.isoformat() if self.due_date is not None else None,
            "priority": self.priority,
            "updated_at": self.updated_at.isoformat() if self.updated_at is not None else None,
            "team_id": self.team_id,
            "team_key": self.team_key,
            "team_name": self.team_name,
            "status": self.state.name if self.state is not None else None,
            "status_type": self.state.type if self.state is not None else None,
            "project": self.project,
            "milestone": self.milestone,
            "cycle": self.cycle,
            "labels": list(self.labels),
            "assignee_name": self.assignee_name,
            "creator_name": self.creator_name,
        }
