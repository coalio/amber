from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.adapters.codex import CodexAdapter
from src.adapters.linear.status import set_linear_status
from src.events.bus import EventBus, emitter_context
from src.events.linear import LinearQueueWakeRequestedEvent, LinearQueueWakeRequestedPayload
from src.tools.base import BaseTool
from src.utils.time import utc_now

if TYPE_CHECKING:
    from src.tools.registry import ToolSession


class CodexRunTask(BaseTool):
    name = "CodexRunTask"
    description = "Start a headless Codex app-server task through the local Codex adapter."
    brief = "Start Codex work in the local Podman app-server."
    arguments = {
        "task_description": {"type": "string", "description": "Concrete task for Codex to complete."},
        "context": {
            "type": "object",
            "description": "Strict optional task metadata for Codex. Put full details in task_description.",
            "properties": {
                "repository_url": {
                    "type": ["string", "null"],
                    "description": "Repository URL if the task targets a repository.",
                },
                "project": {
                    "type": ["string", "null"],
                    "description": "Project name or label if known.",
                },
                "feature_label": {
                    "type": ["string", "null"],
                    "description": "Short feature branch label if known.",
                },
                "requires_code_editing": {
                    "type": ["boolean", "null"],
                    "description": "Whether the task requires code edits.",
                },
                "notes": {
                    "type": ["string", "null"],
                    "description": "Any other concise metadata not already included in task_description.",
                },
                "linear_issue_id": {
                    "type": ["string", "null"],
                    "description": "Linear issue UUID when this task came from Linear.",
                },
                "linear_identifier": {
                    "type": ["string", "null"],
                    "description": "Linear issue identifier, such as ABC-123.",
                },
                "linear_url": {
                    "type": ["string", "null"],
                    "description": "Linear issue URL.",
                },
                "linear_project": {
                    "type": ["string", "null"],
                    "description": "Linear project name.",
                },
                "linear_milestone": {
                    "type": ["string", "null"],
                    "description": "Linear project milestone name.",
                },
                "linear_status": {
                    "type": ["string", "null"],
                    "description": "Linear status name at task start.",
                },
                "linear_due_date": {
                    "type": ["string", "null"],
                    "description": "Linear due date in YYYY-MM-DD format.",
                },
                "codex_thread_id": {
                    "type": ["string", "null"],
                    "description": "Existing Codex thread id to continue when resuming review follow-up work.",
                },
            },
        },
    }
    required_arguments = ("task_description", "context")

    def run(self, arguments: dict[str, Any], session: ToolSession) -> dict[str, Any]:
        if session.runtime.adapter_registry is None:
            return {"error": "Adapter registry is not available."}
        try:
            adapter = session.runtime.adapter_registry.require("codex")
        except RuntimeError as exc:
            return {"error": str(exc)}
        if not isinstance(adapter, CodexAdapter):
            return {"error": "Configured codex adapter has the wrong type."}
        raw_context = arguments.get("context") or {}
        if not isinstance(raw_context, dict):
            return {"error": "context must be an object."}
        context = self._normalized_context(raw_context)
        linear_issue_id = str(context.get("linear_issue_id") or "").strip()
        resume_thread_id = self._resume_thread_id(context, linear_issue_id, session)
        try:
            if resume_thread_id and hasattr(adapter, "continue_task"):
                task = adapter.continue_task(
                    thread_id=resume_thread_id,
                    task_description=str(arguments.get("task_description") or ""),
                    context={**context, "codex_thread_id": resume_thread_id},
                )
            else:
                task = adapter.start_task(
                    task_description=str(arguments.get("task_description") or ""),
                    context=context,
                )
        except RuntimeError as exc:
            if linear_issue_id and session.runtime.state_store is not None:
                session.runtime.state_store.mark_linear_task_error(
                    issue_id=linear_issue_id,
                    error=str(exc),
                    timestamp=utc_now(),
                )
            return {"error": str(exc)}
        if linear_issue_id and session.runtime.state_store is not None:
            session.runtime.state_store.mark_linear_task_started(
                issue_id=linear_issue_id,
                codex_app_server_id=task.app_server_id,
                codex_task_id=task.task_id,
                codex_thread_id=task.thread_id or resume_thread_id,
                codex_turn_id=task.turn_id,
                started_at=utc_now(),
            )
            try:
                set_linear_status(
                    session.runtime.adapter_registry,
                    issue_id=linear_issue_id,
                    status="in_progress",
                    note=f"Amber started Codex task {task.task_id}.",
                )
            except RuntimeError as exc:
                session.runtime.state_store.mark_linear_task_last_error(issue_id=linear_issue_id, error=str(exc))
            with emitter_context("tool.linear"):
                EventBus.emit(
                    LinearQueueWakeRequestedEvent(
                        chat_id="linear:queue",
                        payload=LinearQueueWakeRequestedPayload(
                            reason="linear_task_started",
                            issue_id=linear_issue_id,
                            requested_at=utc_now(),
                        ),
                    )
                )
        return {
            "app_server_id": task.app_server_id,
            "task_id": task.task_id,
            "status": task.status,
            "thread_id": task.thread_id or resume_thread_id,
            "turn_id": task.turn_id,
            "resumed": bool(resume_thread_id),
        }

    def _normalized_context(self, raw_context: dict[str, Any]) -> dict[str, Any]:
        context = dict(raw_context)
        project = str(context.get("project") or "").strip()
        linear_project = str(context.get("linear_project") or "").strip()
        if not project and linear_project:
            context["project"] = linear_project
        return context

    def _resume_thread_id(self, context: dict[str, Any], linear_issue_id: str, session: ToolSession) -> str | None:
        explicit = str(context.get("codex_thread_id") or "").strip()
        if explicit:
            return explicit
        if not linear_issue_id or session.runtime.state_store is None:
            return None
        task = session.runtime.state_store.snapshot().linear_tasks.get(linear_issue_id)
        if task is None or not task.codex_thread_id:
            return None
        if task.queue_status not in {"under_review", "waiting_for_user"}:
            return None
        return task.codex_thread_id
