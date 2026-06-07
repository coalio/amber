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

    layer.handle_semantic_decision(_reply_event(draft_text="first thought\nsecond thought\n\nthird thought"))

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
        _reply_event(draft_text="before\n```python\nprint('hi')\nprint('bye')\n```\nafter this line should wrap")
    )

    assert capture
    ordered_messages = capture[-1].payload.ordered_messages
    assert ordered_messages[:2] == ["before", "```python\nprint('hi')\nprint('bye')\n```"]
    assert " ".join(ordered_messages[2:]) == "after this line should wrap"
    assert all(len(message) <= 12 for message in ordered_messages[2:])


def test_codex_draft_rewrite_removes_relay_framing(tmp_path) -> None:
    capture: list[OutboundMessagePreparedEvent] = []
    EventBus.subscribe("OutboundMessagePreparedEvent", capture.append)
    layer = _build_layer(tmp_path)

    layer.handle_semantic_decision(
        _reply_event(
            draft_text=(
                "Got it - What architecture do you prefer so I can let Codex know?\n"
                "I'll send this to Codex."
            ),
            codex=True,
        )
    )

    assert capture
    output = "\n".join(capture[-1].payload.ordered_messages)
    assert "codex" not in output
    assert "got it" not in output
    assert output == "what architecture do you prefer?\ni'll keep going"


def test_outbound_preparation_layer_preserves_first_person_direction(tmp_path) -> None:
    capture: list[OutboundMessagePreparedEvent] = []
    EventBus.subscribe("OutboundMessagePreparedEvent", capture.append)
    layer = _build_layer(tmp_path)

    layer.handle_semantic_decision(_reply_event(draft_text="running it now. i'll send you the output once it finishes."))

    assert capture
    output = " ".join(capture[-1].payload.ordered_messages)
    assert "i'll send you the output" in output
    assert "send me the output" not in output


def _build_layer(tmp_path, *, max_chunk_chars: int = 220) -> OutboundPreparationLayer:
    state_store = GlobalStateStore(tmp_path / "runtime_state.json", "America/Managua")
    return OutboundPreparationLayer(
        OutboundPreparationConfig(max_chunk_chars=max_chunk_chars),
        state_store,
    )


def _reply_event(*, draft_text: str = "draft", codex: bool = False) -> SemanticDecisionMadeEvent:
    return SemanticDecisionMadeEvent(
        chat_id=1001001001,
        payload=SemanticDecisionPayload(
            action="reply",
            chat_id=1001001001,
            draft_text=draft_text,
            reply_to_message_id=411,
            confidence=0.82,
            trigger_message_id=412,
            session_id="session_debug",
            codex_app_server_id="codex-sandbox" if codex else None,
            codex_task_id="task_1" if codex else None,
            codex_tool_call_id="tool_1" if codex else None,
        ),
    )
