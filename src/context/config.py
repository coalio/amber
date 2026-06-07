from __future__ import annotations

from dataclasses import dataclass

from src.config.config import Settings


@dataclass(frozen=True)
class ContextConfig:
    debounce_seconds: float
    idle_timeout_seconds: float
    competing_chat_timeout_seconds: float
    recent_message_budget: int
    max_compacted_facts: int
    disable_sleep_state: bool
    initial_engagement_delay_min_seconds: float = 2.0
    initial_engagement_delay_max_seconds: float = 10.0
    conversation_window_before: int = 15
    conversation_window_after: int = 15
    idle_timeout_min_seconds: float | None = None
    idle_timeout_max_seconds: float | None = None

    def idle_timeout_bounds(self) -> tuple[float, float]:
        minimum = self.idle_timeout_min_seconds if self.idle_timeout_min_seconds is not None else self.idle_timeout_seconds
        maximum = self.idle_timeout_max_seconds if self.idle_timeout_max_seconds is not None else self.idle_timeout_seconds
        minimum = max(minimum, 0.0)
        maximum = max(maximum, minimum)
        return minimum, maximum

    @classmethod
    def from_settings(cls, settings: Settings) -> "ContextConfig":
        initial_delay_min = max(settings.context_initial_engagement_delay_min_seconds, 0.0)
        initial_delay_max = max(settings.context_initial_engagement_delay_max_seconds, initial_delay_min)
        idle_timeout_seconds = (
            settings.context_idle_timeout_seconds
            if settings.context_idle_timeout_seconds is not None
            else settings.context_idle_timeout_max_seconds
        )
        return cls(
            debounce_seconds=max(min(settings.context_debounce_seconds, 5.0), 0.0),
            idle_timeout_seconds=idle_timeout_seconds,
            competing_chat_timeout_seconds=settings.context_competing_chat_timeout_seconds,
            recent_message_budget=settings.context_recent_message_budget,
            max_compacted_facts=settings.context_max_compacted_facts,
            disable_sleep_state=settings.disable_sleep_state,
            initial_engagement_delay_min_seconds=initial_delay_min,
            initial_engagement_delay_max_seconds=initial_delay_max,
            conversation_window_before=settings.context_conversation_window_before,
            conversation_window_after=settings.context_conversation_window_after,
            idle_timeout_min_seconds=settings.context_idle_timeout_min_seconds,
            idle_timeout_max_seconds=settings.context_idle_timeout_max_seconds,
        )
