from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict


class TaskOrigin(BaseModel):
    """Trusted, opaque delivery route; never part of model-authored task context."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    delivery_route: str


@dataclass(frozen=True)
class ToolInvocation:
    chat_id: int | str
    trigger_message_id: int
    reply_to_message_id: int | None
    source: str
    task_origin: TaskOrigin | None = None


@dataclass(frozen=True)
class WorkReceipt:
    chat_id: int | str
    trigger_message_id: int
    reply_to_message_id: int | None
    message: str = "on it"
