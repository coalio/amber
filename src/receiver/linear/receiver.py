from __future__ import annotations

import threading
from datetime import date, timedelta

from src.events.bus import EventBus, emitter_context
from src.events.linear import (
    LinearQueueWakeRequestedEvent,
    LinearTaskListPayload,
    LinearTaskListReceivedEvent,
    LinearTaskPayload,
)
from src.adapters.linear.client import LinearGraphQLClient
from src.adapters.linear.models import LinearIssue
from src.state.store import GlobalStateStore
from src.utils.logging import get_logger
from src.utils.time import local_now, utc_now


class LinearReceiver:
    def __init__(
        self,
        *,
        client: LinearGraphQLClient,
        state_store: GlobalStateStore,
        timezone_name: str,
        poll_seconds: float,
        due_window_days: int,
    ) -> None:
        self._client = client
        self._state_store = state_store
        self._timezone_name = timezone_name
        self._poll_seconds = poll_seconds
        self._due_window_days = due_window_days
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._wake_subscription_id: str | None = None
        self._logger = get_logger("amber_blue.receiver.linear")

    def register(self) -> None:
        if self._wake_subscription_id is None:
            self._wake_subscription_id = EventBus.subscribe("LinearQueueWakeRequestedEvent", self.handle_queue_wake)
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._poll_loop, name="linear-receiver-poll", daemon=True)
        self._thread.start()

    def unregister(self) -> None:
        if self._wake_subscription_id is not None:
            EventBus.unsubscribe(self._wake_subscription_id)
            self._wake_subscription_id = None
        self._stop.set()

    def handle_queue_wake(self, event: LinearQueueWakeRequestedEvent) -> None:
        self._logger.info(
            "linear.queue_wake_requested",
            extra={
                "event": "linear.queue_wake_requested",
                "context": {
                    "reason": event.payload.reason,
                    "issue_id": event.payload.issue_id,
                },
            },
        )
        threading.Thread(target=self.poll_once, name="linear-receiver-wake", daemon=True).start()

    def poll_once(self) -> None:
        with emitter_context("receiver.linear"):
            try:
                issues = self._client.assigned_issues()
            except Exception as exc:
                self._logger.exception(
                    "linear.poll_failed",
                    extra={"event": "linear.poll_failed", "context": {"error": str(exc)}},
                )
                return
            today = local_now(self._timezone_name).date()
            window_end = today + timedelta(days=self._due_window_days - 1)
            for issue in issues:
                if issue.is_terminal:
                    self._state_store.mark_linear_task_lifecycle_status(
                        issue_id=issue.id,
                        status_alias="completed",
                        timestamp=utc_now(),
                    )
            due_issues = [issue for issue in issues if self._issue_is_in_due_window(issue, today, window_end)]
            self._state_store.sync_linear_queue(
                [issue.to_queue_payload() for issue in due_issues],
                seen_at=utc_now(),
            )
            self._emit_if_available(today=today, window_end=window_end)

    def _poll_loop(self) -> None:
        self.poll_once()
        while not self._stop.wait(self._poll_seconds):
            self.poll_once()

    def _issue_is_in_due_window(self, issue: LinearIssue, today: date, window_end: date) -> bool:
        if issue.is_terminal:
            return False
        if not str(issue.project or "").strip():
            return False
        if (issue.state.name if issue.state is not None else None) != "Planned":
            return False
        if issue.due_date is None:
            return False
        return today <= issue.due_date <= window_end

    def _emit_if_available(self, *, today: date, window_end: date) -> None:
        tasks = self._state_store.available_linear_tasks()
        if not tasks:
            busy_reason = self._state_store.linear_busy_reason()
            if busy_reason is not None:
                self._logger.info(
                    "linear.queue_emit_skipped",
                    extra={"event": "linear.queue_emit_skipped", "context": {"reason": busy_reason}},
                )
            return
        queue_hash = self._state_store.linear_available_queue_hash()
        snapshot = self._state_store.snapshot()
        if snapshot.linear_last_emitted_queue_hash == queue_hash:
            self._logger.info(
                "linear.queue_emit_skipped",
                extra={"event": "linear.queue_emit_skipped", "context": {"reason": "unchanged_queue"}},
            )
            return
        payload = LinearTaskListPayload(
            tasks=[LinearTaskPayload.model_validate(task.ai_payload()) for task in tasks],
            generated_at=utc_now(),
            window_start_date=today.isoformat(),
            window_end_date=window_end.isoformat(),
            queue_hash=queue_hash,
        )
        self._state_store.mark_linear_queue_emitted(queue_hash)
        EventBus.emit(LinearTaskListReceivedEvent(chat_id="linear:queue", payload=payload))
