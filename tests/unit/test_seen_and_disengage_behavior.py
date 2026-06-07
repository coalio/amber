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
from src.events.ai import SemanticDecisionMadeEvent, SemanticDecisionPayload
from src.events.attention import AttentionDecisionMadeEvent
from src.events.bus import EventBus
from src.events.context import ContextFrameMessagePayload
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


def test_attention_discards_messages_already_marked_seen(tmp_path) -> None:
    state_store = GlobalStateStore(tmp_path / "runtime_state.json", "America/Managua")
    state_store.update_action_state(seen_through_by_chat={"1001001001": 412})
    attention_layer = _build_attention_layer(tmp_path, state_store=state_store, disable_sleep_state=True)
    captured: list[AttentionDecisionMadeEvent] = []
    EventBus.subscribe("AttentionDecisionMadeEvent", captured.append)

    attention_layer.handle_message(_telegram_received_event("amber did you see this?", message_id=412))

    assert captured
    assert captured[-1].payload.decision == "discard"
    assert captured[-1].payload.reasons == ["already_seen"]


def test_attention_marks_messages_from_sleep_window_as_seen(tmp_path) -> None:
    now = datetime(2026, 4, 21, 8, 0, tzinfo=timezone.utc)
    slept_at = now - timedelta(hours=7)
    message_time = slept_at + timedelta(minutes=30)
    state_store = GlobalStateStore(tmp_path / "runtime_state.json", "America/Managua")
    state_store.update_action_state(
        sleep_state="awake",
        slept_at=slept_at,
        woke_at=now,
        scheduled_wake_at=None,
    )
    transport = RecordingTransport()
    ActionLayer(
        ActionConfig(
            enable_real_delays=False,
            disable_sleep_state=False,
            transport_max_retries=1,
            transport_retry_delay_seconds=2.0,
        ),
        transport,
        state_store,
        RuntimeScheduler.instance(),
        MessageArchive.instance(),
        "America/Managua",
    )
    attention_layer = _build_attention_layer(tmp_path, state_store=state_store, disable_sleep_state=False)
    captured: list[AttentionDecisionMadeEvent] = []
    EventBus.subscribe("AttentionDecisionMadeEvent", captured.append)

    attention_layer.handle_message(_telegram_received_event("you missed this while asleep", message_id=412, timestamp=message_time))

    assert captured
    assert captured[-1].payload.decision == "discard"
    assert captured[-1].payload.reasons == ["sleep_state_asleep"]
    assert [(item.chat_id, item.read_through_message_id) for item in transport.read_records] == [(1001001001, 412)]
    assert state_store.snapshot().seen_through_by_chat == {"1001001001": 412}


def test_attention_discards_messages_inside_ignore_window_and_marks_seen(tmp_path) -> None:
    now = datetime(2026, 4, 21, 8, 0, tzinfo=timezone.utc)
    state_store = GlobalStateStore(tmp_path / "runtime_state.json", "America/Managua")
    state_store.remember_conversation_ignore(
        chat_id=1001001001,
        sender_id="user-123",
        sender_name="Fixture Sender",
        created_at=now - timedelta(minutes=5),
        ignore_until=now + timedelta(minutes=30),
        reason="cooldown",
    )
    transport = RecordingTransport()
    ActionLayer(
        ActionConfig(
            enable_real_delays=False,
            disable_sleep_state=True,
            transport_max_retries=1,
            transport_retry_delay_seconds=2.0,
        ),
        transport,
        state_store,
        RuntimeScheduler.instance(),
        MessageArchive.instance(),
        "America/Managua",
    )
    attention_layer = _build_attention_layer(tmp_path, state_store=state_store, disable_sleep_state=True)
    captured: list[AttentionDecisionMadeEvent] = []
    EventBus.subscribe("AttentionDecisionMadeEvent", captured.append)

    attention_layer.handle_message(_telegram_received_event("still trying to get a rise out of amber", timestamp=now))

    assert captured
    assert captured[-1].payload.decision == "discard"
    assert captured[-1].payload.reasons == ["ignore_window_active"]
    assert [(item.chat_id, item.read_through_message_id) for item in transport.read_records] == [(1001001001, 412)]
    assert state_store.snapshot().seen_through_by_chat == {"1001001001": 412}


def test_disengage_clears_session_sets_ignore_and_writes_bad_memory(tmp_path) -> None:
    state_store = GlobalStateStore(tmp_path / "runtime_state.json", "America/Managua")
    memory_store = MemoryStore(tmp_path / "memories")
    transport = RecordingTransport()
    ActionLayer(
        ActionConfig(
            enable_real_delays=False,
            disable_sleep_state=True,
            transport_max_retries=1,
            transport_retry_delay_seconds=2.0,
        ),
        transport,
        state_store,
        RuntimeScheduler.instance(),
        MessageArchive.instance(),
        "America/Managua",
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
        memory_store,
        "America/Managua",
    )
    trigger_message = _context_message(412, "Fixture Sender", "user-123", "keep going then, say something back")
    pending_message = _context_message(413, "Fixture Sender", "user-123", "yeah exactly, thats what I thought")
    session = ConversationSession(
        session_id="sess_disengage",
        chat_id=1001001001,
        last_updated_at=utc_now(),
        recent_messages=[trigger_message, pending_message],
        participant_names={"user-123": "Fixture Sender"},
        engaged_user_ids={"user-123"},
        latest_trigger_message_id=412,
        pending_surfaced_messages={413: pending_message},
        pending_first_surfaced_at=utc_now(),
        frame_in_flight=True,
    )
    context_layer._active_session = session
    state_store.update_context_state(
        active_chat_id=1001001001,
        active_session_id=session.session_id,
        last_engagement_at=utc_now(),
        conversation_engaged_user_ids=["user-123"],
    )

    context_layer.handle_semantic_decision(
        SemanticDecisionMadeEvent(
            chat_id=1001001001,
            payload=SemanticDecisionPayload(
                action="disengage",
                chat_id=1001001001,
                trigger_message_id=412,
                session_id=session.session_id,
                disengage_sender_id="user-123",
                disengage_reason="Fixture Sender kept baiting a hostile argument.",
                ignore_for_seconds=1800,
                create_bad_memory=True,
                bad_memory_sender_id="user-123",
                bad_memory_text="Fixture Sender kept baiting hostile arguments and pushing for a reaction.",
            ),
        )
    )

    state = state_store.snapshot()
    rule = state.conversation_ignore_rules["1001001001:user-123"]
    memories = memory_store._iter_memories("user-123", "Fixture Sender")

    assert context_layer._active_session is None
    assert state.active_chat_id is None
    assert state.active_session_id is None
    assert state.conversation_engaged_user_ids == []
    assert state.seen_through_by_chat == {"1001001001": 413}
    assert rule.reason == "Fixture Sender kept baiting a hostile argument."
    assert rule.sender_name == "Fixture Sender"
    assert (rule.ignore_until - rule.created_at).total_seconds() == 1800
    assert [(item.chat_id, item.read_through_message_id) for item in transport.read_records] == [(1001001001, 413)]
    assert memories[-1].text == "Fixture Sender kept baiting hostile arguments and pushing for a reaction."
    assert "negative_interaction" in memories[-1].tags


def test_disengage_writes_bad_memory_to_explicit_profile_owner(tmp_path) -> None:
    state_store = GlobalStateStore(tmp_path / "runtime_state.json", "America/Managua")
    memory_store = MemoryStore(tmp_path / "memories")
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
        memory_store,
        "America/Managua",
    )
    trigger_message = _context_message(420, "Fixture Peer", "user-456", "lets just move on")
    prior_offender_message = _context_message(419, "Fixture Sender", "user-123", "still pushing the same hostile bait")
    session = ConversationSession(
        session_id="sess_bad_memory_owner",
        chat_id=1001001001,
        last_updated_at=utc_now(),
        recent_messages=[prior_offender_message, trigger_message],
        participant_names={"user-123": "Fixture Sender", "user-456": "Fixture Peer"},
        engaged_user_ids={"user-123", "user-456"},
        latest_trigger_message_id=420,
    )
    context_layer._active_session = session

    context_layer.handle_semantic_decision(
        SemanticDecisionMadeEvent(
            chat_id=1001001001,
            payload=SemanticDecisionPayload(
                action="disengage",
                chat_id=1001001001,
                trigger_message_id=420,
                session_id=session.session_id,
                disengage_sender_id="user-456",
                disengage_reason="Fixture Peer is not the problem, but this conversation is done.",
                create_bad_memory=True,
                bad_memory_sender_id="user-123",
                bad_memory_text="Fixture Sender kept pushing hostile bait in the chat.",
            ),
        )
    )

    primary_memories = memory_store._iter_memories("user-123", "Fixture Sender")
    peer_memories = memory_store._iter_memories("user-456", "Fixture Peer")

    assert primary_memories[-1].text == "Fixture Sender kept pushing hostile bait in the chat."
    assert peer_memories == []


def _build_attention_layer(
    tmp_path,
    *,
    state_store: GlobalStateStore,
    disable_sleep_state: bool,
) -> AttentionLayer:
    return AttentionLayer(
        AttentionConfig(
            surface_threshold=0.0,
            urgent_threshold=1.0,
            memory_limit=0,
            disable_sleep_state=disable_sleep_state,
        ),
        StubAttentionScorer(),
        state_store,
        MemoryStore(tmp_path / "memories"),
        MessageArchive.instance(),
    )


def _telegram_received_event(
    content: str,
    *,
    message_id: int = 412,
    timestamp: datetime | None = None,
) -> TelegramMessageReceivedEvent:
    sent_at = timestamp or datetime(2026, 4, 21, 3, 54, 5, tzinfo=timezone.utc)
    payload = TelegramMessagePayload(
        message_id=message_id,
        chat_id=1001001001,
        sender=TelegramSenderPayload(id="user-123", name="Fixture Sender"),
        timestamp=sent_at,
        content=content,
        raw_text=content,
        reply_to_message_id=None,
        reply_to_sender=TelegramReplySenderPayload(),
        mentions=["amber"] if "amber" in content.lower() else [],
        attachment=TelegramAttachmentPayload(),
        transport=TelegramTransportPayload(peer_id=1001001001, raw_chat_id=1001001001, raw_message_id=message_id),
    )
    return TelegramMessageReceivedEvent(chat_id=1001001001, payload=payload)


def _context_message(message_id: int, sender_name: str, sender_id: str, content: str) -> ContextFrameMessagePayload:
    return ContextFrameMessagePayload(
        message_id=message_id,
        sender_id=sender_id,
        sender_name=sender_name,
        content=content,
        timestamp=datetime(2026, 4, 21, 4, 0 + (message_id - 412), 0, tzinfo=timezone.utc),
    )
