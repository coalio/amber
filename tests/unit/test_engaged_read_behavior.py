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
from src.events.action import OutboundMessageSentEvent
from src.events.outbound import OutboundMessagePreparedEvent, OutboundMessagePreparedPayload
from src.events.receiver import (
    TelegramAttachmentPayload,
    TelegramMessagePayload,
    TelegramMessageReceivedEvent,
    TelegramReplySenderPayload,
    TelegramSenderPayload,
    TelegramTransportPayload,
)
from src.state.models import OpenQuestionCandidate
from src.state.store import GlobalStateStore
from src.utils.message_archive import MessageArchive
from src.utils.scheduler import RuntimeScheduler
from src.utils.time import utc_now


class StubAttentionScorer:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def score(self, row: dict[str, object]) -> float:
        self.calls.append(row)
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


from src.events.bus import EventBus


def test_active_chat_messages_are_visibly_read_but_not_marked_seen(tmp_path) -> None:
    state_store = GlobalStateStore(tmp_path / "runtime_state.json", "America/Managua")
    state_store.update_context_state(
        active_chat_id=1001001001,
        active_session_id="sess_active",
        last_engagement_at=utc_now(),
        conversation_engaged_user_ids=["user-123"],
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

    attention_layer.handle_message(_telegram_received_event("amber, still around?", message_id=412))

    assert [(item.chat_id, item.read_through_message_id) for item in transport.read_records] == [(1001001001, 412)]
    assert state_store.snapshot().seen_through_by_chat == {}


def test_prepared_outbound_batch_marks_chat_read_without_marking_seen(tmp_path) -> None:
    state_store = GlobalStateStore(tmp_path / "runtime_state.json", "America/Managua")
    transport = RecordingTransport()
    archive = MessageArchive.instance()
    archive.put(_telegram_message("latest inbound before send", message_id=412))
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
        archive,
        "America/Managua",
    )

    action_layer.handle_prepared_message(
        OutboundMessagePreparedEvent(
            chat_id=1001001001,
            payload=OutboundMessagePreparedPayload(
                chat_id=1001001001,
                session_id="sess_send",
                trigger_message_id=412,
                ordered_messages=["first reply", "second reply"],
                reply_to_message_id=412,
                mood="calm",
                no_send=False,
            ),
        )
    )

    assert transport.read_records
    assert transport.read_records[0].read_through_message_id == 412
    assert state_store.snapshot().seen_through_by_chat == {}


def test_codex_clarification_is_interrupted_by_newer_open_question_reply(tmp_path) -> None:
    state_store = GlobalStateStore(tmp_path / "runtime_state.json", "America/Managua")
    state_store.remember_open_question(
        chat_id=1001001001,
        sender_id="user-123",
        sender_name="Fixture Sender",
        app_server_id="codex-sandbox",
        task_id="task-1",
        tool_call_id="tool-1",
        questions=["What filename should Codex use?"],
        task_description="Create a Python echo script.",
        context={},
        candidate_people=[
            OpenQuestionCandidate(
                sender_id="user-123",
                chat_id=1001001001,
                display_name="Fixture Sender",
            )
        ],
        created_at=utc_now(),
    )
    transport = RecordingTransport()
    archive = MessageArchive.instance()
    archive.put(_telegram_message("literal hello world is fine", message_id=412, sender_id="user-123"))
    archive.put(_telegram_message("use script.py", message_id=413, sender_id="user-123"))
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
        archive,
        "America/Managua",
    )
    deliveries: list[OutboundMessageSentEvent] = []
    EventBus.subscribe("OutboundMessageSentEvent", deliveries.append)

    action_layer.handle_prepared_message(
        OutboundMessagePreparedEvent(
            chat_id=1001001001,
            payload=OutboundMessagePreparedPayload(
                chat_id=1001001001,
                session_id="sess_send",
                trigger_message_id=412,
                ordered_messages=["what filename should codex use?"],
                mood="neutral",
                frame_created_at=utc_now(),
                visible_surfaced_message_ids=[412],
                visible_surfaced_until_message_id=412,
                visible_read_through_message_id=412,
            ),
        )
    )

    assert deliveries
    assert deliveries[-1].payload.interrupted is True
    assert deliveries[-1].payload.interruption_message_id == 413
    assert transport.records == []
    assert state_store.snapshot().pending_interruption is not None


def test_pending_engagement_messages_surface_without_visible_read(tmp_path) -> None:
    state_store = GlobalStateStore(tmp_path / "runtime_state.json", "America/Managua")
    state_store.update_context_state(
        pending_chat_id=1001001001,
        pending_session_id="sess_pending",
        pending_engaged_user_ids=["user-123"],
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
    attention_layer = AttentionLayer(
        AttentionConfig(
            surface_threshold=0.5,
            urgent_threshold=10.0,
            memory_limit=0,
            disable_sleep_state=True,
        ),
        StubAttentionScorer(),
        state_store,
        MemoryStore(tmp_path / "memories"),
        MessageArchive.instance(),
    )
    decisions = []
    EventBus.subscribe("AttentionDecisionMadeEvent", decisions.append)

    attention_layer.handle_message(_telegram_received_event("just c++, i wanted to know about sfinae", message_id=412))

    assert decisions
    assert decisions[-1].payload.decision == "surface"
    assert "pending_engagement_bypass" in decisions[-1].payload.reasons
    assert transport.read_records == []
    assert state_store.snapshot().seen_through_by_chat == {}


def test_always_surface_sender_bypasses_attention_score(tmp_path) -> None:
    state_store = GlobalStateStore(tmp_path / "runtime_state.json", "America/Managua")
    attention_layer = AttentionLayer(
        AttentionConfig(
            surface_threshold=0.5,
            urgent_threshold=10.0,
            memory_limit=0,
            disable_sleep_state=True,
            always_surface_telegram_ids=frozenset({"1001001001"}),
        ),
        StubAttentionScorer(),
        state_store,
        MemoryStore(tmp_path / "memories"),
        MessageArchive.instance(),
    )
    decisions = []
    EventBus.subscribe("AttentionDecisionMadeEvent", decisions.append)

    attention_layer.handle_message(
        TelegramMessageReceivedEvent(
            chat_id=1001001001,
            payload=_telegram_message(
                "low score but trusted sender",
                message_id=412,
                sender_id="1001001001",
                sender_name="Fixture User",
            ),
        )
    )

    assert decisions
    assert decisions[-1].payload.decision == "surface"
    assert "always_surface_sender" in decisions[-1].payload.reasons


def test_heuristic_only_attention_uses_heuristics_as_model_score(tmp_path) -> None:
    state_store = GlobalStateStore(tmp_path / "runtime_state.json", "America/Managua")
    attention_layer = AttentionLayer(
        AttentionConfig(
            surface_threshold=0.5,
            urgent_threshold=1.0,
            memory_limit=0,
            disable_sleep_state=True,
        ),
        None,
        state_store,
        MemoryStore(tmp_path / "memories"),
        MessageArchive.instance(),
    )
    decisions = []
    EventBus.subscribe("AttentionDecisionMadeEvent", decisions.append)

    attention_layer.handle_message(_telegram_received_event("amber, can you help with this?", message_id=412))

    assert decisions
    assert decisions[-1].payload.decision == "surface"
    assert decisions[-1].payload.model_score == decisions[-1].payload.heuristic_score
    assert decisions[-1].payload.classification is None
    assert "direct_mention" in decisions[-1].payload.reasons


def test_work_mode_discards_non_allowlisted_sender_even_when_directed(tmp_path) -> None:
    state_store = GlobalStateStore(tmp_path / "runtime_state.json", "America/Managua")
    attention_layer = AttentionLayer(
        AttentionConfig(
            surface_threshold=0.0,
            urgent_threshold=10.0,
            memory_limit=0,
            disable_sleep_state=True,
            mode="work",
        ),
        StubAttentionScorer(),
        state_store,
        MemoryStore(tmp_path / "memories"),
        MessageArchive.instance(),
    )
    decisions = []
    EventBus.subscribe("AttentionDecisionMadeEvent", decisions.append)

    attention_layer.handle_message(_telegram_received_event("amber, can you look at this?", message_id=412))

    assert decisions
    assert decisions[-1].payload.decision == "discard"
    assert decisions[-1].payload.reasons == ["work_mode_sender_not_allowed"]


def test_work_mode_still_surfaces_allowlisted_sender(tmp_path) -> None:
    state_store = GlobalStateStore(tmp_path / "runtime_state.json", "America/Managua")
    scorer = StubAttentionScorer()
    attention_layer = AttentionLayer(
        AttentionConfig(
            surface_threshold=0.5,
            urgent_threshold=0.9,
            memory_limit=0,
            disable_sleep_state=True,
            mode="work",
            always_surface_telegram_ids=frozenset({"1001001001"}),
        ),
        scorer,
        state_store,
        MemoryStore(tmp_path / "memories"),
        MessageArchive.instance(),
    )
    decisions = []
    EventBus.subscribe("AttentionDecisionMadeEvent", decisions.append)

    attention_layer.handle_message(
        TelegramMessageReceivedEvent(
            chat_id=1001001001,
            payload=_telegram_message(
                "low score but trusted sender",
                message_id=412,
                sender_id="1001001001",
                sender_name="Fixture User",
            ),
        )
    )

    assert decisions
    assert decisions[-1].payload.decision == "surface_urgent"
    assert decisions[-1].payload.attention_score == 1.0
    assert decisions[-1].payload.heuristic_score == 1.0
    assert decisions[-1].payload.model_score == 1.0
    assert scorer.calls == []
    assert "work_mode_full_importance" in decisions[-1].payload.reasons
    assert "always_surface_sender" in decisions[-1].payload.reasons


def test_idle_expiry_marks_everything_seen_up_to_disengagement_point(tmp_path) -> None:
    state_store = GlobalStateStore(tmp_path / "runtime_state.json", "America/Managua")
    transport = RecordingTransport()
    archive = MessageArchive.instance()
    archive.put(_telegram_message("first inbound", message_id=412))
    archive.put(_telegram_message("amber reply", message_id=500, is_self=True))
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
        archive,
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
        archive,
        MemoryStore(tmp_path / "memories"),
        "America/Managua",
    )
    session = ConversationSession(
        session_id="sess_expire",
        chat_id=1001001001,
        last_updated_at=utc_now() - timedelta(seconds=120),
        recent_messages=[],
        latest_trigger_message_id=412,
        idle_expire_at=utc_now() - timedelta(seconds=1),
    )
    context_layer._active_session = session
    state_store.update_context_state(
        active_chat_id=1001001001,
        active_session_id=session.session_id,
        last_engagement_at=utc_now() - timedelta(seconds=120),
        conversation_engaged_user_ids=["user-123"],
    )

    context_layer._expire_session(session.session_id)

    assert transport.read_records
    assert transport.read_records[-1].read_through_message_id == 500
    assert state_store.snapshot().seen_through_by_chat == {"1001001001": 500}


def _telegram_received_event(content: str, *, message_id: int) -> TelegramMessageReceivedEvent:
    payload = _telegram_message(content, message_id=message_id)
    return TelegramMessageReceivedEvent(chat_id=1001001001, payload=payload)


def _telegram_message(
    content: str,
    *,
    message_id: int,
    is_self: bool = False,
    sender_id: str = "user-123",
    sender_name: str = "Fixture Sender",
) -> TelegramMessagePayload:
    return TelegramMessagePayload(
        message_id=message_id,
        chat_id=1001001001,
        sender=TelegramSenderPayload(id="amber-self" if is_self else sender_id, name="amber" if is_self else sender_name, is_self=is_self),
        timestamp=datetime(2026, 4, 21, 3, 54, 5, tzinfo=timezone.utc),
        content=content,
        raw_text=content,
        reply_to_message_id=None,
        reply_to_sender=TelegramReplySenderPayload(),
        mentions=["amber"] if "amber" in content.lower() else [],
        attachment=TelegramAttachmentPayload(),
        transport=TelegramTransportPayload(peer_id=1001001001, raw_chat_id=1001001001, raw_message_id=message_id),
    )
