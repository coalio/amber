from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.adapters.linear.status import set_linear_status
from src.tools.base import BaseTool

if TYPE_CHECKING:
    from src.tools.registry import ToolSession


class CodexSendReply(BaseTool):
    name = "CodexSendReply"
    description = "Return structured clarification answers to a waiting Codex app-server tool call."
    brief = "Complete a Codex clarification request with structured answers."
    arguments = {
        "app_server_id": {"type": "string", "description": "Codex app-server id."},
        "task_id": {"type": "string", "description": "Codex task id."},
        "tool_call_id": {"type": "string", "description": "Codex AmberAskUserQuestion tool call id."},
        "answers": {"type": "array", "items": {"type": "string"}, "description": "Structured answers gathered from the user."},
        "summary": {"type": "string", "description": "Concise summary of the gathered information."},
        "confidence": {"type": "number", "description": "Confidence that the Codex questions are fully answered."},
    }
    required_arguments = ("app_server_id", "task_id", "tool_call_id", "answers", "summary", "confidence")

    def run(self, arguments: dict[str, Any], session: ToolSession) -> dict[str, Any]:
        if session.runtime.adapter_registry is None:
            return {"error": "Adapter registry is not available."}
        try:
            adapter = session.runtime.adapter_registry.require("codex")
        except RuntimeError as exc:
            return {"error": str(exc)}
        if not hasattr(adapter, "submit_tool_output"):
            return {"error": "Configured codex adapter does not support tool output submission."}
        app_server_id = str(arguments.get("app_server_id") or "")
        task_id = str(arguments.get("task_id") or "")
        tool_call_id = str(arguments.get("tool_call_id") or "")
        try:
            confidence = float(arguments.get("confidence") or 0.0)
        except (TypeError, ValueError):
            return {"error": "confidence must be a number."}
        output = {
            "answers": list(arguments.get("answers") or []),
            "summary": str(arguments.get("summary") or ""),
            "confidence": confidence,
        }
        try:
            response = adapter.submit_tool_output(
                app_server_id=app_server_id,
                task_id=task_id,
                tool_call_id=tool_call_id,
                output=output,
            )
        except RuntimeError as exc:
            return {"error": str(exc)}
        cleared = None
        if session.runtime.state_store is not None:
            cleared = session.runtime.state_store.clear_open_question_by_codex_ids(
                app_server_id=app_server_id,
                task_id=task_id,
                tool_call_id=tool_call_id,
            )
            linear_task = session.runtime.state_store.mark_linear_task_running_by_codex_ids(
                app_server_id=app_server_id,
                task_id=task_id,
            )
            if linear_task is not None:
                try:
                    set_linear_status(
                        session.runtime.adapter_registry,
                        issue_id=linear_task.issue_id,
                        status="in_progress",
                        note=f"User clarified Codex task {task_id}.",
                    )
                except RuntimeError as exc:
                    session.runtime.state_store.mark_linear_task_last_error(
                        issue_id=linear_task.issue_id,
                        error=str(exc),
                    )
        return {
            "submitted": True,
            "response": response,
            "cleared_open_question": cleared is not None,
        }
