from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.action.config import ActionConfig
from src.action.telegram.layer import ActionLayer
from src.action.telegram.transport import RecordingTransport
from src.attention.memory.store import MemoryStore
from src.context.config import ContextConfig
from src.context.pipeline import ContextLayer
from src.context.session.store import ConversationSession
from src.events.action import MessageReadEvent, MessageReadPayload, OutboundDeliveryPayload, OutboundMessageSentEvent
from src.events.bus import EventBus
from src.events.context import ContextFrameMessagePayload, ContextFrameReadyEvent
from src.events.receiver import TelegramSenderPayload, TelegramTypingPayload, TelegramTypingUpdatedEvent
from src.state.store import GlobalStateStore
from src.utils.message_archive import MessageArchive
from src.utils.scheduler import RuntimeScheduler
from src.utils.time import utc_now


@pytest.fixture(autouse=True)
def reset_runtime_singletons() -> None:
    EventBus.reset_for_tests()
    MessageArchive.instance().reset()
    RuntimeScheduler.instance().shutdown()
    yield
    EventBus.reset_for_tests()
    MessageArchive.instance().reset()
    RuntimeScheduler.instance().shutdown()


def test_context_attaches_visible_read_metadata_when_pending_frame_surfaces(tmp_path) -> None:
    context_layer = _build_context_layer(tmp_path)
    read_events: list[MessageReadEvent] = []
    frames: list[ContextFrameReadyEvent] = []
    EventBus.subscribe("MessageReadEvent", read_events.append)
    EventBus.subscribe("ContextFrameReadyEvent", frames.append)

    message = _message_payload(412, "Need a second pair of eyes on this patch.")
    session = ConversationSession(
        session_id="sess_read",
        chat_id=1001001001,
        last_updated_at=utc_now(),
        recent_messages=[message],
        participant_names={"user-123": "Fixture Sender"},
        engaged_user_ids={"user-123"},
        latest_trigger_message_id=412,
        pending_surfaced_messages={412: message},
        pending_first_surfaced_at=utc_now(),
    )

    context_layer._emit_pending_frame(session)

    assert frames
    assert read_events
    assert read_events[-1].correlation_id == frames[-1].correlation_id
    assert read_events[-1].payload.surfaced_message_ids == [412]
    assert read_events[-1].payload.surfaced_until_message_id == 412
    assert read_events[-1].payload.read_through_message_id == 412
    assert read_events[-1].payload.mark_seen is False
    assert frames[-1].payload.visible_surfaced_message_ids == [412]
    assert frames[-1].payload.visible_surfaced_until_message_id == 412
    assert frames[-1].payload.visible_read_through_message_id == 412
    assert session.pending_surfaced_messages == {}
    assert session.pending_first_surfaced_at is None
    assert session.read_cooldown_until is not None
    assert session.frame_in_flight is True


def test_follow_up_messages_flush_after_reply_and_read_through_reply(tmp_path) -> None:
    context_layer = _build_context_layer(tmp_path)
    read_events: list[MessageReadEvent] = []
    frames: list[ContextFrameReadyEvent] = []
    EventBus.subscribe("MessageReadEvent", read_events.append)
    EventBus.subscribe("ContextFrameReadyEvent", frames.append)

    first_message = _message_payload(412, "Checking whether you saw the earlier note.")
    session = ConversationSession(
        session_id="sess_reply",
        chat_id=1001001001,
        last_updated_at=utc_now(),
        recent_messages=[first_message],
        participant_names={"user-123": "Fixture Sender"},
        engaged_user_ids={"user-123"},
        latest_trigger_message_id=412,
        pending_surfaced_messages={412: first_message},
        pending_first_surfaced_at=utc_now(),
    )
    context_layer._active_session = session
    context_layer._emit_pending_frame(session)

    follow_up = _message_payload(413, "Actually, there is one more stack trace line.", minutes=1)
    context_layer._upsert_message(session, follow_up)
    context_layer._track_pending_surfaced_message(session, follow_up, utc_now())
    session.latest_trigger_message_id = 413
    session.last_updated_at = utc_now()
    session.pending_first_surfaced_at = utc_now() - timedelta(seconds=1)
    session.read_cooldown_until = utc_now() - timedelta(seconds=1)

    session.frame_in_flight = False
    session.pending_read_through_message_id = 500
    context_layer._finalize_session(session.session_id)

    assert len(read_events) == 2
    assert len(frames) == 2
    assert read_events[-1].correlation_id == frames[-1].correlation_id
    assert read_events[-1].payload.surfaced_message_ids == [413]
    assert read_events[-1].payload.surfaced_until_message_id == 413
    assert read_events[-1].payload.read_through_message_id == 500
    assert read_events[-1].payload.mark_seen is False
    assert frames[-1].payload.visible_surfaced_message_ids == [413]
    assert frames[-1].payload.visible_surfaced_until_message_id == 413
    assert frames[-1].payload.visible_read_through_message_id == 500
    assert frames[-1].payload.current_message.message_id == 413


def test_initial_engagement_delay_metadata_does_not_block_engagement_commit(tmp_path) -> None:
    context_layer = _build_context_layer(tmp_path)
    read_events: list[MessageReadEvent] = []
    frames: list[ContextFrameReadyEvent] = []
    EventBus.subscribe("MessageReadEvent", read_events.append)
    EventBus.subscribe("ContextFrameReadyEvent", frames.append)

    message = _message_payload(412, "Need a second pair of eyes on this patch.")
    session = ConversationSession(
        session_id="sess_pending_engage",
        chat_id=1001001001,
        last_updated_at=utc_now(),
        recent_messages=[message],
        participant_names={"user-123": "Fixture Sender"},
        engaged_user_ids={"user-123"},
        latest_trigger_message_id=412,
        pending_surfaced_messages={412: message},
        pending_first_surfaced_at=utc_now(),
        engagement_delay_until=utc_now() + timedelta(seconds=5),
    )
    context_layer._active_session = session
    context_layer._sync_context_state(session)

    context_layer._finalize_session(session.session_id)

    state = context_layer._state_store.snapshot()
    assert read_events
    assert frames
    assert state.active_chat_id == 1001001001
    assert state.active_session_id == session.session_id
    assert state.pending_chat_id is None
    assert state.pending_session_id is None
    assert session.engagement_committed is True
    assert frames[-1].payload.visible_read_not_before is not None
    assert read_events[-1].payload.visible_not_before is not None


def test_context_frame_includes_pending_interruption_plan_once(tmp_path) -> None:
    state_store = GlobalStateStore(tmp_path / "runtime_state.json", "America/Managua")
    context_layer = ContextLayer(
        ContextConfig(
            debounce_seconds=0.0,
            idle_timeout_seconds=60.0,
            competing_chat_timeout_seconds=15.0,
            recent_message_budget=8,
            max_compacted_facts=6,
            disable_sleep_state=True,
            initial_engagement_delay_min_seconds=2.0,
            initial_engagement_delay_max_seconds=10.0,
        ),
        state_store,
        RuntimeScheduler.instance(),
        MessageArchive.instance(),
        MemoryStore(tmp_path / "memories"),
        "America/Managua",
    )
    message = _message_payload(413, "wait, keep it short")
    session = ConversationSession(
        session_id="sess_interrupt",
        chat_id=1001001001,
        last_updated_at=utc_now(),
        recent_messages=[message],
        participant_names={"user-123": "Fixture Sender"},
        engaged_user_ids={"user-123"},
        latest_trigger_message_id=413,
    )
    state_store.remember_pending_interruption(
        chat_id=1001001001,
        session_id="sess_interrupt",
        original_trigger_message_id=412,
        original_reply_to_message_id=412,
        interrupting_message_id=413,
        reply_target_sender_id="user-123",
        reply_target_sender_name="Fixture Sender",
        sent_reply_chunks=["first chunk"],
        remaining_reply_chunks=["second chunk", "third chunk"],
        created_at=utc_now(),
    )

    first_frame = context_layer._build_frame_event(session)
    second_frame = context_layer._build_frame_event(session)

    assert first_frame.payload.pending_interruption is not None
    assert first_frame.payload.pending_interruption.interrupting_message_id == 413
    assert first_frame.payload.pending_interruption.remaining_reply_chunks == ["second chunk", "third chunk"]
    assert second_frame.payload.pending_interruption is None


def test_pending_interruption_survives_debounced_follow_up_messages(tmp_path) -> None:
    state_store = GlobalStateStore(tmp_path / "runtime_state.json", "America/Managua")
    context_layer = ContextLayer(
        ContextConfig(
            debounce_seconds=0.0,
            idle_timeout_seconds=60.0,
            competing_chat_timeout_seconds=15.0,
            recent_message_budget=8,
            max_compacted_facts=6,
            disable_sleep_state=True,
            initial_engagement_delay_min_seconds=2.0,
            initial_engagement_delay_max_seconds=10.0,
        ),
        state_store,
        RuntimeScheduler.instance(),
        MessageArchive.instance(),
        MemoryStore(tmp_path / "memories"),
        "America/Managua",
    )
    first = _message_payload(413, "i mean")
    second = _message_payload(414, "the history of it", minutes=1)
    session = ConversationSession(
        session_id="sess_interrupt_followup",
        chat_id=1001001001,
        last_updated_at=utc_now(),
        recent_messages=[first, second],
        participant_names={"user-123": "Fixture Sender"},
        engaged_user_ids={"user-123"},
        latest_trigger_message_id=414,
    )
    state_store.remember_pending_interruption(
        chat_id=1001001001,
        session_id="sess_interrupt_followup",
        original_trigger_message_id=412,
        original_reply_to_message_id=412,
        interrupting_message_id=413,
        reply_target_sender_id="user-123",
        reply_target_sender_name="Fixture Sender",
        sent_reply_chunks=["metaprogramming is code that shapes code"],
        remaining_reply_chunks=["c++ uses it for templates and nicer interfaces"],
        created_at=utc_now(),
    )

    frame = context_layer._build_frame_event(session)

    assert frame.payload.current_message.message_id == 414
    assert frame.payload.pending_interruption is not None
    assert frame.payload.pending_interruption.interrupting_message_id == 413
    assert frame.payload.pending_interruption.remaining_reply_chunks == ["c++ uses it for templates and nicer interfaces"]


def test_expire_session_does_not_disengage_while_frame_is_in_flight(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    context_layer = _build_context_layer(tmp_path)
    session = ConversationSession(
        session_id="sess_in_flight",
        chat_id=1001001001,
        last_updated_at=utc_now() - timedelta(seconds=120),
        recent_messages=[_message_payload(412, "still here")],
        latest_trigger_message_id=412,
        frame_in_flight=True,
    )
    context_layer._active_session = session
    scheduled: list[tuple[str, float, tuple[object, ...]]] = []

    def record_schedule(key: str, delay_seconds: float, callback, *args) -> None:
        scheduled.append((key, delay_seconds, args))

    monkeypatch.setattr(context_layer._scheduler, "schedule_after", record_schedule)

    context_layer._expire_session(session.session_id)

    assert context_layer._active_session is session
    assert scheduled == [(f"context_expire:{session.session_id}", 1.0, (session.session_id,))]


def test_outbound_delivery_refreshes_session_activity_and_reschedules_expiry(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    context_layer = _build_context_layer(tmp_path)
    session = ConversationSession(
        session_id="sess_delivery",
        chat_id=1001001001,
        last_updated_at=utc_now() - timedelta(seconds=120),
        recent_messages=[_message_payload(412, "still here")],
        latest_trigger_message_id=412,
        frame_in_flight=True,
        idle_expire_at=utc_now() + timedelta(seconds=1),
    )
    context_layer._active_session = session
    before_delivery = session.last_updated_at
    scheduled: list[tuple[str, float, tuple[object, ...]]] = []

    def record_schedule(key: str, delay_seconds: float, callback, *args) -> None:
        scheduled.append((key, delay_seconds, args))

    monkeypatch.setattr(context_layer._scheduler, "schedule_after", record_schedule)
    monkeypatch.setattr(context_layer, "_schedule_finalize", lambda session: None)
    monkeypatch.setattr(context_layer, "_random_idle_timeout_seconds", lambda: 9.0)

    context_layer.handle_outbound_delivery(
        OutboundMessageSentEvent(
            chat_id=1001001001,
            payload=OutboundDeliveryPayload(
                chat_id=1001001001,
                session_id=session.session_id,
                trigger_message_id=412,
                ordered_messages=["reply"],
                sent_message_ids=[500],
                no_send=False,
            ),
        )
    )

    assert session.frame_in_flight is False
    assert session.last_updated_at > before_delivery
    assert session.pending_read_through_message_id == 500
    assert session.idle_expire_at is not None
    assert scheduled == [(f"context_expire:{session.session_id}", 9.0, (session.session_id,))]


def test_action_layer_marks_transport_read_records(tmp_path) -> None:
    transport = RecordingTransport()
    action_layer = ActionLayer(
        ActionConfig(
            enable_real_delays=False,
            disable_sleep_state=True,
            transport_max_retries=1,
            transport_retry_delay_seconds=2.0,
        ),
        transport,
        GlobalStateStore(tmp_path / "runtime_state.json", "America/Managua"),
        RuntimeScheduler.instance(),
        MessageArchive.instance(),
        "America/Managua",
    )

    action_layer.handle_message_read(
        MessageReadEvent(
            chat_id=1001001001,
            payload=MessageReadPayload(
                chat_id=1001001001,
                session_id="sess_transport",
                trigger_message_id=412,
                surfaced_message_ids=[412],
                surfaced_until_message_id=412,
                read_through_message_id=500,
            ),
        )
    )

    assert [(item.chat_id, item.read_through_message_id) for item in transport.read_records] == [(1001001001, 500)]


def test_action_layer_marks_visible_read_immediately_even_when_not_before_is_present(tmp_path) -> None:
    transport = RecordingTransport()
    action_layer = ActionLayer(
        ActionConfig(
            enable_real_delays=False,
            disable_sleep_state=True,
            transport_max_retries=1,
            transport_retry_delay_seconds=2.0,
        ),
        transport,
        GlobalStateStore(tmp_path / "runtime_state_visible.json", "America/Managua"),
        RuntimeScheduler.instance(),
        MessageArchive.instance(),
        "America/Managua",
    )

    action_layer.handle_message_read(
        MessageReadEvent(
            chat_id=1001001001,
            payload=MessageReadPayload(
                chat_id=1001001001,
                session_id="sess_transport",
                trigger_message_id=412,
                surfaced_message_ids=[412],
                surfaced_until_message_id=412,
                read_through_message_id=500,
                mark_seen=False,
                visible_not_before=utc_now() + timedelta(seconds=5),
            ),
        )
    )

    assert [(item.chat_id, item.read_through_message_id) for item in transport.read_records] == [(1001001001, 500)]


def test_action_layer_syncs_offline_presence_after_startup_without_engagement(tmp_path) -> None:
    transport = RecordingTransport()

    action_layer = ActionLayer(
        ActionConfig(
            enable_real_delays=False,
            disable_sleep_state=True,
            transport_max_retries=1,
            transport_retry_delay_seconds=2.0,
        ),
        transport,
        GlobalStateStore(tmp_path / "runtime_state_presence.json", "America/Managua"),
        RuntimeScheduler.instance(),
        MessageArchive.instance(),
        "America/Managua",
    )

    assert transport.presence_records == []

    action_layer.sync_presence_from_state()

    assert transport.presence_records == [False]


def test_presence_flips_online_on_engagement_and_offline_on_disengage(tmp_path) -> None:
    state_store = GlobalStateStore(tmp_path / "runtime_state_presence_flow.json", "America/Managua")
    transport = RecordingTransport()
    action_layer = ActionLayer(
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
    action_layer.sync_presence_from_state()
    context_layer = _build_context_layer(tmp_path)
    context_layer._state_store = state_store
    message = _message_payload(412, "Need a second pair of eyes on this patch.")
    session = ConversationSession(
        session_id="sess_presence",
        chat_id=1001001001,
        last_updated_at=utc_now(),
        recent_messages=[message],
        participant_names={"user-123": "Fixture Sender"},
        engaged_user_ids={"user-123"},
        latest_trigger_message_id=412,
    )
    context_layer._active_session = session
    context_layer._sync_context_state(session)

    context_layer.handle_message_read(
        MessageReadEvent(
            chat_id=1001001001,
            payload=MessageReadPayload(
                chat_id=1001001001,
                session_id=session.session_id,
                trigger_message_id=412,
                surfaced_message_ids=[412],
                surfaced_until_message_id=412,
                read_through_message_id=412,
                mark_seen=False,
            ),
        )
    )
    context_layer._disengage_session(
        session,
        correlation_id=None,
        trigger_message_id=412,
        reason="idle_timeout",
    )

    assert transport.presence_records == [False, True, False]


def test_expire_session_waits_while_engaged_participant_is_typing(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    context_layer = _build_context_layer(tmp_path)
    session = ConversationSession(
        session_id="sess_typing",
        chat_id=1001001001,
        last_updated_at=utc_now() - timedelta(seconds=120),
        recent_messages=[_message_payload(412, "still here")],
        participant_names={"user-123": "Fixture Sender"},
        engaged_user_ids={"user-123"},
        latest_trigger_message_id=412,
        idle_expire_at=utc_now() - timedelta(seconds=1),
    )
    context_layer._active_session = session
    scheduled: list[tuple[str, float, tuple[object, ...]]] = []

    def record_schedule(key: str, delay_seconds: float, callback, *args) -> None:
        scheduled.append((key, delay_seconds, args))

    monkeypatch.setattr(context_layer._scheduler, "schedule_after", record_schedule)
    context_layer.handle_typing_update(
        TelegramTypingUpdatedEvent(
            chat_id=1001001001,
            payload=TelegramTypingPayload(
                chat_id=1001001001,
                sender=TelegramSenderPayload(id="user-123", name="Fixture Sender"),
                timestamp=utc_now(),
                active=True,
                activity="typing",
                expires_at=utc_now() + timedelta(seconds=6),
            ),
        )
    )

    context_layer._expire_session(session.session_id)

    assert context_layer._active_session is session
    assert scheduled
    assert scheduled[-1][0] == f"context_expire:{session.session_id}"
    assert scheduled[-1][1] >= 1.0


def test_pending_frame_waits_while_engaged_participant_is_typing(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    context_layer = _build_context_layer(tmp_path)
    message = _message_payload(412, "one more thought")
    session = ConversationSession(
        session_id="sess_typing_frame",
        chat_id=1001001001,
        last_updated_at=utc_now(),
        recent_messages=[message],
        participant_names={"user-123": "Fixture Sender"},
        engaged_user_ids={"user-123"},
        latest_trigger_message_id=412,
        pending_surfaced_messages={412: message},
        pending_first_surfaced_at=utc_now() - timedelta(seconds=10),
        pending_latest_surfaced_at=utc_now() - timedelta(seconds=10),
    )
    context_layer._active_session = session
    frames: list[ContextFrameReadyEvent] = []
    scheduled: list[tuple[str, float, tuple[object, ...]]] = []
    EventBus.subscribe("ContextFrameReadyEvent", frames.append)

    def record_schedule(key: str, delay_seconds: float, callback, *args) -> None:
        scheduled.append((key, delay_seconds, args))

    monkeypatch.setattr(context_layer._scheduler, "schedule_after", record_schedule)
    context_layer.handle_typing_update(
        TelegramTypingUpdatedEvent(
            chat_id=1001001001,
            payload=TelegramTypingPayload(
                chat_id=1001001001,
                sender=TelegramSenderPayload(id="user-123", name="Fixture Sender"),
                timestamp=utc_now(),
                active=True,
                activity="typing",
                expires_at=utc_now() + timedelta(seconds=6),
            ),
        )
    )

    context_layer._finalize_session(session.session_id)

    assert frames == []
    assert scheduled
    assert scheduled[-1][0] == f"context_finalize:{session.session_id}"
    assert scheduled[-1][1] >= 1.0


def test_debounce_counts_from_latest_surfaced_message(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    context_layer = _build_context_layer(tmp_path)
    first = _message_payload(412, "first")
    second = _message_payload(413, "second")
    now = utc_now()
    session = ConversationSession(
        session_id="sess_latest_debounce",
        chat_id=1001001001,
        last_updated_at=now,
        recent_messages=[first, second],
        participant_names={"user-123": "Fixture Sender"},
        engaged_user_ids={"user-123"},
        latest_trigger_message_id=413,
        pending_surfaced_messages={412: first, 413: second},
        pending_first_surfaced_at=now - timedelta(seconds=10),
        pending_latest_surfaced_at=now,
    )
    context_layer._config = ContextConfig(
        debounce_seconds=5.0,
        idle_timeout_seconds=60.0,
        competing_chat_timeout_seconds=15.0,
        recent_message_budget=8,
        max_compacted_facts=6,
        disable_sleep_state=True,
    )
    context_layer._active_session = session
    frames: list[ContextFrameReadyEvent] = []
    scheduled: list[tuple[str, float, tuple[object, ...]]] = []
    EventBus.subscribe("ContextFrameReadyEvent", frames.append)

    def record_schedule(key: str, delay_seconds: float, callback, *args) -> None:
        scheduled.append((key, delay_seconds, args))

    monkeypatch.setattr(context_layer._scheduler, "schedule_after", record_schedule)

    context_layer._finalize_session(session.session_id)

    assert frames == []
    assert scheduled
    assert scheduled[-1][0] == f"context_finalize:{session.session_id}"
    assert scheduled[-1][1] > 0


def test_resolve_session_rotates_when_idle_deadline_has_passed(tmp_path) -> None:
    context_layer = _build_context_layer(tmp_path)
    expired_session = ConversationSession(
        session_id="sess_expired",
        chat_id=1001001001,
        last_updated_at=utc_now(),
        recent_messages=[_message_payload(412, "old context")],
        latest_trigger_message_id=412,
        idle_expire_at=utc_now() - timedelta(seconds=1),
    )
    context_layer._active_session = expired_session

    resolved = context_layer._resolve_session(1001001001)

    assert resolved is not expired_session
    assert resolved.chat_id == 1001001001
    assert resolved.session_id != expired_session.session_id


def _build_context_layer(tmp_path) -> ContextLayer:
    return ContextLayer(
        ContextConfig(
            debounce_seconds=0.0,
            idle_timeout_seconds=60.0,
            competing_chat_timeout_seconds=15.0,
            recent_message_budget=8,
            max_compacted_facts=6,
            disable_sleep_state=True,
            initial_engagement_delay_min_seconds=2.0,
            initial_engagement_delay_max_seconds=10.0,
        ),
        GlobalStateStore(tmp_path / "runtime_state.json", "America/Managua"),
        RuntimeScheduler.instance(),
        MessageArchive.instance(),
        MemoryStore(tmp_path / "memories"),
        "America/Managua",
    )


def _message_payload(message_id: int, content: str, *, minutes: int = 0) -> ContextFrameMessagePayload:
    return ContextFrameMessagePayload(
        message_id=message_id,
        sender_id="user-123",
        sender_name="Fixture Sender",
        content=content,
        timestamp=datetime(2026, 4, 21, 3, 54 + minutes, 5, tzinfo=timezone.utc),
    )
