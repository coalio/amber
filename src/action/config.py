from __future__ import annotations

from dataclasses import dataclass

from src.config.config import Settings


@dataclass(frozen=True)
class ActionConfig:
    enable_real_delays: bool
    disable_sleep_state: bool
    transport_max_retries: int
    transport_retry_delay_seconds: float
    typing_baseline_wpm: float = 135.0
    filler_pause_seconds: float = 5.0
    inter_chunk_delay_min_seconds: float = 0.5
    inter_chunk_delay_max_seconds: float = 1.0
    inter_chunk_delay_length_threshold_chars: int = 50
    inter_chunk_delay_chars_per_step: int = 5
    inter_chunk_delay_step_seconds: float = 0.5
    inter_chunk_delay_total_max_seconds: float = 5.0

    @classmethod
    def from_settings(cls, settings: Settings) -> "ActionConfig":
        inter_chunk_delay_min_seconds = max(settings.action_inter_chunk_delay_min_seconds, 0.0)
        inter_chunk_delay_max_seconds = max(settings.action_inter_chunk_delay_max_seconds, inter_chunk_delay_min_seconds)
        return cls(
            enable_real_delays=settings.enable_real_delays,
            disable_sleep_state=settings.disable_sleep_state,
            transport_max_retries=settings.action_transport_max_retries,
            transport_retry_delay_seconds=settings.action_transport_retry_delay_seconds,
            typing_baseline_wpm=settings.action_typing_baseline_wpm,
            filler_pause_seconds=max(settings.action_filler_pause_seconds, 0.0),
            inter_chunk_delay_min_seconds=inter_chunk_delay_min_seconds,
            inter_chunk_delay_max_seconds=inter_chunk_delay_max_seconds,
            inter_chunk_delay_length_threshold_chars=settings.action_inter_chunk_delay_length_threshold_chars,
            inter_chunk_delay_chars_per_step=max(settings.action_inter_chunk_delay_chars_per_step, 1),
            inter_chunk_delay_step_seconds=max(settings.action_inter_chunk_delay_step_seconds, 0.0),
            inter_chunk_delay_total_max_seconds=max(settings.action_inter_chunk_delay_total_max_seconds, 0.0),
        )
