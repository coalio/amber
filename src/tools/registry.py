from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.adapters.registry import AdapterRegistry
from src.attention.memory.store import MemoryStore
from src.state.store import GlobalStateStore
from src.tools.base import BaseTool
from src.tools.codex_send_reply import CodexSendReply
from src.tools.codex_run_task import CodexRunTask
from src.tools.codex_workflow import CompletedCodexTransition, FailedCodexTransition, CodexWorkRoute, CodexWorkStateMachine
from src.tools.get_memory import GetMemory
from src.tools.get_tool import GetTool
from src.tools.manage_memory import ManageMemory
from src.tools.send_file import SendFile
from src.utils.logging import get_logger


GET_TOOL_NAME = "GetTool"


@dataclass
class ToolRuntime:
    memory_store: MemoryStore | None = None
    adapter_registry: AdapterRegistry | None = None
    state_store: GlobalStateStore | None = None
    telegram_transport: Any | None = None
    codex_workspace: Path | None = None


@dataclass(frozen=True)
class ToolExecution:
    name: str
    arguments: dict[str, Any]
    result: Any


class ToolRegistry:
    def __init__(self, tools: list[BaseTool]) -> None:
        self._tools = {tool.name: tool for tool in tools}
        if GET_TOOL_NAME not in self._tools:
            raise RuntimeError("Tool registry must include GetTool.")

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def tool_names(self, *, exclude_get_tool: bool = False) -> list[str]:
        names = sorted(self._tools)
        if exclude_get_tool:
            return [name for name in names if name != GET_TOOL_NAME]
        return names

    def new_session(
        self,
        runtime: ToolRuntime | None = None,
        *,
        codex_work_route: CodexWorkRoute = CodexWorkRoute.UNRESTRICTED,
        codex_workflow: CodexWorkStateMachine | None = None,
    ) -> ToolSession:
        return ToolSession(
            self,
            runtime=runtime,
            codex_work_route=codex_work_route,
            codex_workflow=codex_workflow,
        )

    def prompt_summary(self) -> str:
        lines = [
            "Work-mode tools are available.",
            (
                "GetTool is always enabled. If you need to use another tool, call GetTool with "
                'the exact {"tool_name": "..."} first to read its full schema and enable it.'
            ),
            "Available tool summaries:",
        ]
        for name in self.tool_names(exclude_get_tool=True):
            tool = self._tools[name]
            lines.append(f"- {tool.name}: {tool.summary}")
        return "\n".join(lines)


class ToolSession:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        runtime: ToolRuntime | None = None,
        codex_work_route: CodexWorkRoute = CodexWorkRoute.UNRESTRICTED,
        codex_workflow: CodexWorkStateMachine | None = None,
    ) -> None:
        self.registry = registry
        self.runtime = runtime or ToolRuntime()
        self._enabled_tool_names: list[str] = [GET_TOOL_NAME]
        self._executions: list[ToolExecution] = []
        self._codex_workflow = codex_workflow or CodexWorkStateMachine(codex_work_route)

    def enable(self, name: str) -> None:
        if self.registry.get(name) is None:
            raise RuntimeError(f"Unknown tool: {name}")
        access_error = self.tool_access_error(name)
        if access_error is not None:
            raise RuntimeError(access_error)
        if name not in self._enabled_tool_names:
            self._enabled_tool_names.append(name)

    def tool_access_error(self, name: str) -> str | None:
        return self._codex_workflow.access_error(name)

    def tool_definitions(self) -> list[dict[str, Any]]:
        definitions: list[dict[str, Any]] = []
        for name in self._enabled_tool_names:
            tool = self.registry.get(name)
            if tool is not None:
                definitions.append(tool.tool_definition())
        return definitions

    def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        logger = get_logger("amber.tools")
        access_error = self.tool_access_error(name)
        if access_error is not None:
            result = {"error": access_error}
            self._record_execution(name, arguments, result)
            logger.info(
                "tool.execute_denied",
                extra={"event": "tool.execute_denied", "context": _tool_argument_log_context(name, arguments)},
            )
            return result
        if name not in self._enabled_tool_names:
            result = {"error": f"Tool is not enabled: {name}"}
            self._record_execution(name, arguments, result)
            logger.info(
                "tool.execute_denied",
                extra={
                    "event": "tool.execute_denied",
                    "context": _tool_argument_log_context(name, arguments),
                },
            )
            return result
        tool = self.registry.get(name)
        if tool is None:
            result = {"error": f"Unknown tool: {name}"}
            self._record_execution(name, arguments, result)
            logger.info(
                "tool.execute_unknown",
                extra={
                    "event": "tool.execute_unknown",
                    "context": _tool_argument_log_context(name, arguments),
                },
            )
            return result

        # replay a committed transition without repeating its external side effect
        reused, reused_result = self._codex_workflow.replay(name, arguments)
        if reused:
            self._record_execution(name, arguments, reused_result)
            logger.info(
                "tool.execute_reused",
                extra={"event": "tool.execute_reused", "context": _tool_result_log_context(name, reused_result)},
            )
            return reused_result
        logger.info(
            "tool.execute",
            extra={"event": "tool.execute", "context": _tool_argument_log_context(name, arguments)},
        )
        result = tool.run(arguments, self)
        self._codex_workflow.record(name, arguments, result)
        self._record_execution(name, arguments, result)
        logger.info(
            "tool.result",
            extra={"event": "tool.result", "context": _tool_result_log_context(name, result)},
        )
        return result

    @property
    def executions(self) -> tuple[ToolExecution, ...]:
        return tuple(self._executions)

    @property
    def completed_codex_transition(self) -> CompletedCodexTransition | None:
        return self._codex_workflow.completed

    @property
    def last_codex_failure(self) -> FailedCodexTransition | None:
        return self._codex_workflow.last_failure

    def record_codex_failure(self, tool_name: str, result: dict[str, Any]) -> None:
        """Expose a route denial discovered while loading a Codex transition tool."""
        self._codex_workflow.record(tool_name, {}, result)

    def _record_execution(self, name: str, arguments: dict[str, Any], result: Any) -> None:
        self._executions.append(
            ToolExecution(
                name=name,
                arguments=deepcopy(arguments),
                result=deepcopy(result),
            )
        )


def default_tool_registry() -> ToolRegistry:
    return ToolRegistry([GetTool(), GetMemory(), ManageMemory(), CodexRunTask(), CodexSendReply(), SendFile()])


def _tool_argument_log_context(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    # log only shape metadata because any tool argument can contain user secrets
    context: dict[str, Any] = {
        "tool_name": tool_name,
        "argument_keys": sorted(str(key) for key in arguments),
        "argument_shapes": {str(key): _value_shape(value) for key, value in arguments.items()},
    }
    if tool_name == GET_TOOL_NAME:
        requested_tool_name = str(arguments.get("tool_name") or "")
        if requested_tool_name in {"CodexRunTask", "CodexSendReply", "GetMemory", "ManageMemory", "SendFile"}:
            context["requested_tool_name"] = requested_tool_name
    return context


def _tool_result_log_context(tool_name: str, result: Any) -> dict[str, Any]:
    # retain operational outcome without serializing tool-returned content
    context: dict[str, Any] = {"tool_name": tool_name, "outcome": "success"}
    if not isinstance(result, dict):
        context["result_shape"] = _value_shape(result)
        return context
    context["result_keys"] = sorted(str(key) for key in result)
    if result.get("error"):
        context["outcome"] = "error"
        context["error_code"] = str(result.get("error_code") or "unspecified")
    for key in ("status", "submitted", "recovered", "enabled"):
        value = result.get(key)
        if isinstance(value, str | bool | int | float):
            context[key] = value
    return context


def _value_shape(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return {"type": "string", "chars": len(value)}
    if isinstance(value, dict):
        return {"type": "object", "items": len(value)}
    if isinstance(value, list | tuple | set):
        return {"type": "array", "items": len(value)}
    if value is None:
        return {"type": "null"}
    return {"type": type(value).__name__}
