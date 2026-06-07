from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.action.config import ActionConfig
from src.action.telegram.layer import ActionLayer
from src.action.telegram.transport import RecordingTransport
from src.events.action import OutboundMessageSentEvent
from src.events.bus import EventBus
from src.events.outbound import OutboundMessagePreparedEvent, OutboundMessagePreparedPayload
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


@pytest.fixture(autouse=True)
def reset_runtime_singletons() -> None:
    EventBus.reset_for_tests()
    MessageArchive.instance().reset()
    RuntimeScheduler.instance().shutdown()
    yield
    EventBus.reset_for_tests()
    MessageArchive.instance().reset()
    RuntimeScheduler.instance().shutdown()


def test_same_user_interrupt_pauses_batch_and_persists_remaining_plan(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.action.telegram.layer.random.uniform",
        lambda low, high: 0.0 if (low, high) == (0.0, 40.0) else low,
    )
    archive = MessageArchive.instance()
    archive.put(_telegram_message("teach me a bit about sfinae", message_id=411))
    state_store = GlobalStateStore(tmp_path / "runtime_state.json", "America/Managua")
    transport = RecordingTransport()
    layer = ActionLayer(
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
    delivered: list[OutboundMessageSentEvent] = []
    EventBus.subscribe("OutboundMessageSentEvent", delivered.append)
    original_send_typing = transport.send_typing
    interrupt_inserted = {"value": False}

    def send_typing(chat_id: int | str, duration_seconds: float) -> None:
        original_send_typing(chat_id, duration_seconds)
        if interrupt_inserted["value"]:
            return
        archive.put(
            _telegram_message(
                "wait, keep it short",
                message_id=412,
                reply_to_message_id=411,
                reply_to_sender_id="user-123",
                reply_to_sender_name="Fixture Sender",
            )
        )
        interrupt_inserted["value"] = True

    monkeypatch.setattr(transport, "send_typing", send_typing)

    layer.handle_prepared_message(
        OutboundMessagePreparedEvent(
            chat_id=1001001001,
            payload=OutboundMessagePreparedPayload(
                chat_id=1001001001,
                session_id="sess_interrupt",
                trigger_message_id=411,
                ordered_messages=["first chunk", "second chunk", "third chunk"],
                reply_to_message_id=411,
                mood="calm",
                raw_output="first chunk\nsecond chunk\nthird chunk",
                no_send=False,
            ),
        )
    )

    assert [record.ordered_messages for record in transport.records] == [["first chunk"]]
    pending = state_store.snapshot().pending_interruption
    assert pending is not None
    assert pending.interrupting_message_id == 412
    assert pending.sent_reply_chunks == ["first chunk"]
    assert pending.remaining_reply_chunks == ["second chunk", "third chunk"]
    assert delivered
    assert delivered[-1].payload.ordered_messages == ["first chunk"]
    assert delivered[-1].payload.sent_message_ids == [900001]
    assert delivered[-1].payload.planned_message_count == 3
    assert delivered[-1].payload.interrupted is True
    assert delivered[-1].payload.interruption_message_id == 412


def test_messages_arriving_between_chunks_do_not_pause_batch(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.action.telegram.layer.random.uniform",
        lambda low, high: 0.0 if (low, high) == (0.0, 40.0) else low,
    )
    archive = MessageArchive.instance()
    archive.put(_telegram_message("teach me a bit about sfinae", message_id=411))
    state_store = GlobalStateStore(tmp_path / "runtime_state.json", "America/Managua")
    transport = RecordingTransport()
    layer = ActionLayer(
        ActionConfig(
            enable_real_delays=True,
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
    delay_inserted = {"value": False}

    def apply_real_delay(duration_seconds: float) -> None:
        if delay_inserted["value"]:
            return
        archive.put(
            _telegram_message(
                "another normal follow up",
                message_id=412,
                reply_to_message_id=411,
                reply_to_sender_id="user-123",
                reply_to_sender_name="Fixture Sender",
            )
        )
        delay_inserted["value"] = True

    monkeypatch.setattr(layer, "_apply_real_delay", apply_real_delay)

    layer.handle_prepared_message(
        OutboundMessagePreparedEvent(
            chat_id=1001001001,
            payload=OutboundMessagePreparedPayload(
                chat_id=1001001001,
                session_id="sess_no_interrupt",
                trigger_message_id=411,
                ordered_messages=["first chunk", "second chunk"],
                reply_to_message_id=411,
                mood="calm",
                raw_output="first chunk\nsecond chunk",
                no_send=False,
            ),
        )
    )

    assert [record.ordered_messages for record in transport.records] == [["first chunk"], ["second chunk"]]
    assert state_store.snapshot().pending_interruption is None


def _telegram_message(
    content: str,
    *,
    message_id: int,
    reply_to_message_id: int | None = None,
    reply_to_sender_id: str | None = None,
    reply_to_sender_name: str | None = None,
) -> TelegramMessagePayload:
    return TelegramMessagePayload(
        message_id=message_id,
        chat_id=1001001001,
        sender=TelegramSenderPayload(id="user-123", name="Fixture Sender"),
        timestamp=datetime(2026, 4, 21, 7, 5 + (message_id - 411), 0, tzinfo=timezone.utc),
        content=content,
        raw_text=content,
        reply_to_message_id=reply_to_message_id,
        reply_to_sender=TelegramReplySenderPayload(id=reply_to_sender_id, name=reply_to_sender_name),
        mentions=["amber"] if "amber" in content.lower() else [],
        attachment=TelegramAttachmentPayload(),
        transport=TelegramTransportPayload(peer_id=1001001001, raw_chat_id=1001001001, raw_message_id=message_id),
    )
