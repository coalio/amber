from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from src.action.telegram.transport import RecordingTransport
from src.adapters.codex import CodexAdapter, CodexTask
from src.config.config import get_settings
from src.events.action import OutboundMessageSentEvent
from src.events.ai import SemanticDecisionMadeEvent
from src.events.bus import EventBus, emitter_context
from src.events.codex import CodexCandidatePersonPayload, CodexNotificationPayload, CodexNotificationReceivedEvent
from src.events.context import ContextFrameReadyEvent
from src.events.receiver import (
    TelegramAttachmentPayload,
    TelegramMessagePayload,
    TelegramMessageReceivedEvent,
    TelegramReplySenderPayload,
    TelegramSenderPayload,
    TelegramTransportPayload,
)
from src.runtime import build_application


@pytest.mark.integration
def test_work_mode_message_starts_codex_task_and_replies(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    fake_openai = FakeOpenAIClient()
    monkeypatch.setattr("src.providers.openai.provider.OpenAI", lambda api_key: fake_openai)
    monkeypatch.setattr("src.runtime.build_codex_adapter", lambda settings: FakeCodexAdapter())

    transport = RecordingTransport()
    settings = _test_settings(tmp_path)
    app = build_application(
        settings=settings,
        attention_scorer=NeverCalledAttentionScorer(),
        transport=transport,
    )
    frames: list[ContextFrameReadyEvent] = []
    semantic: list[SemanticDecisionMadeEvent] = []
    outbound: list[OutboundMessageSentEvent] = []
    EventBus.subscribe("ContextFrameReadyEvent", frames.append)
    EventBus.subscribe("SemanticDecisionMadeEvent", semantic.append)
    EventBus.subscribe("OutboundMessageSentEvent", outbound.append)

    message = _telegram_message(
        "please make a tmp folder in your workspace and create a small python script inside it",
        message_id=1440,
    )
    app.message_archive.put(message.payload)
    with emitter_context("receiver.telegram"):
        EventBus.emit(message)

    _wait_until(lambda: bool(outbound), timeout_seconds=3.0)

    adapter = FakeCodexAdapter.instances[-1]
    assert len(fake_openai.responses.calls) == 3
    assert len(adapter.started_tasks) == 1
    assert "tmp folder" in adapter.started_tasks[0]["task_description"]
    assert adapter.started_tasks[0]["context"]["requires_code_editing"] is True
    assert frames and frames[-1].payload.trigger_message_id == 1440
    assert semantic and semantic[-1].payload.action == "reply"
    assert semantic[-1].payload.notes == ["codex task started"]
    assert outbound[-1].payload.sent_message_ids
    assert transport.records[-1].ordered_messages == ["i'll start on that now"]
    assert transport.records[-1].reply_to_message_id == 1440

    app.scheduler.shutdown()


@pytest.mark.integration
def test_codex_notification_with_requested_output_is_sent_without_rewrite(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    fake_openai = FakeOpenAIClient(NotificationResponsesClient())
    monkeypatch.setattr("src.providers.openai.provider.OpenAI", lambda api_key: fake_openai)
    monkeypatch.setattr("src.runtime.build_codex_adapter", lambda settings: FakeCodexAdapter())

    transport = RecordingTransport()
    app = build_application(
        settings=_test_settings(tmp_path),
        attention_scorer=NeverCalledAttentionScorer(),
        transport=transport,
    )
    outbound: list[OutboundMessageSentEvent] = []
    EventBus.subscribe("OutboundMessageSentEvent", outbound.append)

    with emitter_context("receiver.codex"):
        EventBus.emit(
            CodexNotificationReceivedEvent(
                chat_id="codex:task_factorial",
                payload=CodexNotificationPayload(
                    app_server_id="codex-sandbox",
                    task_id="task_factorial",
                    notification_id="notify_factorial",
                    message="script finished. output: 120",
                    task_description="write a python script that calculates factorial 5 and tell me what it outputs",
                    candidate_people=[
                        CodexCandidatePersonPayload(
                            sender_id="1001001001",
                            chat_id=1001001001,
                            display_name="Fixture User",
                        )
                    ],
                    created_at=datetime(2026, 6, 1, 1, 16, 0, tzinfo=timezone.utc),
                ),
            )
        )

    _wait_until(lambda: bool(outbound), timeout_seconds=3.0)

    assert len(fake_openai.responses.calls) == 1
    assert transport.records[-1].ordered_messages == ["done. output: 120"]
    assert "send me" not in transport.records[-1].ordered_messages[0]
    assert outbound[-1].payload.chat_id == 1001001001

    app.scheduler.shutdown()


class FakeOpenAIClient:
    def __init__(self, responses: Any | None = None) -> None:
        self.responses = responses or StrictToolLoopResponsesClient()


class StrictToolLoopResponsesClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        _assert_strict_tool_definitions(kwargs.get("tools", []))
        call_index = len(self.calls)
        if call_index == 1:
            assert [tool["name"] for tool in kwargs["tools"]] == ["GetTool"]
            return _tool_call_response(
                response_id="resp_get_tool",
                name="GetTool",
                call_id="call_get_tool",
                arguments={"tool_name": "CodexRunTask"},
            )
        if call_index == 2:
            assert kwargs["previous_response_id"] == "resp_get_tool"
            assert [tool["name"] for tool in kwargs["tools"]] == ["GetTool", "CodexRunTask"]
            return _tool_call_response(
                response_id="resp_run_task",
                name="CodexRunTask",
                call_id="call_run_task",
                arguments={
                    "task_description": "make a tmp folder in the workspace and create a small python script inside it",
                    "context": {
                        "repository_url": None,
                        "project": None,
                        "feature_label": "tmp-script-smoke",
                        "requires_code_editing": True,
                        "notes": "integration test",
                    },
                },
            )
        if call_index == 3:
            assert kwargs["previous_response_id"] == "resp_run_task"
            tool_output = json.loads(kwargs["input"][0]["output"])
            assert tool_output == {
                "app_server_id": "codex-sandbox",
                "task_id": "task_test",
                "status": "started",
                "thread_id": None,
                "turn_id": None,
                "resumed": False,
            }
            return SimpleNamespace(
                id="resp_final",
                output=[],
                output_text=json.dumps(
                    {
                        "action": "reply",
                        "reply_to_message_id": 1440,
                        "chat_id": 1001001001,
                        "draft_text": "i'll start on that now",
                        "referenced_memory_ids": [],
                        "confidence": 0.92,
                        "notes": ["codex task started"],
                    }
                ),
            )
        raise AssertionError(f"unexpected OpenAI response call {call_index}")


class NotificationResponsesClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        _assert_strict_tool_definitions(kwargs.get("tools", []))
        assert [tool["name"] for tool in kwargs["tools"]] == ["GetTool"]
        turn_context = json.loads(kwargs["input"][-1]["content"][0]["text"])
        assert turn_context["codex_notification"]["message"] == "script finished. output: 120"
        return SimpleNamespace(
            id="resp_notification",
            output=[],
            output_text=json.dumps(
                {
                    "action": "reply",
                    "reply_to_message_id": None,
                    "chat_id": 1001001001,
                    "draft_text": "done. output: 120",
                    "referenced_memory_ids": [],
                    "confidence": 0.93,
                    "notes": ["codex notification includes requested output"],
                }
            ),
        )


class FakeCodexAdapter(CodexAdapter):
    instances: list["FakeCodexAdapter"] = []

    def __init__(self, **_: Any) -> None:
        self.started_tasks: list[dict[str, Any]] = []
        FakeCodexAdapter.instances.append(self)

    def preflight(self) -> None:
        return None

    def ensure_app_server(self) -> None:
        return None

    def start_task(self, *, task_description: str, context: dict[str, Any] | None = None) -> CodexTask:
        self.started_tasks.append({"task_description": task_description, "context": dict(context or {})})
        return CodexTask(app_server_id="codex-sandbox", task_id="task_test", status="started")


class NeverCalledAttentionScorer:
    def score(self, row: dict[str, object]) -> float:
        raise AssertionError(f"work mode should not score attention: {row}")


def _tool_call_response(*, response_id: str, name: str, call_id: str, arguments: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        id=response_id,
        output_text="",
        output=[
            SimpleNamespace(
                type="function_call",
                name=name,
                call_id=call_id,
                arguments=json.dumps(arguments),
            )
        ],
    )


def _assert_strict_tool_definitions(tools: list[dict[str, Any]]) -> None:
    for tool in tools:
        assert tool["strict"] is True
        _assert_strict_schema(tool["parameters"])


def _assert_strict_schema(schema: dict[str, Any]) -> None:
    schema_type = schema.get("type")
    is_object = schema_type == "object" or (isinstance(schema_type, list) and "object" in schema_type)
    is_array = schema_type == "array" or (isinstance(schema_type, list) and "array" in schema_type)
    if is_object:
        properties = schema.get("properties", {})
        assert schema.get("additionalProperties") is False
        assert set(schema.get("required", [])) == set(properties)
        for property_schema in properties.values():
            if isinstance(property_schema, dict):
                _assert_strict_schema(property_schema)
    if is_array and isinstance(schema.get("items"), dict):
        _assert_strict_schema(schema["items"])


def _telegram_message(content: str, *, message_id: int) -> TelegramMessageReceivedEvent:
    payload = TelegramMessagePayload(
        message_id=message_id,
        chat_id=1001001001,
        sender=TelegramSenderPayload(id="1001001001", name="Fixture User"),
        timestamp=datetime(2026, 6, 1, 0, 49, 48, tzinfo=timezone.utc),
        content=content,
        raw_text=content,
        reply_to_message_id=None,
        reply_to_sender=TelegramReplySenderPayload(),
        mentions=[],
        attachment=TelegramAttachmentPayload(),
        transport=TelegramTransportPayload(peer_id=1001001001, raw_chat_id=1001001001, raw_message_id=message_id),
    )
    return TelegramMessageReceivedEvent(chat_id=1001001001, payload=payload)


def _test_settings(tmp_path):
    get_settings.cache_clear()
    return get_settings().model_copy(
        update={
            "mode": "work",
            "ai_api_key": "test-key",
            "linear_enabled": False,
            "memories_dir": tmp_path / "memories",
            "runtime_state_path": tmp_path / "runtime_state.json",
            "log_dir": tmp_path / ".logs",
            "always_surface_telegram_ids": ("1001001001",),
            "enable_real_delays": False,
            "disable_sleep_state": True,
            "context_debounce_seconds": 0.0,
            "context_initial_engagement_delay_min_seconds": 0.0,
            "context_initial_engagement_delay_max_seconds": 0.0,
            "context_idle_timeout_seconds": 30.0,
            "context_idle_timeout_min_seconds": 30.0,
            "context_idle_timeout_max_seconds": 30.0,
            "action_filler_pause_seconds": 0.0,
            "action_inter_chunk_delay_min_seconds": 0.0,
            "action_inter_chunk_delay_max_seconds": 0.0,
            "action_inter_chunk_delay_step_seconds": 0.0,
        }
    )


def _wait_until(predicate, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for expected event")
