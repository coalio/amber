from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.attention.memory.store import MemoryStore
from src.context.config import ContextConfig
from src.context.pipeline import ContextLayer
from src.context.session.store import ConversationSession
from src.events.context import ContextFrameMessagePayload
from src.events.receiver import (
    TelegramAttachmentPayload,
    TelegramMessagePayload,
    TelegramReplySenderPayload,
    TelegramSenderPayload,
    TelegramTransportPayload,
)
from src.state.store import GlobalStateStore
from src.utils.message_archive import MessageArchive
from src.utils.scheduler import RuntimeScheduler
from src.utils.time import utc_now


@pytest.fixture(autouse=True)
def reset_runtime_singletons() -> None:
    MessageArchive.instance().reset()
    RuntimeScheduler.instance().shutdown()
    yield
    MessageArchive.instance().reset()
    RuntimeScheduler.instance().shutdown()


def test_context_frame_includes_conversation_window_and_engages_window_participants(tmp_path) -> None:
    archive = MessageArchive.instance()
    context_layer = ContextLayer(
        ContextConfig(
            debounce_seconds=0.0,
            idle_timeout_seconds=60.0,
            competing_chat_timeout_seconds=15.0,
            recent_message_budget=8,
            max_compacted_facts=6,
            disable_sleep_state=True,
            conversation_window_before=2,
            conversation_window_after=2,
        ),
        GlobalStateStore(tmp_path / "runtime_state.json", "America/Managua"),
        RuntimeScheduler.instance(),
        archive,
        MemoryStore(tmp_path / "memories"),
        "America/Managua",
    )

    for payload in [
        _telegram_message(410, "user-a", "Fixture Origin", "earlier context"),
        _telegram_message(411, "amber-self", "amber", "my earlier reply", is_self=True),
        _telegram_message(
            412,
            "user-b",
            "Fixture Sender",
            "trigger message",
            reply_to_message_id=411,
            reply_to_sender_id="amber-self",
            reply_to_sender_name="amber",
            reply_to_content="my earlier reply",
        ),
        _telegram_message(413, "user-c", "Fixture Peer", "follow-up one"),
        _telegram_message(414, "user-a", "Fixture Origin", "follow-up two", reply_to_message_id=412, reply_to_sender_id="user-b", reply_to_sender_name="Fixture Sender"),
    ]:
        archive.put(payload)

    trigger = ContextFrameMessagePayload(
        message_id=412,
        sender_id="user-b",
        sender_name="Fixture Sender",
        content="trigger message",
        timestamp=datetime(2026, 4, 21, 3, 56, 0, tzinfo=timezone.utc),
        reply_to_message_id=411,
        reply_to_sender_id="amber-self",
        reply_to_sender_name="amber",
        reply_to_content="my earlier reply",
        source="surface",
    )
    session = ConversationSession(
        session_id="sess_window",
        chat_id=1001001001,
        last_updated_at=utc_now(),
        recent_messages=[trigger],
        participant_names={"user-b": "Fixture Sender"},
        engaged_user_ids={"user-b"},
        latest_trigger_message_id=412,
    )

    context_layer._engage_conversation_window_participants(session, session.chat_id, 412)
    frame = context_layer._build_frame_event(session)

    assert frame.payload.current_message.message_id == 412
    assert [message.message_id for message in frame.payload.conversation_window_messages] == [410, 411, 412, 413, 414]
    assert frame.payload.conversation_window_messages[2].reply_to_message_id == 411
    assert frame.payload.conversation_window_messages[2].reply_to_sender_name == "amber"
    assert frame.payload.conversation_window_messages[2].reply_to_content == "my earlier reply"
    assert frame.payload.conversation_window_messages[1].is_self is True
    assert set(frame.payload.engaged_user_ids) == {"amber-self", "user-a", "user-b", "user-c"}


def _telegram_message(
    message_id: int,
    sender_id: str,
    sender_name: str,
    content: str,
    *,
    is_self: bool = False,
    reply_to_message_id: int | None = None,
    reply_to_sender_id: str | None = None,
    reply_to_sender_name: str | None = None,
    reply_to_content: str | None = None,
) -> TelegramMessagePayload:
    return TelegramMessagePayload(
        message_id=message_id,
        chat_id=1001001001,
        sender=TelegramSenderPayload(id=sender_id, name=sender_name, is_self=is_self),
        timestamp=datetime(2026, 4, 21, 3, 50 + (message_id - 410), 0, tzinfo=timezone.utc),
        content=content,
        raw_text=content,
        reply_to_message_id=reply_to_message_id,
        reply_to_sender=TelegramReplySenderPayload(id=reply_to_sender_id, name=reply_to_sender_name),
        reply_to_content=reply_to_content,
        reply_to_raw_text=reply_to_content,
        mentions=[],
        attachment=TelegramAttachmentPayload(),
        transport=TelegramTransportPayload(peer_id=1001001001, raw_chat_id=1001001001, raw_message_id=message_id),
    )
