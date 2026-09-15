from dataclasses import replace

import pytest

from src.adapters.codex import CodexAdapter, CodexTask
from src.adapters.registry import AdapterRegistry
from src.ai.semantic.client import SemanticModelClient
from src.ai.semantic.layer import ConsciousHarness
from src.ai.config import AIConfig
from src.tools.registry import ToolRuntime, default_tool_registry
from tests.unit.test_semantic_client_session_history import _config, _frame, _message


class DispatchProvider:
    def generate_structured_with_metadata(self, **request):
        tools = request["tools"]
        tools.execute("GetTool", {"tool_name": "CodexRunTask"})
        tools.execute("CodexRunTask", {"task_description": "Inspect the fixture", "context": {}})
        raise RuntimeError("post-dispatch generation failed")


def test_receipt_is_delivered_before_worker_and_survives_failed_generation():
    order = []
    adapter = object.__new__(CodexAdapter)
    adapter.start_task = lambda **kwargs: order.append("worker") or CodexTask("fixture", "task_fixture", "running")
    registry = AdapterRegistry()
    registry.register(adapter)
    config = replace(_config(default_tool_registry()), tool_runtime=ToolRuntime(adapter_registry=registry))
    client = SemanticModelClient(config, DispatchProvider(), acknowledge_work=lambda frame: order.append("receipt") or 42)
    frame = _frame(session_id="receipt", trigger_message_id=412, messages=[_message(412, "user-123", "Fixture", "inspect")])
    frame.response_required = True

    decision = client.decide(frame)

    assert order == ["receipt", "worker"]
    assert decision.work_acknowledged and decision.codex_task_started
    assert decision.action == "ignore"
    assert decision.codex_task_id == "task_fixture"
    assert ConsciousHarness(AIConfig(semantic_retry_budget=1, max_reply_chars=1600)).evaluate(frame, decision) is None


def test_failed_receipt_prevents_worker_start():
    adapter = object.__new__(CodexAdapter)
    calls = []
    adapter.start_task = lambda **kwargs: calls.append(kwargs)
    registry = AdapterRegistry()
    registry.register(adapter)

    def fail_receipt(frame):
        raise RuntimeError("delivery unavailable")

    client = SemanticModelClient(
        replace(_config(default_tool_registry()), tool_runtime=ToolRuntime(adapter_registry=registry)),
        DispatchProvider(), acknowledge_work=fail_receipt,
    )
    frame = _frame(session_id="receipt", trigger_message_id=412, messages=[_message(412, "user-123", "Fixture", "inspect")])
    with pytest.raises(RuntimeError, match="delivery unavailable"):
        client.decide(frame)
    assert calls == []
