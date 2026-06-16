from __future__ import annotations

from dataclasses import dataclass

from src.config.config import Settings


@dataclass(frozen=True)
class LinearReceiverConfig:
    enabled: bool
    api_key: str | None
    api_url: str
    poll_seconds: float
    due_window_days: int
    ready_to_start_statuses: tuple[str, ...]
    terminal_statuses: tuple[str, ...]

    @classmethod
    def from_settings(cls, settings: Settings) -> "LinearReceiverConfig":
        return cls(
            enabled=settings.linear_enabled,
            api_key=settings.linear_api_key,
            api_url=settings.linear_api_url,
            poll_seconds=settings.linear_poll_seconds,
            due_window_days=settings.linear_due_window_days,
            ready_to_start_statuses=settings.linear_issue_ready_to_start_statuses,
            terminal_statuses=(
                *settings.linear_issue_statuses.get("completed", ()),
                *settings.linear_issue_statuses.get("canceled", ()),
            ),
        )
