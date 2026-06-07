from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.utils.ids import new_correlation_id, new_event_id


class BaseEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    event_id: str = Field(default_factory=new_event_id)
    correlation_id: str = Field(default_factory=new_correlation_id)
    timestamp: datetime | None = None
    origin: str | None = None
    chat_id: int | str | None = None
    payload: Any
    schema_version: str = "v1"

    def __str__(self) -> str:
        return f"{self.name}(event_id={self.event_id}, correlation_id={self.correlation_id}, chat_id={self.chat_id})"

