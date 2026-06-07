from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.ai.semantic.client import SemanticModelClient
from src.ai.semantic.config import SemanticConfig
from src.ai.semantic.schema import (
    InterruptionDecisionSchema,
    SemanticDecisionSchema,
)
from src.events.context import (
    ContextFrameMessagePayload,
    ContextFramePayload,
    LinearTaskListFramePayload,
    PendingInterruptionPayload,
)
from src.tools.base import BaseTool
from src.tools.get_tool import GetTool
from src.tools.registry import ToolRegistry, default_tool_registry


class RecordingProvider:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._next_id = 0

    def generate_structured(
        self,
        *,
        model: str,
        instructions: str,
        input_items: list[dict],
        schema,
        max_output_tokens: int,
        temperature: float,
        reasoning_effort: str | None = None,
        tools=None,
    ):
        self.calls.append(
            {
                "model": model,
                "instructions": instructions,
                "input_items": input_items,
                "schema": schema,
                "max_output_tokens": max_output_tokens,
                "temperature": temperature,
                "reasoning_effort": reasoning_effort,
                "tools": tools,
            }
        )
        if schema is InterruptionDecisionSchema:
            return InterruptionDecisionSchema(
                interrupt_decision="accept",
                action="reply",
                reply_to_message_id=413,
                draft_text="yeah exactly, and the short version is overload selection by substitution failure.",
                referenced_memory_ids=[],
                confidence=0.9,
                reason="Steering message changed the reply plan.",
                notes=["redirect"],
            )
        return SemanticDecisionSchema(
            action="ignore",
            reply_to_message_id=None,
            chat_id=1001001001,
            draft_text=None,
            referenced_memory_ids=[],
            confidence=0.2,
            notes=[],
            trigger_message_id=None,
            session_id=None,
        )

    def generate_structured_with_metadata(
        self,
        *,
        model: str,
        instructions: str,
        input_items: list[dict],
        schema,
        max_output_tokens: int,
        temperature: float,
        reasoning_effort: str | None = None,
        previous_response_id: str | None = None,
        tools=None,
    ):
        self._next_id += 1
        self.calls.append(
            {
                "model": model,
                "instructions": instructions,
                "input_items": input_items,
                "schema": schema,
                "max_output_tokens": max_output_tokens,
                "temperature": temperature,
                "reasoning_effort": reasoning_effort,
                "previous_response_id": previous_response_id,
                "tools": tools,
            }
        )
        if schema is InterruptionDecisionSchema:
            return (
                InterruptionDecisionSchema(
                    interrupt_decision="accept",
                    action="reply",
                    reply_to_message_id=413,
                    draft_text="yeah exactly, and the short version is overload selection by substitution failure.",
                    referenced_memory_ids=[],
                    confidence=0.9,
                    reason="Steering message changed the reply plan.",
                    notes=["redirect"],
                ),
                f"resp_{self._next_id}",
            )
        return (
            SemanticDecisionSchema(
                action="ignore",
                reply_to_message_id=None,
                chat_id=1001001001,
                draft_text=None,
                referenced_memory_ids=[],
                confidence=0.2,
                notes=[],
                trigger_message_id=None,
                session_id=None,
            ),
            f"resp_{self._next_id}",
        )


class CodexStartThenIgnoreProvider:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate_structured(
        self,
        *,
        model: str,
        instructions: str,
        input_items: list[dict],
        schema,
        max_output_tokens: int,
        temperature: float,
        reasoning_effort: str | None = None,
        tools=None,
    ):
        self.calls.append({"tools": tools})
        self._start_codex_task(tools)
        return self._ignore_decision()

    def generate_structured_with_metadata(
        self,
        *,
        model: str,
        instructions: str,
        input_items: list[dict],
        schema,
        max_output_tokens: int,
        temperature: float,
        reasoning_effort: str | None = None,
        previous_response_id: str | None = None,
        tools=None,
    ):
        self.calls.append({"tools": tools, "previous_response_id": previous_response_id})
        self._start_codex_task(tools)
        return self._ignore_decision(), "resp_codex_started"

    def _start_codex_task(self, tools) -> None:
        if tools is None:
            return
        tools.execute("GetTool", {"tool_name": "CodexRunTask"})
        tools.execute(
            "CodexRunTask",
            {
                "task_description": "implement the requested change",
                "context": {"requires_code_editing": True},
            },
        )

    def _ignore_decision(self) -> SemanticDecisionSchema:
        return SemanticDecisionSchema(
            action="ignore",
            reply_to_message_id=None,
            chat_id=1001001001,
            draft_text=None,
            referenced_memory_ids=[],
            confidence=0.2,
            notes=[],
            trigger_message_id=None,
            session_id=None,
        )


class FakeCodexRunTask(BaseTool):
    name = "CodexRunTask"
    description = "Start a fake Codex task."
    arguments = {
        "task_description": {"type": "string"},
        "context": {"type": "object", "properties": {"requires_code_editing": {"type": ["boolean", "null"]}}},
    }
    required_arguments = ("task_description", "context")

    def run(self, arguments: dict[str, Any], session) -> dict[str, str]:
        return {
            "app_server_id": "codex-sandbox",
            "task_id": "task_fake",
            "status": "started",
        }


def test_semantic_client_seeds_ai_system_once_per_session() -> None:
    provider = RecordingProvider()
    client = SemanticModelClient(_config(), provider)

    first_frame = _frame(
        session_id="sess_history",
        trigger_message_id=412,
        messages=[
            _message(411, "amber-self", "amber", "previous amber line", is_self=True),
            _message(
                412,
                "user-123",
                "Fixture Sender",
                "current trigger",
                reply_to_message_id=411,
                reply_to_sender_id="amber-self",
                reply_to_sender_name="amber",
                reply_to_content="previous amber line",
            ),
        ],
    )
    second_frame = _frame(
        session_id="sess_history",
        trigger_message_id=413,
        messages=[
            _message(411, "amber-self", "amber", "previous amber line", is_self=True),
            _message(412, "user-123", "Fixture Sender", "current trigger", reply_to_message_id=411, reply_to_sender_id="amber-self", reply_to_sender_name="amber"),
            _message(413, "user-999", "Fixture Peer", "new follow up"),
        ],
    )

    client.decide(first_frame)
    client.decide(second_frame)

    assert len(provider.calls) == 2
    first_call = provider.calls[0]
    second_call = provider.calls[1]

    assert "selective but human-like participant" not in first_call["instructions"]
    assert first_call["input_items"][0]["role"] == "developer"
    assert "selective but human-like participant" in first_call["input_items"][0]["content"][0]["text"]
    assert first_call["input_items"][1]["role"] == "assistant"
    assert first_call["input_items"][1]["content"][0]["type"] == "output_text"
    assert first_call["input_items"][1]["content"][0]["annotations"] == []
    assert '"reply_to_content": "previous amber line"' in first_call["input_items"][2]["content"][0]["text"]
    assert first_call["previous_response_id"] is None
    assert second_call["previous_response_id"] == "resp_1"
    assert sum(1 for item in second_call["input_items"] if item["role"] == "developer") == 0

    conversation_texts = [item["content"][0]["text"] for item in second_call["input_items"][:-1]]
    assert sum('"message_id": 411' in text for text in conversation_texts) == 0
    assert sum('"message_id": 412' in text for text in conversation_texts) == 0
    assert sum('"message_id": 413' in text for text in conversation_texts) == 1
    assert '"trigger_message_id": 413' in second_call["input_items"][-1]["content"][0]["text"]


def test_semantic_client_uses_interruption_schema_for_interrupt_checks() -> None:
    provider = RecordingProvider()
    client = SemanticModelClient(_config(), provider)
    frame = _frame(
        session_id="sess_history",
        trigger_message_id=413,
        messages=[
            _message(412, "user-123", "Fixture Sender", "teach me sfinae"),
            _message(413, "user-123", "Fixture Sender", "wait, make it short", reply_to_message_id=412),
        ],
    )
    interruption = PendingInterruptionPayload(
        original_trigger_message_id=412,
        original_reply_to_message_id=412,
        interrupting_message_id=413,
        reply_target_sender_id="user-123",
        reply_target_sender_name="Fixture Sender",
        sent_reply_chunks=["first chunk"],
        remaining_reply_chunks=["second chunk", "third chunk"],
    )

    decision = client.decide_interruption(frame, interruption)

    assert decision.interrupt_decision == "accept"
    call = provider.calls[-1]
    assert call["schema"] is InterruptionDecisionSchema
    assert call["input_items"][0]["role"] == "developer"
    assert '"interrupting_message_id": 413' in call["input_items"][-1]["content"][0]["text"]
    assert call["previous_response_id"] is None


def test_semantic_client_reuses_response_chain_for_interruption_checks() -> None:
    provider = RecordingProvider()
    client = SemanticModelClient(_config(), provider)

    first_frame = _frame(
        session_id="sess_history",
        trigger_message_id=412,
        messages=[
            _message(411, "amber-self", "amber", "previous amber line", is_self=True),
            _message(412, "user-123", "Fixture Sender", "current trigger", reply_to_message_id=411, reply_to_sender_id="amber-self", reply_to_sender_name="amber"),
        ],
    )
    interruption_frame = _frame(
        session_id="sess_history",
        trigger_message_id=415,
        messages=[
            _message(411, "amber-self", "amber", "previous amber line", is_self=True),
            _message(412, "user-123", "Fixture Sender", "current trigger", reply_to_message_id=411, reply_to_sender_id="amber-self", reply_to_sender_name="amber"),
            _message(413, "amber-self", "amber", "first chunk", is_self=True),
            _message(414, "amber-self", "amber", "second chunk", is_self=True),
            _message(415, "user-123", "Fixture Sender", "wait, examples instead"),
        ],
    )
    interruption = PendingInterruptionPayload(
        original_trigger_message_id=412,
        original_reply_to_message_id=412,
        interrupting_message_id=415,
        reply_target_sender_id="user-123",
        reply_target_sender_name="Fixture Sender",
        sent_reply_chunks=["first chunk", "second chunk"],
        remaining_reply_chunks=["third chunk"],
    )

    client.decide(first_frame)
    client.decide_interruption(interruption_frame, interruption)

    assert len(provider.calls) == 2
    normal_call = provider.calls[0]
    interruption_call = provider.calls[1]
    assert normal_call["previous_response_id"] is None
    assert interruption_call["schema"] is InterruptionDecisionSchema
    assert interruption_call["previous_response_id"] == "resp_1"
    conversation_texts = [item["content"][0]["text"] for item in interruption_call["input_items"][:-1]]
    assert sum('"message_id": 411' in text for text in conversation_texts) == 0
    assert sum('"message_id": 412' in text for text in conversation_texts) == 0
    assert sum('"message_id": 413' in text for text in conversation_texts) == 1
    assert sum('"message_id": 414' in text for text in conversation_texts) == 1
    assert sum('"message_id": 415' in text for text in conversation_texts) == 1
    assert '"interrupting_message_id": 415' in interruption_call["input_items"][-1]["content"][0]["text"]


def test_semantic_client_includes_interruption_retry_feedback_when_requested() -> None:
    provider = RecordingProvider()
    client = SemanticModelClient(_config(), provider)
    frame = _frame(
        session_id="sess_history",
        trigger_message_id=413,
        messages=[
            _message(412, "user-123", "Fixture Sender", "teach me sfinae"),
            _message(413, "user-123", "Fixture Sender", "wait, make it short", reply_to_message_id=412),
        ],
    )
    interruption = PendingInterruptionPayload(
        original_trigger_message_id=412,
        original_reply_to_message_id=412,
        interrupting_message_id=413,
        reply_target_sender_id="user-123",
        reply_target_sender_name="Fixture Sender",
        sent_reply_chunks=["first chunk"],
        remaining_reply_chunks=["second chunk", "third chunk"],
    )

    client.decide_interruption(
        frame,
        interruption,
        harness_feedback={"code": "accepted_interruption_reuses_unsent_plan"},
        previous_decision=InterruptionDecisionSchema(
            interrupt_decision="accept",
            action="reply",
            reply_to_message_id=413,
            draft_text="second chunk",
            referenced_memory_ids=[],
            confidence=0.5,
            reason="bad first pass",
            notes=[],
        ),
    )

    call = provider.calls[-1]
    assert "Harness retry mode" in call["instructions"]
    assert '"accepted_interruption_reuses_unsent_plan"' in call["input_items"][-1]["content"][0]["text"]


def test_semantic_client_passes_configured_tools_to_provider() -> None:
    provider = RecordingProvider()
    client = SemanticModelClient(_config(tool_registry=default_tool_registry()), provider)
    frame = _frame(
        session_id="sess_tools",
        trigger_message_id=412,
        messages=[
            _message(412, "user-123", "Fixture Sender", "current trigger"),
        ],
    )

    client.decide(frame)

    tools = provider.calls[-1]["tools"]
    assert tools is not None
    assert [tool["name"] for tool in tools.tool_definitions()] == ["GetTool"]


def test_semantic_client_acknowledges_successful_codex_task_start() -> None:
    provider = CodexStartThenIgnoreProvider()
    client = SemanticModelClient(_config(tool_registry=ToolRegistry([GetTool(), FakeCodexRunTask()])), provider)
    frame = _frame(
        session_id="sess_codex_start",
        trigger_message_id=412,
        messages=[
            _message(412, "user-123", "Fixture Sender", "please implement this"),
        ],
    )

    decision = client.decide(frame)

    assert decision.action == "reply"
    assert decision.reply_to_message_id == 412
    # Starting Codex is the behavior; Amber's acknowledgement wording can evolve.
    assert isinstance(decision.draft_text, str) and decision.draft_text.strip()
    assert decision.notes
    tools = provider.calls[-1]["tools"]
    assert [execution.name for execution in tools.executions] == ["GetTool", "CodexRunTask"]


def test_semantic_client_keeps_linear_codex_task_start_silent() -> None:
    provider = CodexStartThenIgnoreProvider()
    client = SemanticModelClient(_config(tool_registry=ToolRegistry([GetTool(), FakeCodexRunTask()])), provider)
    frame = _frame(
        session_id="linear:queue-hash",
        trigger_message_id=412,
        messages=[
            _message(412, "linear", "Linear", "Linear tasks due today: LIN-1"),
        ],
    )
    frame.linear_task_list = LinearTaskListFramePayload(
        generated_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        window_start_date="2026-06-01",
        window_end_date="2026-06-02",
        queue_hash="queue-hash",
    )

    decision = client.decide(frame)

    assert decision.action == "ignore"
    assert decision.draft_text is None


def _config(tool_registry: ToolRegistry | None = None) -> SemanticConfig:
    return SemanticConfig(
        provider_name="openai",
        api_key="test-key",
        model="gpt-5.4",
        system_prompt="You are the semantic decision layer for Amber, a selective but human-like participant in a Telegram group chat.",
        interruption_prompt="Interruption prompt.",
        action_contract_prompt="Return a strict structured decision.",
        memory_prompt="Memory prompt.",
        max_output_tokens=300,
        temperature=0.3,
        reasoning_effort="medium",
        tool_registry=tool_registry,
    )


def _frame(
    *,
    session_id: str,
    trigger_message_id: int,
    messages: list[ContextFrameMessagePayload],
) -> ContextFramePayload:
    current_message = next(message for message in messages if message.message_id == trigger_message_id)
    return ContextFramePayload(
        session_id=session_id,
        chat_id=1001001001,
        trigger_message_id=trigger_message_id,
        current_message=current_message,
        recent_messages=messages[-2:],
        conversation_window_messages=messages,
        topic_summary="debugging",
        open_loops=[],
        participants=sorted({message.sender_name for message in messages}),
        relevant_memories=[],
        mood="calm",
        fatigue_notice=None,
        recommended_reply_candidate=trigger_message_id,
        engaged_user_ids=sorted({message.sender_id for message in messages}),
        compacted_facts=[],
        expanded_memory_ids=[],
        pending_interruption=None,
    )


def _message(
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
) -> ContextFrameMessagePayload:
    return ContextFrameMessagePayload(
        message_id=message_id,
        sender_id=sender_id,
        sender_name=sender_name,
        is_self=is_self,
        content=content,
        timestamp=datetime(2026, 4, 21, 4, 0 + (message_id - 411), 0, tzinfo=timezone.utc),
        reply_to_message_id=reply_to_message_id,
        reply_to_sender_id=reply_to_sender_id,
        reply_to_sender_name=reply_to_sender_name,
        reply_to_content=reply_to_content,
        reply_to_raw_text=reply_to_content,
        source="conversation_window",
    )
