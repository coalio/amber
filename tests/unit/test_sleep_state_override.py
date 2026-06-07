from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.action.config import ActionConfig
from src.action.telegram.layer import ActionLayer
from src.action.telegram.transport import RecordingTransport
from src.attention.config import AttentionConfig
from src.attention.memory.store import MemoryStore
from src.attention.pipeline import AttentionLayer
from src.context.config import ContextConfig
from src.context.pipeline import ContextLayer
from src.context.session.store import ConversationSession
from src.events.action import SleepStateChangedEvent
from src.events.ai import SemanticDecisionMadeEvent, SemanticDecisionPayload
from src.events.attention import AttentionDecisionMadeEvent
from src.events.bus import EventBus
from src.events.context import ContextFrameMessagePayload, ContextFrameReadyEvent
from src.events.receiver import (
    TelegramAttachmentPayload,
    TelegramMessagePayload,
    TelegramMessageReceivedEvent,
    TelegramReplySenderPayload,
    TelegramSenderPayload,
    TelegramTransportPayload,
)
from src.state.store import GlobalStateStore
from src.utils.message_archive import MessageArchive
from src.utils.scheduler import RuntimeScheduler
from src.utils.sleep import fatigue_notice
from src.utils.time import utc_now


class StubAttentionScorer:
    def score(self, row: dict[str, object]) -> float:
        return 0.0


@pytest.fixture(autouse=True)
def reset_runtime_singletons() -> None:
    EventBus.reset_for_tests()
    MessageArchive.instance().reset()
    RuntimeScheduler.instance().shutdown()
    yield
    EventBus.reset_for_tests()
    MessageArchive.instance().reset()
    RuntimeScheduler.instance().shutdown()


def test_attention_layer_does_not_discard_when_sleep_state_is_disabled(tmp_path) -> None:
    state_store = GlobalStateStore(tmp_path / "runtime_state.json", "America/Managua")
    state_store.update_action_state(sleep_state="asleep")
    attention_layer = AttentionLayer(
        AttentionConfig(
            surface_threshold=0.0,
            urgent_threshold=1.0,
            memory_limit=0,
            disable_sleep_state=True,
        ),
        StubAttentionScorer(),
        state_store,
        MemoryStore(tmp_path / "memories"),
        MessageArchive.instance(),
    )
    captured: list[AttentionDecisionMadeEvent] = []
    EventBus.subscribe("AttentionDecisionMadeEvent", captured.append)

    attention_layer.handle_message(_telegram_received_event("amber can you take a look at this?"))

    assert captured
    assert captured[-1].payload.decision == "surface"
    assert "sleep_state_asleep" not in captured[-1].payload.reasons


def test_action_layer_forces_awake_and_ignores_sleep_decisions_when_disabled(tmp_path) -> None:
    state_store = GlobalStateStore(tmp_path / "runtime_state.json", "America/Managua")
    wake_at = utc_now() + timedelta(hours=4)
    state_store.update_action_state(
        sleep_state="asleep",
        scheduled_wake_at=wake_at,
        fatigue_alert_active=True,
        pending_sleep_window={"hard_cutoff": wake_at.isoformat()},
    )
    emitted: list[SleepStateChangedEvent] = []
    EventBus.subscribe("SleepStateChangedEvent", emitted.append)
    action_layer = ActionLayer(
        ActionConfig(
            enable_real_delays=False,
            disable_sleep_state=True,
            transport_max_retries=1,
            transport_retry_delay_seconds=2.0,
        ),
        RecordingTransport(),
        state_store,
        RuntimeScheduler.instance(),
        MessageArchive.instance(),
        "America/Managua",
    )

    state = state_store.snapshot()
    assert state.sleep_state == "awake"
    assert state.scheduled_wake_at is None
    assert state.fatigue_alert_active is False
    assert state.pending_sleep_window == {}

    action_layer.handle_semantic_decision(
        SemanticDecisionMadeEvent(
            chat_id=1001001001,
            payload=SemanticDecisionPayload(action="sleep", chat_id=1001001001),
        )
    )

    assert state_store.snapshot().sleep_state == "awake"
    assert emitted == []


def test_context_frames_omit_fatigue_notice_when_sleep_state_is_disabled(tmp_path) -> None:
    state_store = GlobalStateStore(tmp_path / "runtime_state.json", "America/Managua")
    state_store.update_action_state(
        sleep_state="awake",
        woke_at=utc_now() - timedelta(hours=18),
        energy_level=16.0,
    )
    context_layer = ContextLayer(
        ContextConfig(
            debounce_seconds=0.0,
            idle_timeout_seconds=60.0,
            competing_chat_timeout_seconds=15.0,
            recent_message_budget=8,
            max_compacted_facts=6,
            disable_sleep_state=True,
        ),
        state_store,
        RuntimeScheduler.instance(),
        MessageArchive.instance(),
        MemoryStore(tmp_path / "memories"),
        "America/Managua",
    )
    captured: list[ContextFrameReadyEvent] = []
    EventBus.subscribe("ContextFrameReadyEvent", captured.append)
    message = ContextFrameMessagePayload(
        message_id=412,
        sender_id="user-123",
        sender_name="Fixture Sender",
        content="Need a quick debug run on the sleep state override.",
        timestamp=datetime(2026, 4, 21, 3, 54, 5, tzinfo=timezone.utc),
    )
    session = ConversationSession(
        session_id="sess_debug",
        chat_id=1001001001,
        last_updated_at=utc_now(),
        recent_messages=[message],
        participant_names={"user-123": "Fixture Sender"},
        engaged_user_ids={"user-123"},
        latest_trigger_message_id=412,
    )

    assert fatigue_notice(state_store.snapshot(), "America/Managua") is not None

    context_layer._emit_frame(session)

    assert captured
    assert captured[-1].payload.fatigue_notice is None


def _telegram_received_event(content: str) -> TelegramMessageReceivedEvent:
    payload = TelegramMessagePayload(
        message_id=412,
        chat_id=1001001001,
        sender=TelegramSenderPayload(id="user-123", name="Fixture Sender"),
        timestamp=datetime(2026, 4, 21, 3, 54, 5, tzinfo=timezone.utc),
        content=content,
        raw_text=content,
        reply_to_message_id=None,
        reply_to_sender=TelegramReplySenderPayload(),
        mentions=["amber"],
        attachment=TelegramAttachmentPayload(),
        transport=TelegramTransportPayload(peer_id=1001001001, raw_chat_id=1001001001, raw_message_id=412),
    )
    return TelegramMessageReceivedEvent(chat_id=1001001001, payload=payload)
