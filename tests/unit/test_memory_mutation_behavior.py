from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.attention.memory.store import MemoryStore
from src.context.config import ContextConfig
from src.context.pipeline import ContextLayer
from src.context.session.store import ConversationSession
from src.events.ai import SemanticDecisionMadeEvent, SemanticDecisionPayload
from src.events.bus import EventBus
from src.events.context import ContextFrameMessagePayload, ContextFrameReadyEvent
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


def test_reply_can_rewrite_memory_for_exact_profile(tmp_path) -> None:
    context_layer, memory_store = _build_context_layer(tmp_path)
    original_entry = memory_store.write_bad_memory(
        "user-123",
        "Fixture Sender",
        "Fixture Sender was hostile and kept trying to start fights.",
        source_message_id=401,
    )
    memory_card = memory_store.expand("user-123", "Fixture Sender", [original_entry.memory_id])[0]
    session = ConversationSession(
        session_id="sess_rewrite",
        chat_id=1001001001,
        last_updated_at=utc_now(),
        recent_messages=[_message(501, "user-123", "Fixture Sender", "look, i know i was annoying earlier")],
        participant_names={"user-123": "Fixture Sender"},
        engaged_user_ids={"user-123"},
        latest_trigger_message_id=501,
        memory_cards={memory_card.memory_id: memory_card},
    )
    context_layer._active_session = session

    context_layer.handle_semantic_decision(
        SemanticDecisionMadeEvent(
            chat_id=1001001001,
            payload=SemanticDecisionPayload(
                action="reply",
                chat_id=1001001001,
                session_id=session.session_id,
                trigger_message_id=501,
                reply_text="fair enough",
                confidence=0.8,
                memory_mutation="rewrite",
                target_memory_id=memory_card.memory_id,
                target_memory_sender_id="user-123",
                rewritten_memory_text="Fixture Sender used to be combative, but later apologized and cooled off.",
                rewritten_memory_tags=["past_conflict", "apology", "cooled_off"],
            ),
        )
    )

    rewritten_entry = memory_store._iter_memories("user-123", "Fixture Sender")[-1]

    assert rewritten_entry.memory_id == memory_card.memory_id
    assert rewritten_entry.text == "Fixture Sender used to be combative, but later apologized and cooled off."
    assert rewritten_entry.tags == ["past_conflict", "apology", "cooled_off"]
    assert session.memory_cards[memory_card.memory_id].owner_sender_id == "user-123"
    assert session.memory_cards[memory_card.memory_id].updated_at == rewritten_entry.updated_at


def test_ignore_can_forget_memory_for_exact_profile(tmp_path) -> None:
    context_layer, memory_store = _build_context_layer(tmp_path)
    primary_entry = memory_store.write_bad_memory(
        "user-123",
        "Fixture Sender",
        "Fixture Sender kept dragging the chat into pointless fights.",
        source_message_id=410,
    )
    peer_entry = memory_store.write_bad_memory(
        "user-456",
        "Fixture Peer",
        "Fixture Peer likes clean code and short answers.",
        source_message_id=411,
    )
    primary_card = memory_store.expand("user-123", "Fixture Sender", [primary_entry.memory_id])[0]
    session = ConversationSession(
        session_id="sess_forget",
        chat_id=1001001001,
        last_updated_at=utc_now(),
        recent_messages=[_message(502, "user-123", "Fixture Sender", "thanks for letting that go")],
        participant_names={"user-123": "Fixture Sender", "user-456": "Fixture Peer"},
        engaged_user_ids={"user-123", "user-456"},
        latest_trigger_message_id=502,
        memory_cards={primary_card.memory_id: primary_card},
        expanded_memory_ids={primary_card.memory_id},
    )
    context_layer._active_session = session

    context_layer.handle_semantic_decision(
        SemanticDecisionMadeEvent(
            chat_id=1001001001,
            payload=SemanticDecisionPayload(
                action="ignore",
                chat_id=1001001001,
                session_id=session.session_id,
                trigger_message_id=502,
                confidence=0.65,
                memory_mutation="forget",
                target_memory_id=primary_card.memory_id,
                target_memory_sender_id="user-123",
            ),
        )
    )

    assert memory_store._iter_memories("user-123", "Fixture Sender") == []
    assert memory_store._iter_memories("user-456", "Fixture Peer")[-1].memory_id == peer_entry.memory_id
    assert primary_card.memory_id not in session.memory_cards
    assert primary_card.memory_id not in session.expanded_memory_ids


def test_expand_memory_uses_memory_owner_from_card_not_latest_trigger_sender(tmp_path) -> None:
    context_layer, memory_store = _build_context_layer(tmp_path)
    entry = memory_store.write_bad_memory(
        "user-123",
        "Fixture Sender",
        "Fixture Sender had a rough patch months ago, but it eventually settled down.",
        source_message_id=420,
    )
    primary_card = memory_store.expand("user-123", "Fixture Sender", [entry.memory_id])[0]
    session = ConversationSession(
        session_id="sess_expand_owner",
        chat_id=1001001001,
        last_updated_at=utc_now(),
        recent_messages=[_message(503, "user-456", "Fixture Peer", "what do you think now?")],
        participant_names={"user-123": "Fixture Sender", "user-456": "Fixture Peer"},
        engaged_user_ids={"user-123", "user-456"},
        latest_trigger_message_id=503,
        memory_cards={primary_card.memory_id: primary_card},
    )
    context_layer._active_session = session
    frames: list[ContextFrameReadyEvent] = []
    EventBus.subscribe("ContextFrameReadyEvent", frames.append)

    context_layer.handle_semantic_decision(
        SemanticDecisionMadeEvent(
            chat_id=1001001001,
            payload=SemanticDecisionPayload(
                action="expand_memory",
                chat_id=1001001001,
                session_id=session.session_id,
                trigger_message_id=503,
                confidence=0.7,
                referenced_memory_ids=[primary_card.memory_id],
            ),
        )
    )

    assert primary_card.memory_id in session.expanded_memory_ids
    assert frames
    expanded_card = next(memory for memory in frames[-1].payload.relevant_memories if memory.memory_id == primary_card.memory_id)
    assert expanded_card.owner_sender_id == "user-123"
    assert expanded_card.owner_sender_name == "Fixture Sender"
    assert expanded_card.created_at is not None
    assert expanded_card.updated_at is not None


def _build_context_layer(tmp_path) -> tuple[ContextLayer, MemoryStore]:
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
        GlobalStateStore(tmp_path / "runtime_state.json", "America/Managua"),
        RuntimeScheduler.instance(),
        MessageArchive.instance(),
        memory_store,
        "America/Managua",
    )
    return context_layer, memory_store


def _message(message_id: int, sender_id: str, sender_name: str, content: str) -> ContextFrameMessagePayload:
    return ContextFrameMessagePayload(
        message_id=message_id,
        sender_id=sender_id,
        sender_name=sender_name,
        content=content,
        timestamp=datetime(2026, 4, 21, 4, message_id - 500, 0, tzinfo=timezone.utc),
    )
