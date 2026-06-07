from __future__ import annotations

import pytest

from src.events.ai import SemanticDecisionMadeEvent, SemanticDecisionPayload
from src.events.bus import EventBus
from src.events.outbound import OutboundMessagePreparedEvent
from src.outbound.config import OutboundPreparationConfig
from src.outbound.layer import OutboundPreparationLayer
from src.state.store import GlobalStateStore


@pytest.fixture(autouse=True)
def reset_event_bus() -> None:
    EventBus.reset_for_tests()
    yield
    EventBus.reset_for_tests()


def test_outbound_preparation_layer_emits_one_message_per_non_empty_line(tmp_path) -> None:
    capture: list[OutboundMessagePreparedEvent] = []
    EventBus.subscribe("OutboundMessagePreparedEvent", capture.append)
    layer = _build_layer(tmp_path)

    layer.handle_semantic_decision(_reply_event(reply_text="first thought\nsecond thought\n\nthird thought"))

    # preserve paragraphs; drop empty spacer lines
    assert capture
    assert capture[-1].payload.ordered_messages == ["first thought", "second thought", "third thought"]


def test_outbound_preparation_layer_keeps_fenced_code_blocks_together_and_wraps_long_lines(tmp_path) -> None:
    capture: list[OutboundMessagePreparedEvent] = []
    EventBus.subscribe("OutboundMessagePreparedEvent", capture.append)
    layer = _build_layer(
        tmp_path,
        max_chunk_chars=12,
    )

    layer.handle_semantic_decision(
        _reply_event(reply_text="before\n```python\nprint('hi')\nprint('bye')\n```\nafter this line should wrap")
    )

    assert capture
    ordered_messages = capture[-1].payload.ordered_messages
    # keep fenced code atomic; chunk prose only
    assert ordered_messages[:2] == ["before", "```python\nprint('hi')\nprint('bye')\n```"]
    assert " ".join(ordered_messages[2:]) == "after this line should wrap"
    assert all(len(message) <= 12 for message in ordered_messages[2:])


def test_codex_tagged_replies_use_standard_outbound_contract(tmp_path) -> None:
    capture: list[OutboundMessagePreparedEvent] = []
    EventBus.subscribe("OutboundMessagePreparedEvent", capture.append)
    layer = _build_layer(tmp_path)
    reply_text = "Status update ready.\nNext step is queued."

    layer.handle_semantic_decision(_reply_event(reply_text=reply_text))
    layer.handle_semantic_decision(_reply_event(reply_text=reply_text, codex=True))

    normal_payload = capture[-2].payload
    codex_payload = capture[-1].payload
    # codex metadata is routing context only
    assert codex_payload.no_send is False
    assert codex_payload.raw_reply_text == normal_payload.raw_reply_text
    assert codex_payload.ordered_messages == normal_payload.ordered_messages
    assert all(isinstance(message, str) and message for message in codex_payload.ordered_messages)


def _build_layer(tmp_path, *, max_chunk_chars: int = 220) -> OutboundPreparationLayer:
    state_store = GlobalStateStore(tmp_path / "runtime_state.json", "America/Managua")
    return OutboundPreparationLayer(
        OutboundPreparationConfig(max_chunk_chars=max_chunk_chars),
        state_store,
    )


def _reply_event(*, reply_text: str = "reply", codex: bool = False) -> SemanticDecisionMadeEvent:
    return SemanticDecisionMadeEvent(
        chat_id=1001001001,
        payload=SemanticDecisionPayload(
            action="reply",
            chat_id=1001001001,
            reply_text=reply_text,
            reply_to_message_id=411,
            confidence=0.82,
            trigger_message_id=412,
            session_id="session_debug",
            codex_app_server_id="codex-sandbox" if codex else None,
            codex_task_id="task_1" if codex else None,
            codex_tool_call_id="tool_1" if codex else None,
        ),
    )
