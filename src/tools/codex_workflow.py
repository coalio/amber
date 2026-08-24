from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from typing import Any


class CodexWorkRoute(str, Enum):
    UNRESTRICTED = "unrestricted"
    NONE = "none"
    START_TASK = "start_task"
    SUBMIT_CLARIFICATION = "submit_clarification"


_TOOL_ROUTES = {
    "CodexRunTask": CodexWorkRoute.START_TASK,
    "CodexSendReply": CodexWorkRoute.SUBMIT_CLARIFICATION,
}


@dataclass(frozen=True)
class CompletedCodexTransition:
    tool_name: str
    arguments: dict[str, Any]
    result: Any


@dataclass(frozen=True)
class FailedCodexTransition:
    tool_name: str
    result: Any


class CodexWorkStateMachine:
    def __init__(self, route: CodexWorkRoute = CodexWorkRoute.UNRESTRICTED) -> None:
        self._route = route
        self._completed: CompletedCodexTransition | None = None
        self._last_failure: FailedCodexTransition | None = None

    @property
    def route(self) -> CodexWorkRoute:
        return self._route

    @property
    def completed(self) -> CompletedCodexTransition | None:
        return deepcopy(self._completed)

    @property
    def last_failure(self) -> FailedCodexTransition | None:
        return deepcopy(self._last_failure)

    def access_error(self, tool_name: str) -> str | None:
        requested_route = _TOOL_ROUTES.get(tool_name)
        if requested_route is None:
            return None

        # keep model-selected tools inside the route chosen from structured context
        if self._route == CodexWorkRoute.NONE:
            return "No Codex work transition is allowed for this context."
        if self._route == CodexWorkRoute.SUBMIT_CLARIFICATION and requested_route != self._route:
            return (
                "An existing Codex task is waiting for clarification. Use CodexSendReply to resume it; "
                "do not start another task."
            )
        if self._route == CodexWorkRoute.START_TASK and requested_route != self._route:
            return "No Codex clarification is active for this context; CodexSendReply is unavailable."

        # permit only an idempotent replay after the turn has committed one transition
        if self._completed is not None and self._completed.tool_name != tool_name:
            return (
                f"Codex work already transitioned through {self._completed.tool_name}; "
                f"{tool_name} cannot run in the same model turn."
            )
        return None

    def replay(self, tool_name: str, arguments: dict[str, Any]) -> tuple[bool, Any]:
        if self._completed is None or self._completed.tool_name != tool_name:
            return False, None
        if self._completed.arguments != arguments:
            return True, {
                "error": (
                    f"{tool_name} already completed with different arguments in this model turn; "
                    "a second Codex transition is not allowed."
                )
            }
        return True, deepcopy(self._completed.result)

    def record(self, tool_name: str, arguments: dict[str, Any], result: Any) -> None:
        if self._completed is not None or tool_name not in _TOOL_ROUTES:
            return
        if not self._succeeded(tool_name, result):
            if (
                self._last_failure is not None
                and isinstance(result, dict)
                and result.get("error_code") == "codex_route_blocked"
            ):
                # A model's disallowed fallback attempt must not hide the concrete failure that caused it.
                return
            self._last_failure = FailedCodexTransition(tool_name=tool_name, result=deepcopy(result))
            return
        self._completed = CompletedCodexTransition(
            tool_name=tool_name,
            arguments=deepcopy(arguments),
            result=deepcopy(result),
        )
        self._last_failure = None

    def _succeeded(self, tool_name: str, result: Any) -> bool:
        if not isinstance(result, dict) or result.get("error"):
            return False
        if tool_name == "CodexRunTask":
            return bool(result.get("task_id") and result.get("status"))
        return tool_name == "CodexSendReply" and result.get("submitted") is True
