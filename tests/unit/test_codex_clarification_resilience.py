from __future__ import annotations

import io
import json
import logging
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import error

import pytest

from src.adapters.codex import CodexAppServerRequestError, CodexAdapter, CodexTask
from src.adapters.codex import adapter as codex_adapter_module
from src.adapters.codex import app_server as codex_app_server
from src.adapters.registry import AdapterRegistry
from src.ai.config import AIConfig
from src.ai.semantic.layer import AILayer
from src.ai.semantic.schema import SemanticDecisionSchema
from src.events.codex import CodexCandidatePersonPayload
from src.events.context import ContextFrameMessagePayload, ContextFramePayload, OpenQuestionPayload
from src.state.models import OpenQuestionCandidate
from src.state.store import GlobalStateStore
from src.tools.codex_workflow import CodexWorkRoute
from src.tools.registry import ToolRuntime, default_tool_registry
from src.utils import files as file_utils
from src.utils.files import write_json_atomic
from src.utils.logging import HumanReadableFormatter
from src.utils.time import utc_now


class _ResponseHandler:
    def __init__(self) -> None:
        self.status: int | None = None
        self.body = b""
        self.headers: list[tuple[str, str]] = []

    def send_response(self, status_code: int) -> None:
        self.status = status_code

    def send_header(self, key: str, value: str) -> None:
        self.headers.append((key, value))

    def end_headers(self) -> None:
        return

    @property
    def wfile(self):
        outer = self

        class _Writer:
            def write(self, body: bytes) -> None:
                outer.body = body

        return _Writer()

    def payload(self) -> dict[str, Any]:
        return json.loads(self.body.decode("utf-8"))


class _RestartAwareAdapter(CodexAdapter):
    def __init__(
        self,
        *,
        submit_error_code: str = "unknown_task",
        continuation_error: str | None = None,
    ) -> None:
        self.submit_error_code = submit_error_code
        self.continuation_error = continuation_error
        self.submissions: list[dict[str, Any]] = []
        self.continuations: list[dict[str, Any]] = []

    def submit_tool_output(self, **kwargs: Any) -> dict[str, Any]:
        self.submissions.append(kwargs)
        raise CodexAppServerRequestError(
            f"fixture failure: {self.submit_error_code}",
            status_code=404 if self.submit_error_code == "unknown_task" else 409,
            error_code=self.submit_error_code,
        )

    def continue_task(
        self,
        *,
        thread_id: str,
        task_description: str,
        context: dict[str, Any] | None = None,
    ) -> CodexTask:
        self.continuations.append(
            {
                "thread_id": thread_id,
                "task_description": task_description,
                "context": dict(context or {}),
            }
        )
        if self.continuation_error is not None:
            raise RuntimeError(self.continuation_error)
        return CodexTask(
            app_server_id="codex-sandbox",
            task_id="task_recovered",
            status="running",
            thread_id=thread_id,
            turn_id="turn_recovered",
        )

    def start_task(self, **kwargs: Any) -> CodexTask:
        raise AssertionError("clarification recovery must continue the saved thread")


class _TransportFailureAdapter:
    name = "codex"

    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        self.outputs: list[dict[str, Any]] = []

    def submit_tool_output(self, **kwargs: Any) -> dict[str, Any]:
        self.outputs.append(kwargs)
        raise CodexAppServerRequestError("fixture connection failure", error_code=self.error_code)


class _SuccessfulReplyAdapter:
    name = "codex"

    def __init__(self) -> None:
        self.outputs: list[dict[str, Any]] = []

    def submit_tool_output(self, **kwargs: Any) -> dict[str, Any]:
        self.outputs.append(kwargs)
        return {"status": "clarification_received"}


def test_open_question_survives_old_expiry_metadata_and_multi_day_restart(tmp_path: Path) -> None:
    state_path = tmp_path / "global_state.json"
    created_at = utc_now() - timedelta(days=30)
    state_store = _state_with_question(state_path, created_at=created_at)
    legacy_payload = json.loads(state_path.read_text(encoding="utf-8"))
    legacy_question = next(iter(legacy_payload["open_questions"].values()))
    legacy_question["expires_at"] = (created_at + timedelta(minutes=15)).isoformat()
    state_path.write_text(json.dumps(legacy_payload), encoding="utf-8")

    reloaded = GlobalStateStore(state_path, "UTC")
    question = reloaded.open_question_by_codex_ids(
        app_server_id="codex-sandbox",
        task_id="task_original",
        tool_call_id="tool_original",
    )

    assert question is not None
    assert question.created_at == created_at
    assert question.expires_at is not None  # accepted only for compatibility with v0.5.0 state
    reloaded.update_attention_state(mood="focused")
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert "expires_at" not in next(iter(persisted["open_questions"].values()))
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    assert state_store.snapshot().open_questions


def test_atomic_state_write_preserves_last_generation_if_power_fails_before_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_path = tmp_path / "global_state.json"
    write_json_atomic(state_path, {"generation": 1}, mode=0o600)
    original_bytes = state_path.read_bytes()

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("simulated power loss before atomic rename")

    monkeypatch.setattr(file_utils.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated power loss"):
        write_json_atomic(state_path, {"generation": 2}, mode=0o600)

    assert state_path.read_bytes() == original_bytes
    assert list(tmp_path.glob(f".{state_path.name}.*")) == []


def test_blocked_runner_waits_without_a_deadline_or_additional_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    codex_app_server.TASKS.clear()
    codex_app_server.RUNNERS.clear()
    task_id = "task_idle"
    codex_app_server.TASKS[task_id] = {"status": "queued"}
    rpc_methods: list[str] = []

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            return

        def start(self) -> None:
            return

        def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
            rpc_methods.append(method)
            if method == "thread/start":
                return {"thread": {"id": "thread_idle"}}
            if method == "turn/start":
                return {"turn": {"id": "turn_idle"}}
            return {}

        def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
            return

        def stop(self) -> None:
            return

    wait_arguments: list[tuple[Any, ...]] = []

    class TokenIdleWait:
        def wait(self, *args: Any) -> None:
            wait_arguments.append(args)

        def is_set(self) -> bool:
            return False

        def set(self) -> None:
            return

    monkeypatch.setattr(codex_app_server, "JsonRpcClient", FakeClient)
    runner = codex_app_server.CodexTaskRunner(task_id, {"task_description": "Wait for a durable answer."})
    runner._done = TokenIdleWait()  # type: ignore[assignment]
    codex_app_server.RUNNERS[task_id] = runner

    runner._run()

    assert wait_arguments == [()]  # Event.wait received no timeout at all
    assert rpc_methods.count("turn/start") == 1
    assert task_id not in codex_app_server.RUNNERS


def test_tool_output_replay_is_idempotent_after_dropped_http_response() -> None:
    codex_app_server.TASKS.clear()
    codex_app_server.EVENTS.clear()
    codex_app_server.RUNNERS.clear()
    task_id = "task_drop"
    codex_app_server.TASKS[task_id] = {
        "status": "waiting_for_clarification",
        "pending_tool_calls": ["tool_drop"],
        "tool_outputs": [],
    }

    class FakeClient:
        def __init__(self) -> None:
            self.responses: list[dict[str, Any]] = []

        def respond(self, request_id: int, result: dict[str, Any] | None = None, error: Any = None) -> None:
            self.responses.append({"request_id": request_id, "result": result, "error": error})

    client = FakeClient()
    runner = codex_app_server.CodexTaskRunner(task_id, {"task_description": "Resume safely."})
    runner.pending_tool_calls["tool_drop"] = codex_app_server.PendingToolCall(
        request_id=7,
        task_id=task_id,
        tool_call_id="tool_drop",
        response_kind="dynamic_tool",
        client=client,
    )
    codex_app_server.RUNNERS[task_id] = runner
    output = {"answers": ["Use option B."], "summary": "Option B", "confidence": 0.9}

    first = _receive_tool_output(task_id, "tool_drop", output)
    replay = _receive_tool_output(task_id, "tool_drop", output)
    conflict = _receive_tool_output(
        task_id,
        "tool_drop",
        {"answers": ["Use option C."], "summary": "Option C", "confidence": 0.9},
    )

    assert first.status == 200
    assert replay.status == 200
    assert replay.payload()["replayed"] is True
    assert conflict.status == 409
    assert conflict.payload()["error"] == "tool_output_conflict"
    assert len(client.responses) == 1
    assert len(codex_app_server.TASKS[task_id]["tool_outputs"]) == 1


def test_dead_worker_pipe_returns_recoverable_error_instead_of_dropping_connection() -> None:
    codex_app_server.TASKS.clear()
    codex_app_server.EVENTS.clear()
    codex_app_server.RUNNERS.clear()
    task_id = "task_power_loss"
    codex_app_server.TASKS[task_id] = {
        "status": "waiting_for_clarification",
        "pending_tool_calls": ["tool_power_loss"],
        "tool_outputs": [],
    }

    class BrokenClient:
        def respond(self, request_id: int, result: Any = None, error: Any = None) -> None:
            raise BrokenPipeError("worker process disappeared")

    runner = codex_app_server.CodexTaskRunner(task_id, {"task_description": "Resume after failure."})
    runner.pending_tool_calls["tool_power_loss"] = codex_app_server.PendingToolCall(
        request_id=8,
        task_id=task_id,
        tool_call_id="tool_power_loss",
        response_kind="dynamic_tool",
        client=BrokenClient(),
    )
    codex_app_server.RUNNERS[task_id] = runner

    response = _receive_tool_output(task_id, "tool_power_loss", {"answers": ["Proceed."], "summary": "Proceed"})

    assert response.status == 409
    assert response.payload()["error"] == "task_runner_unavailable"
    assert codex_app_server.TASKS[task_id]["tool_outputs"] == []
    assert codex_app_server.TASKS[task_id]["status"] == "failed"
    assert task_id not in codex_app_server.RUNNERS


@pytest.mark.parametrize("server_error", ["unknown_task", "task_runner_unavailable"])
def test_reply_recovers_same_thread_after_app_server_or_worker_restart(
    tmp_path: Path,
    server_error: str,
) -> None:
    state_store = _state_with_question(tmp_path / "state.json", thread_id="thread_durable")
    adapter = _RestartAwareAdapter(submit_error_code=server_error)
    session = _reply_session(state_store, adapter)

    result = session.execute("CodexSendReply", _valid_reply_arguments())

    assert result["submitted"] is True
    assert result["recovered"] is True
    assert result["task_id"] == "task_recovered"
    assert adapter.continuations[0]["thread_id"] == "thread_durable"
    assert "Use the supplied regional profile." in adapter.continuations[0]["task_description"]
    assert state_store.snapshot().open_questions == {}
    recovered = state_store.codex_task_by_ids(app_server_id="codex-sandbox", task_id="task_recovered")
    assert recovered is not None
    assert recovered.thread_id == "thread_durable"


def test_reply_preserves_answer_when_restart_has_no_resumable_thread(tmp_path: Path) -> None:
    state_store = _state_with_question(tmp_path / "state.json", thread_id=None)
    adapter = _RestartAwareAdapter()
    session = _reply_session(state_store, adapter)

    result = session.execute("CodexSendReply", _valid_reply_arguments())

    assert result["error_code"] == "clarification_recovery_thread_missing"
    assert "no resumable thread" in result["user_error"]
    assert state_store.snapshot().open_questions
    assert adapter.continuations == []


def test_reply_preserves_answer_when_thread_recovery_fails(tmp_path: Path) -> None:
    state_store = _state_with_question(tmp_path / "state.json", thread_id="thread_durable")
    adapter = _RestartAwareAdapter(continuation_error="sandbox could not restart")
    session = _reply_session(state_store, adapter)

    result = session.execute("CodexSendReply", _valid_reply_arguments())

    assert result["error_code"] == "clarification_recovery_failed"
    assert "could not start or resume" in result["user_error"]
    assert state_store.snapshot().open_questions


@pytest.mark.parametrize("error_code", ["transport_error", "invalid_response", "unknown_tool_call"])
def test_ambiguous_connection_and_tool_failures_keep_clarification_for_retry(
    tmp_path: Path,
    error_code: str,
) -> None:
    state_store = _state_with_question(tmp_path / f"{error_code}.json")
    adapter = _TransportFailureAdapter(error_code)
    session = _reply_session(state_store, adapter)

    result = session.execute("CodexSendReply", _valid_reply_arguments())

    assert result["error_code"] == error_code
    assert "still saved" in result["user_error"]
    saved_question = next(iter(state_store.snapshot().open_questions.values()))
    assert saved_question.user_replies == ["Use the supplied regional profile."]


def test_conflicting_replay_reports_new_answer_not_applied_without_leaving_stale_lock(tmp_path: Path) -> None:
    state_store = _state_with_question(tmp_path / "conflict.json")
    adapter = _TransportFailureAdapter("tool_output_conflict")
    session = _reply_session(state_store, adapter)

    result = session.execute("CodexSendReply", _valid_reply_arguments())

    assert result["error_code"] == "tool_output_conflict"
    assert "newer clarification was not applied" in result["user_error"]
    assert result["cleared_open_question"] is True
    assert state_store.snapshot().open_questions == {}


@pytest.mark.parametrize(
    ("override", "expected_code"),
    [
        ({"answers": "not-a-list"}, "invalid_clarification_answer"),
        ({"answers": [""]}, "invalid_clarification_answer"),
        ({"summary": ""}, "invalid_clarification_answer"),
        ({"confidence": "very sure"}, "invalid_clarification_confidence"),
        ({"confidence": 1.01}, "invalid_clarification_confidence"),
        ({"confidence": True}, "invalid_clarification_confidence"),
        ({"tool_call_id": "invented-id"}, "clarification_state_mismatch"),
    ],
)
def test_user_and_model_input_errors_do_not_consume_the_saved_question(
    tmp_path: Path,
    override: dict[str, Any],
    expected_code: str,
) -> None:
    state_store = _state_with_question(tmp_path / f"{expected_code}-{len(str(override))}.json")
    adapter = _SuccessfulReplyAdapter()
    session = _reply_session(state_store, adapter)
    arguments = {**_valid_reply_arguments(), **override}

    result = session.execute("CodexSendReply", arguments)

    assert result["error_code"] == expected_code
    assert state_store.snapshot().open_questions
    assert adapter.outputs == []


def test_adapter_preserves_http_error_type_and_transport_ambiguity(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = CodexAdapter(auto_update=False)

    def http_failure(*args: Any, **kwargs: Any) -> None:
        raise error.HTTPError(
            "http://127.0.0.1/tasks/missing/tool-output",
            404,
            "not found",
            {},
            io.BytesIO(b'{"error":"unknown_task"}'),
        )

    monkeypatch.setattr(codex_adapter_module.request, "urlopen", http_failure)
    with pytest.raises(CodexAppServerRequestError) as http_error:
        adapter._post_json("/tasks/missing/tool-output", {})
    assert http_error.value.status_code == 404
    assert http_error.value.error_code == "unknown_task"

    monkeypatch.setattr(
        codex_adapter_module.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionResetError("connection dropped")),
    )
    with pytest.raises(CodexAppServerRequestError) as transport_error:
        adapter._post_json("/tasks/missing/tool-output", {})
    assert transport_error.value.status_code is None
    assert transport_error.value.error_code == "transport_error"

    class TruncatedResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args: Any) -> None:
            return

        def read(self) -> bytes:
            return b'{"status":"clarification_received"'

    monkeypatch.setattr(codex_adapter_module.request, "urlopen", lambda *args, **kwargs: TruncatedResponse())
    with pytest.raises(CodexAppServerRequestError) as invalid_response:
        adapter._post_json("/tasks/missing/tool-output", {})
    assert invalid_response.value.error_code == "invalid_response"


def test_event_polling_resets_cursor_when_only_app_server_process_restarts() -> None:
    adapter = CodexAdapter(auto_update=False)
    adapter._last_event_seq = 9
    adapter._event_server_instance_id = "server_before_power_loss"
    requested_paths: list[str] = []
    notifications = []
    adapter.subscribe_notifications(notifications.append)

    event = {
        "seq": 1,
        "type": "AmberNotifyUser",
        "app_server_id": "codex-sandbox",
        "task_id": "task_after_restart",
        "notification_id": "notification_after_restart",
        "notification_kind": "milestone",
        "message": "Recovered after restart.",
        "task_description": "Recover.",
    }

    def get_json(path: str, *, timeout: float = 30) -> dict[str, Any]:
        requested_paths.append(path)
        if len(requested_paths) == 2:
            adapter._poll_stop.set()
        return {"server_instance_id": "server_after_power_loss", "events": [event]}

    adapter._get_json = get_json  # type: ignore[method-assign]
    adapter._poll_events()

    assert "after=9" in requested_paths[0]
    assert "after=0" in requested_paths[1]
    assert [item.notification_id for item in notifications] == ["notification_after_restart"]
    assert adapter._last_event_seq == 1


def test_fallback_reports_verified_blocker_without_claiming_work_is_pending() -> None:
    frame = _frame_with_answered_question()
    decision = SemanticDecisionSchema(
        action="reply",
        work_intent="delegate",
        chat_id=frame.chat_id,
        reply_text="thanks, i'll continue",
        confidence=0.8,
        codex_work_error_code="clarification_recovery_thread_missing",
        codex_work_error=(
            "the saved codex process was lost and no resumable thread was recorded; the answer is still saved"
        ),
    )
    layer = AILayer(AIConfig(semantic_retry_budget=0, max_reply_chars=320), _StaticClient(decision))

    result = layer._call_with_harness(frame)

    assert result.action == "reply"
    assert "no resumable thread was recorded" in (result.reply_text or "")
    assert "still saved" in (result.reply_text or "")
    assert "minute" not in (result.reply_text or "")
    assert result.codex_work_error_code == "clarification_recovery_thread_missing"
    assert result.codex_work_dispatched is False


def test_state_machine_fallback_attempt_does_not_hide_original_recovery_failure() -> None:
    adapter = _SuccessfulReplyAdapter()
    session = default_tool_registry().new_session(
        runtime=ToolRuntime(adapter_registry=AdapterRegistry([adapter])),
        codex_work_route=CodexWorkRoute.SUBMIT_CLARIFICATION,
    )
    session.record_codex_failure(
        "CodexSendReply",
        {
            "error": "no saved thread",
            "error_code": "clarification_recovery_thread_missing",
            "user_error": "the saved process was lost and no resumable thread was recorded",
        },
    )

    denied = session.execute("GetTool", {"tool_name": "CodexRunTask"})

    assert denied["error_code"] == "codex_route_blocked"
    assert session.last_codex_failure is not None
    assert session.last_codex_failure.result["error_code"] == "clarification_recovery_thread_missing"


def test_recovered_clarification_keeps_new_task_provenance() -> None:
    frame = _frame_with_answered_question()
    decision = SemanticDecisionSchema(
        action="reply",
        work_intent="delegate",
        codex_work_dispatched=True,
        codex_task_started=True,
        chat_id=frame.chat_id,
        reply_text="thanks, continuing now",
        confidence=0.9,
        codex_app_server_id="codex-sandbox",
        codex_task_id="task_recovered",
        codex_tool_call_id=None,
    )
    layer = AILayer(AIConfig(semantic_retry_budget=0, max_reply_chars=320), _StaticClient(decision))

    result = layer._call_with_harness(frame)

    assert result.codex_task_id == "task_recovered"
    assert result.codex_tool_call_id is None
    assert result.codex_target_sender_id == "user-123"


def test_tool_logs_do_not_serialize_clarification_secrets(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    state_store = _state_with_question(tmp_path / "state.json")
    adapter = _SuccessfulReplyAdapter()
    session = _reply_session(state_store, adapter)
    secret_marker = "fixture-secret-must-not-enter-logs"
    caplog.set_level(logging.INFO, logger="amber.tools")
    arguments = {
        **_valid_reply_arguments(),
        "answers": [secret_marker],
        "summary": f"Use {secret_marker}",
    }

    result = session.execute("CodexSendReply", arguments)

    assert result["submitted"] is True
    serialized_records = "\n".join(str(record.__dict__) for record in caplog.records)
    assert secret_marker not in serialized_records
    assert stat.S_IMODE((tmp_path / "state.json").stat().st_mode) == 0o600


def test_log_formatter_redacts_credential_shaped_message_content() -> None:
    access_key_id = "AKIAABCDEFGHIJKLMNOP"
    secret_value = "fixtureSecretValue1234567890/+="
    record = logging.LogRecord(
        name="amber.fixture",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="received credential",
        args=(),
        exc_info=None,
    )
    record.event = "fixture.event"
    record.context = {
        "content_preview": f'{{"AccessKeyId":"{access_key_id}","SecretAccessKey":"{secret_value}"}}'
    }

    rendered = HumanReadableFormatter().format(record)

    assert access_key_id not in rendered
    assert secret_value not in rendered
    assert rendered.count("[REDACTED]") == 2


def _receive_tool_output(task_id: str, tool_call_id: str, output: dict[str, Any]) -> _ResponseHandler:
    handler = _ResponseHandler()
    codex_app_server.Handler._receive_tool_output(
        handler,
        task_id,
        {"tool_call_id": tool_call_id, "output": output},
    )
    return handler


def _state_with_question(
    path: Path,
    *,
    thread_id: str | None = "thread_durable",
    created_at: datetime | None = None,
) -> GlobalStateStore:
    state_store = GlobalStateStore(path, "UTC")
    timestamp = created_at or utc_now()
    state_store.remember_open_question(
        chat_id=1001001001,
        sender_id="user-123",
        sender_name="Fixture Sender",
        app_server_id="codex-sandbox",
        task_id="task_original",
        tool_call_id="tool_original",
        questions=["Which regional profile should be used?"],
        task_description="Configure the deployment.",
        context={"project": "fixture"},
        candidate_people=[
            OpenQuestionCandidate(
                sender_id="user-123",
                chat_id=1001001001,
                display_name="Fixture Sender",
            )
        ],
        created_at=timestamp,
    )
    state_store.mark_codex_task_turn(
        app_server_id="codex-sandbox",
        task_id="task_original",
        thread_id=thread_id,
        turn_id="turn_original",
        status="waiting_for_clarification",
        updated_at=timestamp,
    )
    return state_store


def _reply_session(state_store: GlobalStateStore, adapter: Any):
    state_store.append_open_question_reply(
        1001001001,
        "Use the supplied regional profile.",
        message_id=412,
        sender_id="user-123",
    )
    session = default_tool_registry().new_session(
        runtime=ToolRuntime(
            adapter_registry=AdapterRegistry([adapter]),
            state_store=state_store,
        ),
        codex_work_route=CodexWorkRoute.SUBMIT_CLARIFICATION,
    )
    session.enable("CodexSendReply")
    return session


def _valid_reply_arguments() -> dict[str, Any]:
    return {
        "app_server_id": "codex-sandbox",
        "task_id": "task_original",
        "tool_call_id": "tool_original",
        "answers": ["Use the supplied regional profile."],
        "summary": "Use the supplied regional profile.",
        "confidence": 0.95,
    }


def _frame_with_answered_question() -> ContextFramePayload:
    message = ContextFrameMessagePayload(
        message_id=412,
        sender_id="user-123",
        sender_name="Fixture Sender",
        content="use the supplied regional profile",
        timestamp=datetime(2026, 8, 23, tzinfo=timezone.utc),
    )
    question = OpenQuestionPayload(
        app_server_id="codex-sandbox",
        task_id="task_original",
        tool_call_id="tool_original",
        questions=["Which regional profile should be used?"],
        task_description="Configure the deployment.",
        context={"project": "fixture"},
        candidate_people=[
            CodexCandidatePersonPayload(
                sender_id="user-123",
                chat_id=1001001001,
                display_name="Fixture Sender",
            )
        ],
        selected_sender_id="user-123",
        selected_sender_name="Fixture Sender",
        user_replies=[message.content],
    )
    return ContextFramePayload(
        session_id="session-resilience",
        chat_id=1001001001,
        trigger_message_id=message.message_id,
        current_message=message,
        recent_messages=[message],
        conversation_window_messages=[message],
        topic_summary="Durable clarification",
        participants=["Fixture Sender"],
        mood="focused",
        recommended_reply_candidate=message.message_id,
        response_required=True,
        response_required_reason="direct_work_request",
        open_question=question,
        open_questions=[question],
    )


class _StaticClient:
    def __init__(self, decision: SemanticDecisionSchema) -> None:
        self.decision = decision

    def decide(self, frame: ContextFramePayload, **kwargs: Any) -> SemanticDecisionSchema:
        return self.decision.model_copy(deep=True)

    def decide_interruption(self, *args: Any, **kwargs: Any) -> SemanticDecisionSchema:
        raise AssertionError("interruption path is not expected")
