from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from src.adapters.codex import CodexAdapter, CodexTask
from src.adapters.linear import LinearAdapter
from src.adapters.registry import AdapterRegistry
from src.attention.memory.store import MemoryStore
from src.context.config import ContextConfig
from src.context.pipeline import ContextLayer
from src.events.bus import EventBus
from src.events.codex import CodexQuestionPayload, CodexQuestionReceivedEvent
from src.events.linear import LinearTaskListReceivedEvent
from src.adapters.linear import LinearIssue, LinearWorkflowState
from src.receiver.linear.receiver import LinearReceiver
from src.state.store import GlobalStateStore
from src.tools.codex_run_task import CodexRunTask
from src.tools.get_tool import GetTool
from src.tools.registry import ToolRegistry, ToolRuntime
from src.utils.message_archive import MessageArchive
from src.utils.scheduler import RuntimeScheduler
from src.utils.time import local_now, utc_now


def test_linear_receiver_emits_only_planned_project_tasks_due_today_or_tomorrow(tmp_path) -> None:
    EventBus.reset_for_tests()
    timezone_name = "America/Managua"
    today = local_now(timezone_name).date()
    state_store = GlobalStateStore(tmp_path / "state.json", timezone_name)
    receiver = LinearReceiver(
        client=FakeLinearClient(
            [
                _issue("issue-today", "LIN-1", today),
                _issue("issue-tomorrow", "LIN-2", today + timedelta(days=1)),
                _issue("issue-overdue", "LIN-3", today - timedelta(days=1)),
                _issue("issue-later", "LIN-4", today + timedelta(days=2)),
                _issue("issue-no-due", "LIN-5", None),
                _issue("issue-done", "LIN-6", today, state_type="completed"),
                _issue("issue-backlog", "LIN-7", today, state_name="Backlog"),
                _issue("issue-no-project", "LIN-8", today, project=None),
            ]
        ),
        state_store=state_store,
        timezone_name=timezone_name,
        poll_seconds=60,
        due_window_days=2,
    )
    events: list[LinearTaskListReceivedEvent] = []
    EventBus.subscribe("LinearTaskListReceivedEvent", events.append)

    receiver.poll_once()

    assert len(events) == 1
    assert [task.identifier for task in events[0].payload.tasks] == ["LIN-1", "LIN-2"]


def test_linear_receiver_allows_other_projects_while_one_project_is_busy(tmp_path) -> None:
    EventBus.reset_for_tests()
    timezone_name = "America/Managua"
    today = local_now(timezone_name).date()
    state_store = GlobalStateStore(tmp_path / "state.json", timezone_name)
    receiver = LinearReceiver(
        client=FakeLinearClient(
            [
                _issue("issue-a", "LIN-1", today, project="Project A"),
                _issue("issue-b", "LIN-2", today, project="Project B"),
                _issue("issue-c", "LIN-3", today, project="Project A"),
            ]
        ),
        state_store=state_store,
        timezone_name=timezone_name,
        poll_seconds=60,
        due_window_days=2,
    )
    events: list[LinearTaskListReceivedEvent] = []
    EventBus.subscribe("LinearTaskListReceivedEvent", events.append)

    receiver.poll_once()
    state_store.mark_linear_task_started(
        issue_id="issue-a",
        codex_app_server_id="codex-sandbox",
        codex_task_id="task-a",
        started_at=utc_now(),
    )
    receiver.poll_once()
    state_store.mark_linear_task_lifecycle_status(issue_id="issue-b", status_alias="under_review", timestamp=utc_now())
    receiver.poll_once()

    assert len(events) == 2
    assert [task.identifier for task in events[0].payload.tasks] == ["LIN-1", "LIN-2", "LIN-3"]
    assert [task.identifier for task in events[1].payload.tasks] == ["LIN-2"]


def test_linear_receiver_blocks_same_project_while_running_waiting_or_under_review(tmp_path) -> None:
    EventBus.reset_for_tests()
    timezone_name = "America/Managua"
    today = local_now(timezone_name).date()
    events: list[LinearTaskListReceivedEvent] = []
    EventBus.subscribe("LinearTaskListReceivedEvent", events.append)

    for status_alias in ("in_progress", "under_review"):
        state_store = GlobalStateStore(tmp_path / f"{status_alias}.json", timezone_name)
        receiver = LinearReceiver(
            client=FakeLinearClient(
                [
                    _issue(f"issue-a-{status_alias}", "LIN-1", today, project="Project A"),
                    _issue(f"issue-b-{status_alias}", "LIN-2", today, project="Project A"),
                ]
            ),
            state_store=state_store,
            timezone_name=timezone_name,
            poll_seconds=60,
            due_window_days=2,
        )
        before_count = len(events)
        receiver.poll_once()
        state_store.mark_linear_task_started(
            issue_id=f"issue-a-{status_alias}",
            codex_app_server_id="codex-sandbox",
            codex_task_id=f"task-{status_alias}",
            started_at=utc_now(),
        )
        state_store.mark_linear_task_lifecycle_status(
            issue_id=f"issue-a-{status_alias}",
            status_alias=status_alias,
            timestamp=utc_now(),
        )
        receiver.poll_once()
        assert len(events) == before_count + 1

    state_store = GlobalStateStore(tmp_path / "waiting.json", timezone_name)
    receiver = LinearReceiver(
        client=FakeLinearClient(
            [
                _issue("issue-a-waiting", "LIN-1", today, project="Project A"),
                _issue("issue-b-waiting", "LIN-2", today, project="Project A"),
            ]
        ),
        state_store=state_store,
        timezone_name=timezone_name,
        poll_seconds=60,
        due_window_days=2,
    )
    before_count = len(events)
    receiver.poll_once()
    state_store.mark_linear_task_started(
        issue_id="issue-a-waiting",
        codex_app_server_id="codex-sandbox",
        codex_task_id="task-waiting",
        started_at=utc_now(),
    )
    state_store.mark_linear_task_waiting_for_user(issue_id="issue-a-waiting")
    receiver.poll_once()
    assert len(events) == before_count + 1


def test_linear_receiver_orders_by_due_date_then_linear_priority(tmp_path) -> None:
    EventBus.reset_for_tests()
    timezone_name = "America/Managua"
    today = local_now(timezone_name).date()
    tomorrow = today + timedelta(days=1)
    state_store = GlobalStateStore(tmp_path / "state.json", timezone_name)
    receiver = LinearReceiver(
        client=FakeLinearClient(
            [
                _issue("issue-low", "LIN-4", today, priority=4, project="Project D"),
                _issue("issue-none", "LIN-5", today, priority=0, project="Project E"),
                _issue("issue-urgent", "LIN-1", today, priority=1, project="Project A"),
                _issue("issue-medium", "LIN-3", today, priority=3, project="Project C"),
                _issue("issue-high-tomorrow", "LIN-6", tomorrow, priority=2, project="Project F"),
                _issue("issue-high", "LIN-2", today, priority=2, project="Project B"),
            ]
        ),
        state_store=state_store,
        timezone_name=timezone_name,
        poll_seconds=60,
        due_window_days=2,
    )
    events: list[LinearTaskListReceivedEvent] = []
    EventBus.subscribe("LinearTaskListReceivedEvent", events.append)

    receiver.poll_once()

    assert [task.identifier for task in events[0].payload.tasks] == ["LIN-1", "LIN-2", "LIN-3", "LIN-4", "LIN-5", "LIN-6"]


def test_codex_run_task_records_selected_linear_task(tmp_path) -> None:
    timezone_name = "America/Managua"
    state_store = GlobalStateStore(tmp_path / "state.json", timezone_name)
    state_store.sync_linear_queue(
        [
            {
                "issue_id": "issue-a",
                "identifier": "LIN-1",
                "title": "Small task",
                "due_date": local_now(timezone_name).date().isoformat(),
                "status": "Planned",
                "project": "Amber Blue",
            }
        ],
        seen_at=utc_now(),
    )
    linear_client = FakeLinearMutationClient()
    adapter_registry = AdapterRegistry([FakeCodexAdapter(), LinearAdapter(api_key=None, client=linear_client)])
    session = ToolRegistry([GetTool(), CodexRunTask()]).new_session(
        runtime=ToolRuntime(adapter_registry=adapter_registry, state_store=state_store)
    )
    session.enable("CodexRunTask")

    result = session.execute(
        "CodexRunTask",
        {
            "task_description": "Implement LIN-1.",
            "context": {
                "repository_url": None,
                "project": None,
                "feature_label": "LIN-1-small-task",
                "requires_code_editing": True,
                "notes": None,
                "linear_issue_id": "issue-a",
                "linear_identifier": "LIN-1",
                "linear_url": "https://linear.app/test/issue/LIN-1",
                "linear_project": None,
                "linear_milestone": None,
                "linear_status": "Todo",
                "linear_due_date": local_now(timezone_name).date().isoformat(),
            },
        },
    )

    assert result["task_id"] == "task-linear"
    task = state_store.snapshot().linear_tasks["issue-a"]
    assert task.queue_status == "codex_running"
    assert task.codex_task_id == "task-linear"
    assert task.codex_thread_id == "thread-linear"
    assert task.codex_turn_id == "turn-linear"
    assert linear_client.status_updates == [("issue-a", "In Progress")]


def test_codex_run_task_uses_linear_project_as_project_context(tmp_path) -> None:
    timezone_name = "America/Managua"
    state_store = GlobalStateStore(tmp_path / "state.json", timezone_name)
    codex_adapter = CapturingCodexAdapter()
    adapter_registry = AdapterRegistry([codex_adapter])
    session = ToolRegistry([GetTool(), CodexRunTask()]).new_session(
        runtime=ToolRuntime(adapter_registry=adapter_registry, state_store=state_store)
    )
    session.enable("CodexRunTask")

    session.execute(
        "CodexRunTask",
        {
            "task_description": "Implement LIN-1.",
            "context": {
                "repository_url": None,
                "project": None,
                "feature_label": "LIN-1-small-task",
                "requires_code_editing": True,
                "notes": None,
                "linear_project": "Amber Blue",
            },
        },
    )

    assert codex_adapter.started_tasks[0]["context"]["project"] == "Amber Blue"


def test_codex_run_task_resumes_stored_linear_thread_for_review_followup(tmp_path) -> None:
    timezone_name = "America/Managua"
    state_store = GlobalStateStore(tmp_path / "state.json", timezone_name)
    state_store.sync_linear_queue(
        [
            {
                "issue_id": "issue-a",
                "identifier": "LIN-1",
                "title": "Small task",
                "due_date": local_now(timezone_name).date().isoformat(),
                "status": "Planned",
                "project": "Amber Blue",
            }
        ],
        seen_at=utc_now(),
    )
    state_store.mark_linear_task_started(
        issue_id="issue-a",
        codex_app_server_id="codex-sandbox",
        codex_task_id="task-original",
        codex_thread_id="thread-original",
        codex_turn_id="turn-original",
        started_at=utc_now(),
    )
    state_store.mark_linear_task_lifecycle_status(issue_id="issue-a", status_alias="under_review", timestamp=utc_now())
    codex_adapter = ResumingCodexAdapter()
    linear_client = FakeLinearMutationClient()
    adapter_registry = AdapterRegistry([codex_adapter, LinearAdapter(api_key=None, client=linear_client)])
    session = ToolRegistry([GetTool(), CodexRunTask()]).new_session(
        runtime=ToolRuntime(adapter_registry=adapter_registry, state_store=state_store)
    )
    session.enable("CodexRunTask")

    result = session.execute(
        "CodexRunTask",
        {
            "task_description": "Address review comments for LIN-1.",
            "context": {
                "linear_issue_id": "issue-a",
                "linear_identifier": "LIN-1",
                "linear_project": "Amber Blue",
                "requires_code_editing": True,
            },
        },
    )

    task = state_store.snapshot().linear_tasks["issue-a"]
    assert result["resumed"] is True
    assert codex_adapter.continued_thread_ids == ["thread-original"]
    assert task.codex_task_id == "task-resumed"
    assert task.codex_thread_id == "thread-original"
    assert task.codex_turn_id == "turn-resumed"
    assert task.queue_status == "codex_running"
    assert linear_client.status_updates == [("issue-a", "In Progress")]


def test_codex_question_marks_linear_task_waiting_and_keeps_linear_in_progress(tmp_path) -> None:
    EventBus.reset_for_tests()
    timezone_name = "America/Managua"
    state_store = GlobalStateStore(tmp_path / "state.json", timezone_name)
    state_store.sync_linear_queue(
        [
            {
                "issue_id": "issue-a",
                "identifier": "LIN-1",
                "title": "Small task",
                "due_date": local_now(timezone_name).date().isoformat(),
                "status": "Planned",
                "project": "Amber Blue",
            }
        ],
        seen_at=utc_now(),
    )
    state_store.mark_linear_task_started(
        issue_id="issue-a",
        codex_app_server_id="codex-sandbox",
        codex_task_id="task-linear",
        started_at=utc_now(),
    )
    linear_client = FakeLinearMutationClient()
    ContextLayer(
        ContextConfig(
            debounce_seconds=0.0,
            idle_timeout_seconds=60.0,
            competing_chat_timeout_seconds=15.0,
            recent_message_budget=8,
            max_compacted_facts=6,
            disable_sleep_state=True,
            initial_engagement_delay_min_seconds=0.0,
            initial_engagement_delay_max_seconds=0.0,
        ),
        state_store,
        RuntimeScheduler.instance(),
        MessageArchive.instance(),
        MemoryStore(tmp_path / "memories"),
        timezone_name,
        adapter_registry=AdapterRegistry([LinearAdapter(api_key=None, client=linear_client)]),
    ).handle_codex_question(
        CodexQuestionReceivedEvent(
            chat_id="codex:task-linear",
            payload=CodexQuestionPayload(
                app_server_id="codex-sandbox",
                task_id="task-linear",
                tool_call_id="tool-1",
                questions=["Need input?"],
                task_description="Implement LIN-1.",
                context={"linear_issue_id": "issue-a"},
                created_at=utc_now(),
            ),
        )
    )

    task = state_store.snapshot().linear_tasks["issue-a"]
    assert task.queue_status == "waiting_for_user"
    assert linear_client.status_updates == [("issue-a", "In Progress")]


class FakeLinearClient:
    def __init__(self, issues: list[LinearIssue]) -> None:
        self._issues = issues

    def assigned_issues(self) -> list[LinearIssue]:
        return list(self._issues)


class FakeLinearMutationClient:
    def __init__(self) -> None:
        self.status_updates: list[tuple[str, str]] = []

    def update_issue_status(self, *, issue_id: str, status_name: str) -> dict[str, Any]:
        self.status_updates.append((issue_id, status_name))
        return {
            "success": True,
            "issue": {
                "id": issue_id,
                "identifier": "LIN-1",
                "url": "https://linear.app/test/issue/LIN-1",
                "state": {"name": status_name},
            },
        }


class FakeCodexAdapter(CodexAdapter):
    def __init__(self) -> None:
        return None

    def start_task(self, *, task_description: str, context: dict[str, Any] | None = None) -> CodexTask:
        return CodexTask(
            app_server_id="codex-sandbox",
            task_id="task-linear",
            status="started",
            thread_id="thread-linear",
            turn_id="turn-linear",
        )


class CapturingCodexAdapter(CodexAdapter):
    def __init__(self) -> None:
        self.started_tasks: list[dict[str, Any]] = []

    def start_task(self, *, task_description: str, context: dict[str, Any] | None = None) -> CodexTask:
        self.started_tasks.append({"task_description": task_description, "context": dict(context or {})})
        return CodexTask(app_server_id="codex-sandbox", task_id="task-captured", status="started")


class ResumingCodexAdapter(CodexAdapter):
    def __init__(self) -> None:
        self.continued_thread_ids: list[str] = []

    def start_task(self, *, task_description: str, context: dict[str, Any] | None = None) -> CodexTask:
        raise AssertionError("start_task should not be called for review follow-up")

    def continue_task(
        self,
        *,
        thread_id: str,
        task_description: str,
        context: dict[str, Any] | None = None,
    ) -> CodexTask:
        self.continued_thread_ids.append(thread_id)
        return CodexTask(
            app_server_id="codex-sandbox",
            task_id="task-resumed",
            status="started",
            thread_id=thread_id,
            turn_id="turn-resumed",
        )


def _issue(
    issue_id: str,
    identifier: str,
    due_date,
    *,
    state_type: str = "unstarted",
    state_name: str = "Planned",
    priority: int | None = 0,
    project: str | None = "Amber Blue",
) -> LinearIssue:
    return LinearIssue(
        id=issue_id,
        identifier=identifier,
        title=f"Task {identifier}",
        due_date=due_date,
        priority=priority,
        updated_at=datetime.now(timezone.utc),
        state=LinearWorkflowState(id=f"state-{identifier}", name=state_name, type=state_type),
        project=project,
    )
