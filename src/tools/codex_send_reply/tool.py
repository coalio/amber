from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from src.adapters.codex import CodexAppServerRequestError
from src.adapters.linear.status import set_linear_status
from src.tools.base import BaseTool
from src.tools.codex_run_task.tool import dispatch_codex_task

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
            return _reply_error(
                "adapter_registry_unavailable",
                "the codex worker is not configured in this Amber runtime",
                "Adapter registry is not available.",
            )
        try:
            adapter = session.runtime.adapter_registry.require("codex")
        except RuntimeError as exc:
            return _reply_error("codex_adapter_unavailable", "the codex worker adapter is unavailable", str(exc))
        if not hasattr(adapter, "submit_tool_output"):
            return _reply_error(
                "codex_adapter_invalid",
                "the configured codex worker cannot accept clarification answers",
                "Configured codex adapter does not support tool output submission.",
            )
        app_server_id = str(arguments.get("app_server_id") or "")
        task_id = str(arguments.get("task_id") or "")
        tool_call_id = str(arguments.get("tool_call_id") or "")
        raw_answers = arguments.get("answers")
        summary = str(arguments.get("summary") or "").strip()
        if (
            not isinstance(raw_answers, list)
            or not raw_answers
            or any(not isinstance(item, str) or not item.strip() for item in raw_answers)
            or not summary
        ):
            return _reply_error(
                "invalid_clarification_answer",
                "the clarification answer was empty or malformed, so it was not submitted",
                "answers and summary must contain non-empty text.",
            )
        answers = [item.strip() for item in raw_answers]
        try:
            if isinstance(arguments.get("confidence"), bool):
                raise TypeError
            confidence = float(arguments.get("confidence") or 0.0)
        except (TypeError, ValueError):
            return _reply_error(
                "invalid_clarification_confidence",
                "the clarification confidence was malformed, so the answer was not submitted",
                "confidence must be a number.",
            )
        if not 0.0 <= confidence <= 1.0:
            return _reply_error(
                "invalid_clarification_confidence",
                "the clarification confidence was outside the accepted range, so the answer was not submitted",
                "confidence must be between 0 and 1.",
            )

        # reject model-generated identifiers that do not match durable state
        question = None
        if session.runtime.state_store is not None:
            question = session.runtime.state_store.open_question_by_codex_ids(
                app_server_id=app_server_id,
                task_id=task_id,
                tool_call_id=tool_call_id,
            )
            if question is None:
                return _reply_error(
                    "clarification_state_mismatch",
                    "the saved clarification no longer matches an open codex task, so the answer was not submitted",
                    "No matching open clarification exists in durable state.",
                )
        output = {
            "answers": answers,
            "summary": summary,
            "confidence": confidence,
        }
        try:
            response = adapter.submit_tool_output(
                app_server_id=app_server_id,
                task_id=task_id,
                tool_call_id=tool_call_id,
                output=output,
            )
        except CodexAppServerRequestError as exc:
            if exc.error_code in {"unknown_task", "task_runner_unavailable"}:
                return self._recover_lost_task(
                    session,
                    question=question,
                    answers=answers,
                    summary=summary,
                    original_error=exc,
                )
            if exc.error_code == "tool_output_conflict":
                cleared = None
                if session.runtime.state_store is not None:
                    cleared = session.runtime.state_store.clear_open_question_by_codex_ids(
                        app_server_id=app_server_id,
                        task_id=task_id,
                        tool_call_id=tool_call_id,
                    )
                return {
                    **_reply_error(exc.error_code, _submission_user_error(exc), str(exc)),
                    "cleared_open_question": cleared is not None,
                }
            return _reply_error(exc.error_code or "clarification_submit_failed", _submission_user_error(exc), str(exc))
        except RuntimeError as exc:
            return _reply_error(
                "clarification_submit_failed",
                "the codex worker rejected the clarification; the answer is still saved for retry",
                str(exc),
            )
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
            "recovered": False,
            "app_server_id": app_server_id,
            "task_id": task_id,
            "tool_call_id": tool_call_id,
        }

    def _recover_lost_task(
        self,
        session: ToolSession,
        *,
        question,
        answers: list[str],
        summary: str,
        original_error: CodexAppServerRequestError,
    ) -> dict[str, Any]:
        state_store = session.runtime.state_store
        if state_store is None or question is None:
            return _reply_error(
                "clarification_recovery_state_missing",
                "the saved codex process was lost and no durable clarification state was available to recover it",
                str(original_error),
            )
        task = state_store.codex_task_by_ids(app_server_id=question.app_server_id, task_id=question.task_id)
        if task is None or not task.thread_id:
            return _reply_error(
                "clarification_recovery_thread_missing",
                "the saved codex process was lost and no resumable thread was recorded; the answer is still saved",
                str(original_error),
            )

        # continue the same Codex thread only after the old process is definitively absent
        recovery = dispatch_codex_task(
            {
                "task_description": _recovery_task_description(question, answers, summary),
                "context": {**question.context, "codex_thread_id": task.thread_id},
            },
            session,
        )
        if recovery.get("error"):
            user_error = str(recovery.get("user_error") or "the codex worker could not resume the saved thread")
            return _reply_error(
                "clarification_recovery_failed",
                f"the saved codex process was lost and {user_error}; the answer is still saved",
                str(recovery.get("error") or original_error),
            )

        # clear the old question only after a replacement turn is durably linked
        cleared = state_store.clear_open_question_by_codex_ids(
            app_server_id=question.app_server_id,
            task_id=question.task_id,
            tool_call_id=question.tool_call_id,
        )
        return {
            "submitted": True,
            "response": {
                "status": "clarification_recovered",
                "previous_error_code": original_error.error_code,
            },
            "cleared_open_question": cleared is not None,
            "recovered": True,
            "app_server_id": recovery.get("app_server_id"),
            "task_id": recovery.get("task_id"),
            "tool_call_id": None,
            "thread_id": recovery.get("thread_id"),
            "turn_id": recovery.get("turn_id"),
        }


def _recovery_task_description(question, answers: list[str], summary: str) -> str:
    clarification = {
        "questions": list(question.questions),
        "answers": answers,
        "summary": summary,
    }
    return "\n\n".join(
        [
            question.task_description,
            "The prior worker process was lost while waiting for the user. Resume the existing thread from this answer.",
            json.dumps(clarification, ensure_ascii=False, indent=2),
            "Continue the original task. Do not ask again for information already answered above.",
        ]
    )


def _submission_user_error(exc: CodexAppServerRequestError) -> str:
    if exc.error_code in {"transport_error", "invalid_response"}:
        return "the connection to the codex worker failed; the answer is still saved for retry"
    if exc.error_code == "unknown_tool_call":
        return "the saved codex process no longer recognizes that pending question; the answer is still saved"
    if exc.error_code == "tool_output_conflict":
        return "an earlier answer was already committed, so this newer clarification was not applied"
    return "the codex worker rejected the clarification; the answer is still saved for retry"


def _reply_error(error_code: str, user_error: str, detail: str) -> dict[str, Any]:
    return {
        "error": detail,
        "error_code": error_code,
        "user_error": user_error,
    }
