from __future__ import annotations

from src.adapters.codex.adapter import CodexAdapter, CodexPullRequestEvent, CodexTaskCompleted
from src.adapters.linear.status import set_linear_status
from src.adapters.registry import AdapterRegistry
from src.events.bus import EventBus, emitter_context
from src.events.linear import LinearQueueWakeRequestedEvent, LinearQueueWakeRequestedPayload
from src.state.store import GlobalStateStore
from src.utils.time import utc_now


class CodexTaskLifecycleHandler:
    def __init__(
        self,
        adapter: CodexAdapter,
        *,
        adapter_registry: AdapterRegistry,
        state_store: GlobalStateStore,
    ) -> None:
        self._adapter = adapter
        self._adapter_registry = adapter_registry
        self._state_store = state_store
        self._subscription_id: str | None = None
        self._pull_request_subscription_id: str | None = None

    def register(self) -> None:
        if self._subscription_id is None:
            self._subscription_id = self._adapter.subscribe_task_completed(self._handle_task_completed)
        if self._pull_request_subscription_id is None:
            self._pull_request_subscription_id = self._adapter.subscribe_pull_request_events(self._handle_pull_request_event)

    def unregister(self) -> None:
        if self._subscription_id is not None:
            self._adapter.unsubscribe(self._subscription_id)
            self._subscription_id = None
        if self._pull_request_subscription_id is not None:
            self._adapter.unsubscribe(self._pull_request_subscription_id)
            self._pull_request_subscription_id = None

    def _handle_task_completed(self, task_completed: CodexTaskCompleted) -> None:
        self._state_store.mark_codex_task_turn(
            app_server_id=task_completed.app_server_id,
            task_id=task_completed.task_id,
            thread_id=task_completed.thread_id,
            turn_id=task_completed.turn_id,
            status=task_completed.status,
            updated_at=utc_now(),
        )
        if task_completed.status not in {"completed", "succeeded"}:
            return
        self._state_store.mark_linear_task_codex_turn(
            app_server_id=task_completed.app_server_id,
            task_id=task_completed.task_id,
            thread_id=task_completed.thread_id,
            turn_id=task_completed.turn_id,
        )

    def _handle_pull_request_event(self, pull_request: CodexPullRequestEvent) -> None:
        if pull_request.event_type not in {"opened", "merged"}:
            return
        task = self._state_store.linear_task_by_codex_ids(
            app_server_id=pull_request.app_server_id,
            task_id=pull_request.task_id,
        )
        if task is None:
            return
        status_alias = "under_review" if pull_request.event_type == "opened" else "completed"
        self._state_store.mark_linear_task_pr_event(
            app_server_id=pull_request.app_server_id,
            task_id=pull_request.task_id,
            event_type=pull_request.event_type,
            pr_url=pull_request.pr_url,
            repository=pull_request.repository,
            pr_number=pull_request.pr_number,
            branch=pull_request.branch,
            title=pull_request.title,
            summary=pull_request.summary,
            timestamp=utc_now(),
        )
        try:
            result = set_linear_status(
                self._adapter_registry,
                issue_id=task.issue_id,
                status=status_alias,
                note=f"Pull request {pull_request.event_type}: {pull_request.pr_url}",
            )
        except RuntimeError as exc:
            self._state_store.mark_linear_task_last_error(issue_id=task.issue_id, error=str(exc))
            return
        issue_id = str(result.get("issue_id") or task.issue_id)
        if issue_id != task.issue_id:
            self._state_store.mark_linear_task_lifecycle_status(
                issue_id=issue_id,
                status_alias=status_alias,
                timestamp=utc_now(),
            )
        if pull_request.event_type == "merged":
            self._wake_linear_queue(issue_id)

    def _wake_linear_queue(self, issue_id: str) -> None:
        with emitter_context("tool.linear"):
            EventBus.emit(
                LinearQueueWakeRequestedEvent(
                    chat_id="linear:queue",
                    payload=LinearQueueWakeRequestedPayload(
                        reason="linear_pr_merged",
                        issue_id=issue_id,
                        requested_at=utc_now(),
                    ),
                )
            )
