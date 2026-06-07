from __future__ import annotations

import asyncio
import threading
from datetime import datetime, UTC
from types import SimpleNamespace

import pytest

from src.events.bus import EventBus
from src.events.receiver import (
    TelegramAttachmentPayload,
    TelegramMessagePayload,
    TelegramMessageReceivedEvent,
    TelegramReplySenderPayload,
    TelegramSenderPayload,
    TelegramTransportPayload,
)
from src.action.telegram.transport import RecordingTransport
from src.receiver.telegram.receiver import TelegramReceiver
from src.state.store import GlobalStateStore
from src.utils.message_archive import MessageArchive


@pytest.fixture(autouse=True)
def reset_runtime_singletons() -> None:
    EventBus.reset_for_tests()
    MessageArchive.instance().reset()
    yield
    EventBus.reset_for_tests()
    MessageArchive.instance().reset()


def test_receiver_dispatches_event_bus_off_telegram_loop_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    archive = MessageArchive.instance()
    receiver = TelegramReceiver(client=object(), message_archive=archive)
    normalized = TelegramMessageReceivedEvent(
        chat_id=1001001001,
        payload=TelegramMessagePayload(
            message_id=1108,
            chat_id=1001001001,
            sender=TelegramSenderPayload(id="1001001001", name="Fixture User"),
            timestamp=datetime.now(UTC),
            content="hey amber",
            raw_text="hey amber",
            reply_to_sender=TelegramReplySenderPayload(),
            attachment=TelegramAttachmentPayload(),
            transport=TelegramTransportPayload(peer_id=1001001001, raw_chat_id=1001001001, raw_message_id=1108),
        ),
    )

    async def fake_normalize(_message: object) -> TelegramMessageReceivedEvent:
        return normalized

    emit_thread_ids: list[int] = []
    archive_visible_during_emit: list[bool] = []

    def fake_emit(event: TelegramMessageReceivedEvent) -> None:
        emit_thread_ids.append(threading.get_ident())
        archive_visible_during_emit.append(archive.get(event.payload.chat_id, event.payload.message_id) is not None)

    monkeypatch.setattr(receiver, "normalize_message", fake_normalize)
    monkeypatch.setattr(EventBus, "emit", fake_emit)

    loop_thread_id: int | None = None

    async def run_receiver() -> None:
        nonlocal loop_thread_id
        loop_thread_id = threading.get_ident()
        await receiver._on_new_message(SimpleNamespace(message=object()))

    asyncio.run(run_receiver())

    assert loop_thread_id is not None
    assert emit_thread_ids
    assert len(emit_thread_ids) == 1
    assert emit_thread_ids[0] != loop_thread_id
    assert archive_visible_during_emit == [True]


def test_receiver_marks_active_chat_read_before_event_bus_dispatch(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    archive = MessageArchive.instance()
    state_store = GlobalStateStore(tmp_path / "runtime_state.json", "America/Managua")
    state_store.update_context_state(
        active_chat_id=1001001001,
        active_session_id="sess_active",
        last_engagement_at=datetime.now(UTC),
        conversation_engaged_user_ids=["1001001001"],
    )
    transport = RecordingTransport()
    receiver = TelegramReceiver(client=object(), message_archive=archive, state_store=state_store, transport=transport)
    normalized = TelegramMessageReceivedEvent(
        chat_id=1001001001,
        payload=TelegramMessagePayload(
            message_id=1108,
            chat_id=1001001001,
            sender=TelegramSenderPayload(id="1001001001", name="Fixture User"),
            timestamp=datetime.now(UTC),
            content="hey amber",
            raw_text="hey amber",
            reply_to_sender=TelegramReplySenderPayload(),
            attachment=TelegramAttachmentPayload(),
            transport=TelegramTransportPayload(peer_id=1001001001, raw_chat_id=1001001001, raw_message_id=1108),
        ),
    )

    async def fake_normalize(_message: object) -> TelegramMessageReceivedEvent:
        return normalized

    read_visible_during_emit: list[bool] = []

    def fake_emit(event: TelegramMessageReceivedEvent) -> None:
        read_visible_during_emit.append(
            [(item.chat_id, item.read_through_message_id) for item in transport.read_records] == [(1001001001, 1108)]
        )

    monkeypatch.setattr(receiver, "normalize_message", fake_normalize)
    monkeypatch.setattr(EventBus, "emit", fake_emit)

    async def run_receiver() -> None:
        await receiver._on_new_message(SimpleNamespace(message=object()))

    asyncio.run(run_receiver())

    assert [(item.chat_id, item.read_through_message_id) for item in transport.read_records] == [(1001001001, 1108)]
    assert read_visible_during_emit == [True]
