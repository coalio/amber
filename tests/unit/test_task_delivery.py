from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.action.delivery import DeliveryPolicy
from src.action.gateway import GatewayTransport
from src.action.telegram.transport import RecordingTransport
from src.adapters.codex import CodexAdapter, CodexNotification, CodexTask
from src.adapters.codex import app_server
from src.adapters.registry import AdapterRegistry
from src.ai.semantic.client import SemanticModelClient
from src.ai.semantic.schema import SemanticDecisionSchema
from src.attention.memory.store import MemoryStore
from src.events.bus import EventBus
from src.events.delivery import TaskOrigin, ToolInvocation
from src.receiver.codex.receiver import CodexReceiver
from src.receiver.telegram.receiver import TelegramReceiver
from src.state.gateway import GatewayStore
from src.state.store import GlobalStateStore
from src.tools.registry import ToolRuntime, default_tool_registry
from src.workflows.task_dispatch import TaskDispatchWorkflow
from tests.unit.test_codex_clarification_resilience import (
    _RestartAwareAdapter, _frame_with_answered_question, _reply_session, _state_with_question, _valid_reply_arguments,
)
from tests.unit.test_semantic_client_session_history import _config


def test_dispatch_keeps_origin_out_of_model_context_and_anchors_receipt(tmp_path):
    origin = TaskOrigin(delivery_route="fixture-route")
    order = []
    requests = []
    adapter = object.__new__(CodexAdapter)

    def start(**request):
        order.append("worker")
        requests.append(request)
        return CodexTask("fixture", "task_fixture", "running")

    adapter.start_task = start
    registry = AdapterRegistry()
    registry.register(adapter)
    state = GlobalStateStore(tmp_path / "state.json", "UTC")
    workflow = TaskDispatchWorkflow(
        registry, state, deliver_receipt=lambda receipt: order.append("receipt") or 42,
        resolve_origin=lambda invocation: invocation.task_origin,
    )
    invocation = ToolInvocation(1001001001, 1, 1, "surface", origin)
    result = workflow.dispatch({"task_description": "inspect", "context": {"origin": "forged"}}, invocation)

    assert order == ["receipt", "worker"]
    assert requests[0]["origin"] == origin
    assert requests[0]["context"] == {"origin": "forged"}
    assert result["work_acknowledged"]
    assert state.codex_task_for_outbound_message(chat_id=1001001001, message_id=42).task_id == "task_fixture"
    with pytest.raises(ValidationError):
        origin.delivery_route = "forged"


def test_dispatch_retry_reuses_receipt_but_not_failed_worker(tmp_path):
    order = []
    adapter = object.__new__(CodexAdapter)

    def start(**request):
        order.append("worker")
        if order.count("worker") == 1:
            raise RuntimeError("temporarily unavailable")
        return CodexTask("fixture", "task_fixture", "running")

    adapter.start_task = start
    registry = AdapterRegistry()
    registry.register(adapter)
    workflow = TaskDispatchWorkflow(registry, None, deliver_receipt=lambda receipt: order.append("receipt") or 42)
    invocation = ToolInvocation(1001001001, 1, 1, "surface")
    request = {"task_description": "inspect", "context": {}}
    assert workflow.dispatch(request, invocation)["error_code"] == "codex_task_start_failed"
    assert workflow.dispatch(request, invocation)["work_acknowledged"]
    assert order == ["receipt", "worker", "worker"]


@pytest.mark.parametrize("resume", [False, True])
def test_adapter_serializes_origin_outside_task_context(resume):
    adapter = CodexAdapter()
    requests = []
    adapter.ensure_app_server = lambda: None
    adapter._ensure_event_polling = lambda: None
    adapter._system_prompt = lambda: "fixture"
    adapter._codex_skills_for_task = lambda *args: []
    adapter._post_json = lambda path, payload: requests.append(payload) or {"task_id": "task_fixture", "status": "running"}
    arguments = {"task_description": "inspect", "context": {"origin": "forged"},
                 "origin": TaskOrigin(delivery_route="fixture-route")}
    if resume:
        adapter.continue_task(thread_id="thread_fixture", **arguments)
    else:
        adapter.start_task(**arguments)
    assert requests[0]["origin"] == "fixture-route"
    assert requests[0]["context"] == {"origin": "forged"}


@pytest.mark.parametrize("event_kind", ["question", "notification", "completion"])
def test_runner_snapshots_origin_for_every_delivery_event(event_kind):
    payload = {"origin": "fixture-route", "context": {"project": "fixture"}}
    runner = app_server.CodexTaskRunner("task_fixture", payload)
    payload["origin"] = "later-mutation"
    runner.client = SimpleNamespace()
    app_server.EVENTS.clear()
    app_server.TASKS["task_fixture"] = {"status": "running"}
    try:
        if event_kind == "question":
            runner._register_pending_question(
                request_id=1, tool_call_id="call_fixture", questions=["Which option?"],
                context={"origin": "forged"}, response_kind="tool_call", question_metadata=[],
            )
        elif event_kind == "notification":
            runner._append_user_notification(message="done", notification_kind="completion", context={"origin": "forged"})
        else:
            runner._on_notification({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
        assert app_server.EVENTS
        assert all(event["origin"] == "fixture-route" for event in app_server.EVENTS)
    finally:
        app_server.EVENTS.clear()
        app_server.TASKS.pop("task_fixture", None)


def test_unknown_origin_does_not_fall_back_to_telegram(tmp_path):
    EventBus.reset_for_tests()
    receiver = CodexReceiver(object(), MemoryStore(tmp_path / "memories"), ["1001001001"], lambda origin: [])
    events = []
    EventBus.subscribe("CodexNotificationReceivedEvent", events.append)
    receiver._handle_notification(CodexNotification(
        "fixture", "task_fixture", "notice_fixture", "completion", "done", "inspect",
        origin=TaskOrigin(delivery_route="unknown-route"),
    ))
    assert events == []
    EventBus.reset_for_tests()


def test_file_delivery_uses_trusted_route_instead_of_model_recipient(tmp_path):
    store = GatewayStore(tmp_path / "captures")
    session = store.create("1001001001")
    delegate = RecordingTransport()
    transport = GatewayTransport(delegate, store, ["1001001001"])
    policy = DeliveryPolicy(transport.origin_for_chat, transport.candidates)
    invocation = ToolInvocation("codex:task_fixture", 0, None, "codex_notification", TaskOrigin(delivery_route=session))
    (tmp_path / "fixture.txt").write_text("fixture")
    runtime = ToolRuntime(telegram_transport=transport, codex_workspace=tmp_path, delivery_policy=policy, invocation=invocation)
    tools = default_tool_registry().new_session(runtime=runtime)
    tools.enable("SendFile")
    arguments = {"chat_id": 1001001002, "file_path": "fixture.txt", "caption": None, "reply_to_message_id": None}
    result = tools.execute("SendFile", arguments)
    assert result["sent"] and result["chat_id"] == session
    assert store.read(session)[-1]["event"] == "file"
    assert delegate.records == []
    assert delegate.file_records == []

    runtime.invocation = replace(invocation, task_origin=TaskOrigin(delivery_route="unknown-route"))
    assert tools.execute("SendFile", arguments)["sent"] is False
    assert delegate.file_records == []


def test_telegram_backlog_skips_non_native_conversations():
    client = SimpleNamespace()
    state = SimpleNamespace(snapshot=lambda: SimpleNamespace(
        open_questions={"fixture": SimpleNamespace(chat_id="local-fixture")}, delivery_state={},
    ))
    receiver = TelegramReceiver(client, object(), state)
    asyncio.run(receiver.replay_open_question_backlog())


@pytest.mark.parametrize("generation_fails", [False, True])
def test_recovered_clarification_retains_receipt_after_model_generation(tmp_path, generation_fails):
    state = _state_with_question(tmp_path / "state.json")
    adapter = _RestartAwareAdapter()
    runtime = _reply_session(state, adapter).runtime
    receipts = []
    runtime.task_dispatcher = TaskDispatchWorkflow(
        runtime.adapter_registry, state, deliver_receipt=lambda receipt: receipts.append(receipt) or 42,
    )

    class Provider:
        def generate_structured_with_metadata(self, **request):
            tools = request["tools"]
            tools.enable("CodexSendReply")
            tools.execute("CodexSendReply", _valid_reply_arguments())
            if generation_fails:
                raise RuntimeError("post-recovery generation failed")
            return SemanticDecisionSchema(action="reply", chat_id=1001001001, reply_text="resuming", confidence=1), None

    client = SemanticModelClient(replace(_config(default_tool_registry()), tool_runtime=runtime), Provider())
    result = client.decide(_frame_with_answered_question())
    assert len(receipts) == 1
    assert len(adapter.continuations) == 1
    assert result.work_acknowledged and result.codex_task_started
    assert result.codex_task_id == "task_recovered"
    assert result.action == "ignore"
