from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from src.adapters.base import BaseAdapter
from src.adapters.registry import AdapterRegistry
from src.ai.config import AIConfig
from src.ai.semantic.layer import AILayer
from src.ai.semantic.schema import SemanticDecisionSchema
from src.events.context import ContextFrameMessagePayload, ContextFramePayload, OpenQuestionPayload
from src.state.models import OpenQuestionCandidate
from src.state.store import GlobalStateStore
from src.tools.registry import ToolRuntime, default_tool_registry
from src.utils.time import utc_now


def test_multiple_open_questions_share_chat_without_overwriting(tmp_path) -> None:
    state_store = GlobalStateStore(tmp_path / "state.json", "UTC")
    now = utc_now()

    _remember_question(state_store, task_id="task-a", tool_call_id="tool-a", question="Which API should task A use?", now=now)
    _remember_question(state_store, task_id="task-b", tool_call_id="tool-b", question="Which UI should task B use?", now=now)

    questions = state_store.open_questions_for_chat(1001001001, sender_id="user-123")
    assert [(question.task_id, question.tool_call_id) for question in questions] == [("task-a", "tool-a"), ("task-b", "tool-b")]


def test_user_reply_attaches_to_all_matching_open_questions_and_selected_clear_leaves_other(tmp_path) -> None:
    state_store = GlobalStateStore(tmp_path / "state.json", "UTC")
    now = utc_now()
    _remember_question(state_store, task_id="task-a", tool_call_id="tool-a", question="Which API should task A use?", now=now)
    _remember_question(state_store, task_id="task-b", tool_call_id="tool-b", question="Which UI should task B use?", now=now)

    updated = state_store.append_open_question_replies(
        chat_id=1001001001,
        sender_id="user-123",
        content="task b should use the compact table ui",
        message_id=412,
    )

    assert [(question.task_id, question.user_replies) for question in updated] == [
        ("task-a", ["task b should use the compact table ui"]),
        ("task-b", ["task b should use the compact table ui"]),
    ]

    adapter = FakeCodexAdapter()
    session = default_tool_registry().new_session(
        runtime=ToolRuntime(adapter_registry=AdapterRegistry([adapter]), state_store=state_store)
    )
    session.enable("CodexSendReply")
    result = session.execute(
        "CodexSendReply",
        {
            "app_server_id": "codex-sandbox",
            "task_id": "task-b",
            "tool_call_id": "tool-b",
            "answers": ["Use the compact table UI."],
            "summary": "Task B should use the compact table UI.",
            "confidence": 0.94,
        },
    )

    remaining = state_store.open_questions_for_chat(1001001001, sender_id="user-123")
    assert result["cleared_open_question"] is True
    assert adapter.outputs[0]["task_id"] == "task-b"
    assert [(question.task_id, question.tool_call_id) for question in remaining] == [("task-a", "tool-a")]


def test_ambiguous_reply_with_multiple_open_questions_remains_unbound() -> None:
    frame = _frame_with_open_questions()
    client = StaticSemanticClient(
        SemanticDecisionSchema(
            action="reply",
            reply_to_message_id=412,
            chat_id=1001001001,
            reply_text="which task did you mean, the api one or the ui one?",
            referenced_memory_ids=[],
            confidence=0.9,
            notes=["ambiguous_open_question"],
        )
    )
    layer = AILayer(AIConfig(semantic_retry_budget=1, max_reply_chars=320), client)

    decision = layer._call_with_harness(frame)

    assert decision.action == "reply"
    assert decision.codex_app_server_id is None
    assert decision.codex_task_id is None
    assert decision.codex_tool_call_id is None
    # keep ambiguous replies with amber
    assert isinstance(decision.reply_text, str) and decision.reply_text.strip()


class FakeCodexAdapter(BaseAdapter):
    name = "codex"

    def __init__(self) -> None:
        self.outputs: list[dict[str, Any]] = []

    def submit_tool_output(
        self,
        *,
        app_server_id: str,
        task_id: str,
        tool_call_id: str,
        output: dict[str, Any],
    ) -> dict[str, Any]:
        self.outputs.append(
            {
                "app_server_id": app_server_id,
                "task_id": task_id,
                "tool_call_id": tool_call_id,
                "output": output,
            }
        )
        return {"status": "accepted"}


class StaticSemanticClient:
    def __init__(self, decision: SemanticDecisionSchema) -> None:
        self._decision = decision

    def decide(self, frame: ContextFramePayload, **kwargs: Any) -> SemanticDecisionSchema:
        return self._decision.model_copy(deep=True)

    def decide_interruption(self, frame, interruption, **kwargs):
        raise AssertionError("interruption flow is not used")


def _remember_question(
    state_store: GlobalStateStore,
    *,
    task_id: str,
    tool_call_id: str,
    question: str,
    now,
) -> None:
    state_store.remember_open_question(
        chat_id=1001001001,
        sender_id="user-123",
        sender_name="Fixture Sender",
        app_server_id="codex-sandbox",
        task_id=task_id,
        tool_call_id=tool_call_id,
        questions=[question],
        task_description=f"Implement {task_id}.",
        context={"linear_project": "Amber", "linear_identifier": task_id.upper()},
        candidate_people=[
            OpenQuestionCandidate(
                sender_id="user-123",
                chat_id=1001001001,
                display_name="Fixture Sender",
            )
        ],
        created_at=now,
        expires_at=now + timedelta(minutes=15),
    )


def _frame_with_open_questions() -> ContextFramePayload:
    current = ContextFrameMessagePayload(
        message_id=412,
        sender_id="user-123",
        sender_name="Fixture Sender",
        content="yeah use that one",
        timestamp=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    open_questions = [
        OpenQuestionPayload(
            app_server_id="codex-sandbox",
            task_id="task-a",
            tool_call_id="tool-a",
            questions=["Which API should task A use?"],
            task_description="Implement task A.",
            context={"linear_identifier": "TASK-A"},
        ),
        OpenQuestionPayload(
            app_server_id="codex-sandbox",
            task_id="task-b",
            tool_call_id="tool-b",
            questions=["Which UI should task B use?"],
            task_description="Implement task B.",
            context={"linear_identifier": "TASK-B"},
        ),
    ]
    return ContextFramePayload(
        session_id="sess-open-questions",
        chat_id=1001001001,
        trigger_message_id=412,
        current_message=current,
        recent_messages=[current],
        conversation_window_messages=[current],
        topic_summary="codex clarification",
        participants=["Fixture Sender"],
        mood="calm",
        open_questions=open_questions,
    )
