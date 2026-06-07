from __future__ import annotations

from datetime import timedelta

import pytest

from src.action.config import ActionConfig
from src.action.telegram.layer import ActionLayer
from src.action.telegram.transport import RecordingTransport
from src.events.action import OutboundChunkSentEvent, OutboundMessageSentEvent
from src.events.bus import EventBus
from src.events.outbound import OutboundMessagePreparedEvent, OutboundMessagePreparedPayload
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


def test_action_types_and_sends_each_chunk_sequentially(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.action.telegram.layer.random.uniform",
        lambda low, high: 0.0 if (low, high) == (0.0, 40.0) else low,
    )
    sleep_calls: list[float] = []
    monkeypatch.setattr("src.action.telegram.layer.time.sleep", sleep_calls.append)
    transport = RecordingTransport()
    layer = ActionLayer(
        ActionConfig(
            enable_real_delays=True,
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
    delivered: list[OutboundMessageSentEvent] = []
    chunks: list[OutboundChunkSentEvent] = []
    EventBus.subscribe("OutboundMessageSentEvent", delivered.append)
    EventBus.subscribe("OutboundChunkSentEvent", chunks.append)

    layer.handle_prepared_message(
        OutboundMessagePreparedEvent(
            chat_id=1001001001,
            payload=OutboundMessagePreparedPayload(
                chat_id=1001001001,
                session_id="sess_chunks",
                trigger_message_id=412,
                ordered_messages=["first chunk", "second chunk", "third chunk"],
                reply_to_message_id=411,
                mood="calm",
                raw_output="first chunk\nsecond chunk\nthird chunk",
                no_send=False,
            ),
        )
    )

    assert [record.ordered_messages for record in transport.records] == [["first chunk"], ["second chunk"], ["third chunk"]]
    assert [record.reply_to_message_id for record in transport.records] == [411, None, None]
    assert all(record.typing_durations and record.typing_durations[0] > 0 for record in transport.records)
    assert [(chunk.payload.chunk_index, chunk.payload.sent_message_id) for chunk in chunks] == [
        (1, 900001),
        (2, 900002),
        (3, 900003),
    ]
    assert all(chunk.payload.typing_duration_seconds > 0 for chunk in chunks)
    assert delivered
    assert delivered[-1].payload.sent_message_ids == [900001, 900002, 900003]
    assert sleep_calls == [0.5, 0.5]


def test_typing_duration_adds_only_negative_wpm_variance(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    layer = ActionLayer(
        ActionConfig(
            enable_real_delays=False,
            disable_sleep_state=True,
            transport_max_retries=1,
            transport_retry_delay_seconds=2.0,
        ),
        RecordingTransport(),
        GlobalStateStore(tmp_path / "runtime_state.json", "America/Managua"),
        RuntimeScheduler.instance(),
        MessageArchive.instance(),
        "America/Managua",
    )
    message = "This sentence has enough words to show the typing variance clearly."
    monkeypatch.setattr("src.action.telegram.layer.random.uniform", lambda _low, _high: 0.0)
    baseline = layer._typing_duration_seconds(message)
    monkeypatch.setattr("src.action.telegram.layer.random.uniform", lambda _low, _high: 40.0)
    slower = layer._typing_duration_seconds(message)

    assert slower >= baseline


def test_typing_duration_respects_configured_baseline_wpm(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr("src.action.telegram.layer.random.uniform", lambda _low, _high: 0.0)
    faster_layer = ActionLayer(
        ActionConfig(
            enable_real_delays=False,
            disable_sleep_state=True,
            transport_max_retries=1,
            transport_retry_delay_seconds=2.0,
            typing_baseline_wpm=180.0,
        ),
        RecordingTransport(),
        GlobalStateStore(tmp_path / "runtime_state_fast.json", "America/Managua"),
        RuntimeScheduler.instance(),
        MessageArchive.instance(),
        "America/Managua",
    )
    slower_layer = ActionLayer(
        ActionConfig(
            enable_real_delays=False,
            disable_sleep_state=True,
            transport_max_retries=1,
            transport_retry_delay_seconds=2.0,
            typing_baseline_wpm=90.0,
        ),
        RecordingTransport(),
        GlobalStateStore(tmp_path / "runtime_state_slow.json", "America/Managua"),
        RuntimeScheduler.instance(),
        MessageArchive.instance(),
        "America/Managua",
    )
    message = "This sentence has enough words to show the configured baseline speed clearly."

    assert faster_layer._typing_duration_seconds(message) < slower_layer._typing_duration_seconds(message)


def test_typing_duration_non_alphanumeric_penalty_respects_30_wpm_floor(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr("src.action.telegram.layer.random.uniform", lambda _low, _high: 40.0)
    layer = ActionLayer(
        ActionConfig(
            enable_real_delays=False,
            disable_sleep_state=True,
            transport_max_retries=1,
            transport_retry_delay_seconds=2.0,
            typing_baseline_wpm=135.0,
        ),
        RecordingTransport(),
        GlobalStateStore(tmp_path / "runtime_state_floor.json", "America/Managua"),
        RuntimeScheduler.instance(),
        MessageArchive.instance(),
        "America/Managua",
    )
    message = "!!! ??? ::: --- [[[ ]]]"
    chars = max(len(message.replace(" ", "")), 1)
    word_estimate = max(chars / 5.0, 1.0)
    expected = max(min((word_estimate / 30.0) * 60.0, 12.0), 0.4)

    assert layer._typing_duration_seconds(message) == pytest.approx(expected)


def test_inter_chunk_delay_uses_next_message_length_and_cap(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr("src.action.telegram.layer.random.uniform", lambda _low, _high: 0.75)
    layer = ActionLayer(
        ActionConfig(
            enable_real_delays=False,
            disable_sleep_state=True,
            transport_max_retries=1,
            transport_retry_delay_seconds=2.0,
            inter_chunk_delay_min_seconds=0.5,
            inter_chunk_delay_max_seconds=1.0,
            inter_chunk_delay_length_threshold_chars=50,
            inter_chunk_delay_chars_per_step=5,
            inter_chunk_delay_step_seconds=0.5,
            inter_chunk_delay_total_max_seconds=5.0,
        ),
        RecordingTransport(),
        GlobalStateStore(tmp_path / "runtime_state_pause.json", "America/Managua"),
        RuntimeScheduler.instance(),
        MessageArchive.instance(),
        "America/Managua",
    )

    assert layer._inter_chunk_delay_seconds("x" * 75) == pytest.approx(3.25)
    assert layer._inter_chunk_delay_seconds("x" * 200) == pytest.approx(5.0)


def test_filler_chunk_forces_configurable_non_typing_pause(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.action.telegram.layer.random.uniform",
        lambda low, high: 0.0 if (low, high) == (0.0, 40.0) else low,
    )
    sleep_calls: list[float] = []
    monkeypatch.setattr("src.action.telegram.layer.time.sleep", sleep_calls.append)
    transport = RecordingTransport()
    layer = ActionLayer(
        ActionConfig(
            enable_real_delays=True,
            disable_sleep_state=True,
            transport_max_retries=1,
            transport_retry_delay_seconds=2.0,
            filler_pause_seconds=7.0,
        ),
        transport,
        GlobalStateStore(tmp_path / "runtime_state_filler.json", "America/Managua"),
        RuntimeScheduler.instance(),
        MessageArchive.instance(),
        "America/Managua",
    )

    layer.handle_prepared_message(
        OutboundMessagePreparedEvent(
            chat_id=1001001001,
            payload=OutboundMessagePreparedPayload(
                chat_id=1001001001,
                session_id="sess_filler",
                trigger_message_id=412,
                ordered_messages=["hmmm,", "next message"],
                reply_to_message_id=411,
                mood="calm",
                raw_output="hmmm,\nnext message",
                no_send=False,
            ),
        )
    )

    assert sleep_calls == [7.0]


def test_pre_send_visible_read_delay_shaves_elapsed_model_time(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.action.telegram.layer.random.uniform", lambda _low, _high: 0.0)
    sleep_calls: list[float] = []
    monkeypatch.setattr("src.action.telegram.layer.time.sleep", sleep_calls.append)
    transport = RecordingTransport()
    layer = ActionLayer(
        ActionConfig(
            enable_real_delays=True,
            disable_sleep_state=True,
            transport_max_retries=1,
            transport_retry_delay_seconds=2.0,
        ),
        transport,
        GlobalStateStore(tmp_path / "runtime_state_pre_send.json", "America/Managua"),
        RuntimeScheduler.instance(),
        MessageArchive.instance(),
        "America/Managua",
    )
    now = utc_now()

    layer.handle_prepared_message(
        OutboundMessagePreparedEvent(
            chat_id=1001001001,
            payload=OutboundMessagePreparedPayload(
                chat_id=1001001001,
                session_id="sess_pre_send",
                trigger_message_id=412,
                ordered_messages=["single reply"],
                reply_to_message_id=411,
                mood="calm",
                raw_output="single reply",
                no_send=False,
                frame_created_at=now - timedelta(seconds=3),
                visible_read_not_before=now + timedelta(seconds=2),
                visible_surfaced_message_ids=[412],
                visible_surfaced_until_message_id=412,
                visible_read_through_message_id=412,
            ),
        )
    )

    assert sleep_calls == [pytest.approx(2.0, abs=0.15)]
    assert [(item.chat_id, item.read_through_message_id) for item in transport.read_records] == [(1001001001, 412)]


def test_archived_self_messages_only_reply_on_first_chunk(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.action.telegram.layer.random.uniform", lambda _low, _high: 0.0)
    archive = MessageArchive.instance()
    layer = ActionLayer(
        ActionConfig(
            enable_real_delays=True,
            disable_sleep_state=True,
            transport_max_retries=1,
            transport_retry_delay_seconds=2.0,
        ),
        RecordingTransport(),
        GlobalStateStore(tmp_path / "runtime_state.json", "America/Managua"),
        RuntimeScheduler.instance(),
        archive,
        "America/Managua",
    )

    layer.handle_prepared_message(
        OutboundMessagePreparedEvent(
            chat_id=1001001001,
            payload=OutboundMessagePreparedPayload(
                chat_id=1001001001,
                session_id="sess_archive",
                trigger_message_id=412,
                ordered_messages=["before", "after"],
                reply_to_message_id=411,
                mood="calm",
                raw_output="before\nafter",
                no_send=False,
            ),
        )
    )

    first = archive.get(1001001001, 900001)
    second = archive.get(1001001001, 900002)

    assert first is not None and first.reply_to_message_id == 411
    assert second is not None and second.reply_to_message_id is None
