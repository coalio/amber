from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from threading import RLock
from typing import Any

from src.adapters.codex import CodexAdapter
from src.adapters.linear.status import set_linear_status
from src.adapters.registry import AdapterRegistry
from src.events.bus import EventBus, emitter_context
from src.events.delivery import TaskOrigin, ToolInvocation, WorkReceipt
from src.events.linear import LinearQueueWakeRequestedEvent, LinearQueueWakeRequestedPayload
from src.state.store import GlobalStateStore
from src.utils.time import utc_now


class TaskDispatchWorkflow:
    def __init__(
        self, adapter_registry: AdapterRegistry | None, state_store: GlobalStateStore | None,
        *, deliver_receipt: Callable[[WorkReceipt], int] | None = None,
        resolve_origin: Callable[[ToolInvocation], TaskOrigin | None] | None = None,
    ) -> None:
        self._adapters = adapter_registry
        self._state = state_store
        self._deliver_receipt = deliver_receipt
        self._resolve_origin = resolve_origin
        self._receipts: OrderedDict[tuple[str, int], int] = OrderedDict()
        self._receipt_lock = RLock()

    def dispatch(self, arguments: dict[str, Any], invocation: ToolInvocation | None = None) -> dict[str, Any]:
        # validate the request and resolve continuation before any external side effects
        if self._adapters is None:
            return _dispatch_error("adapter_registry_unavailable", "the codex worker is not configured in this Amber runtime",
                                   "Adapter registry is not available.")
        try:
            adapter = self._adapters.require("codex")
        except RuntimeError as exc:
            return _dispatch_error("codex_adapter_unavailable", "the codex worker adapter is unavailable", str(exc))
        if not isinstance(adapter, CodexAdapter):
            return _dispatch_error("codex_adapter_invalid", "the configured codex worker adapter is invalid",
                                   "Configured codex adapter has the wrong type.")
        raw_context = arguments.get("context") or {}
        if not isinstance(raw_context, dict):
            return _dispatch_error("invalid_task_context", "the task context was malformed", "context must be an object.")
        description = str(arguments.get("task_description") or "").strip()
        if not description:
            return _dispatch_error("invalid_task_description", "the task description was empty, so no codex worker was started",
                                   "task_description must contain non-empty text.")
        context = dict(raw_context)
        if not str(context.get("project") or "").strip() and context.get("linear_project"):
            context["project"] = str(context["linear_project"]).strip()
        issue_id = str(context.get("linear_issue_id") or "").strip()
        thread_id = self._resume_thread_id(context, issue_id)
        origin = self._resolve_origin(invocation) if invocation and self._resolve_origin else None

        # delivery must succeed before the worker can start; retries reuse the receipt
        receipt_id = self._receipt(invocation)
        request = {"task_description": description, "context": context}
        if origin is not None:
            request["origin"] = origin
        try:
            if thread_id:
                request["context"] = {**context, "codex_thread_id": thread_id}
                task = adapter.continue_task(thread_id=thread_id, **request)
            else:
                task = adapter.start_task(**request)
        except RuntimeError as exc:
            if issue_id and self._state is not None:
                self._state.mark_linear_task_error(issue_id=issue_id, error=str(exc), timestamp=utc_now())
            return _dispatch_error("codex_task_start_failed", "the codex worker could not start or resume the task", str(exc))

        # commit task provenance and its delivered receipt before returning to the model
        if self._state is not None:
            self._state.mark_codex_task_turn(
                app_server_id=task.app_server_id, task_id=task.task_id, thread_id=task.thread_id or thread_id,
                turn_id=task.turn_id, status=task.status, updated_at=utc_now(),
            )
            if receipt_id is not None and invocation is not None:
                self._state.bind_codex_task_outbound(
                    app_server_id=task.app_server_id, task_id=task.task_id, chat_id=invocation.chat_id,
                    message_ids=[receipt_id], updated_at=utc_now(),
                )
        if issue_id and self._state is not None:
            self._record_linear_start(issue_id, task, thread_id)
        return {
            "app_server_id": task.app_server_id, "task_id": task.task_id, "status": task.status,
            "thread_id": task.thread_id or thread_id, "turn_id": task.turn_id,
            "resumed": bool(thread_id), "work_acknowledged": receipt_id is not None,
        }

    def _receipt(self, invocation: ToolInvocation | None) -> int | None:
        if self._deliver_receipt is None or invocation is None or invocation.trigger_message_id <= 0:
            return None
        if invocation.source in {"linear_task_list", "codex_question", "codex_notification"}:
            return None
        # bound the receipt cache while retaining idempotency across harness retries
        key = (str(invocation.chat_id), invocation.trigger_message_id)
        with self._receipt_lock:
            if key not in self._receipts:
                self._receipts[key] = self._deliver_receipt(WorkReceipt(
                    invocation.chat_id, invocation.trigger_message_id, invocation.reply_to_message_id,
                ))
            self._receipts.move_to_end(key)
            while len(self._receipts) > 1024:
                self._receipts.popitem(last=False)
            return self._receipts[key]

    def _resume_thread_id(self, context: dict, issue_id: str) -> str | None:
        explicit = str(context.get("codex_thread_id") or "").strip()
        if explicit:
            return explicit
        if not issue_id or self._state is None:
            return None
        task = self._state.snapshot().linear_tasks.get(issue_id)
        if task and task.codex_thread_id and task.queue_status in {"under_review", "waiting_for_user"}:
            return task.codex_thread_id
        return None

    def _record_linear_start(self, issue_id, task, thread_id) -> None:
        # synchronize the queue after the worker transition is durable
        self._state.mark_linear_task_started(
            issue_id=issue_id, codex_app_server_id=task.app_server_id, codex_task_id=task.task_id,
            codex_thread_id=task.thread_id or thread_id, codex_turn_id=task.turn_id, started_at=utc_now(),
        )
        try:
            set_linear_status(self._adapters, issue_id=issue_id, status="in_progress", note=f"Amber started Codex task {task.task_id}.")
        except RuntimeError as exc:
            self._state.mark_linear_task_last_error(issue_id=issue_id, error=str(exc))
        with emitter_context("workflow.linear"):
            EventBus.emit(LinearQueueWakeRequestedEvent(
                chat_id="linear:queue", payload=LinearQueueWakeRequestedPayload(
                    reason="linear_task_started", issue_id=issue_id, requested_at=utc_now(),
                ),
            ))


def _dispatch_error(error_code: str, user_error: str, detail: str) -> dict[str, Any]:
    return {"error": detail, "error_code": error_code, "user_error": user_error}
