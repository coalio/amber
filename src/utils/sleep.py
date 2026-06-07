from __future__ import annotations

from datetime import datetime, timedelta

from src.state.models import GlobalState
from src.utils.time import local_now


def compute_sleep_window(state: GlobalState, timezone_name: str) -> dict[str, str | float | None]:
    now_local = local_now(timezone_name)
    woke_local = state.woke_at.astimezone(now_local.tzinfo)
    max_awake_at = woke_local + timedelta(hours=state.energy_level)
    tired_window_start = max_awake_at - timedelta(hours=2)
    hard_cutoff = max_awake_at + timedelta(minutes=30)
    return {
        "now": now_local.isoformat(),
        "max_awake_at": max_awake_at.isoformat(),
        "tired_window_start": tired_window_start.isoformat(),
        "hard_cutoff": hard_cutoff.isoformat(),
    }


def fatigue_notice(state: GlobalState, timezone_name: str) -> str | None:
    if state.sleep_state != "awake":
        return None
    window = compute_sleep_window(state, timezone_name)
    now_value = datetime.fromisoformat(str(window["now"]))
    tired_value = datetime.fromisoformat(str(window["tired_window_start"]))
    hard_cutoff = datetime.fromisoformat(str(window["hard_cutoff"]))
    if now_value >= hard_cutoff:
        return "Amber is past the hard cutoff and should go to sleep now."
    if now_value >= tired_value:
        return "Amber is getting tired and should avoid starting new threads."
    return None

