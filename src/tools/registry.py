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

    def new_session(self, runtime: ToolRuntime | None = None) -> ToolSession:
        return ToolSession(self, runtime=runtime)

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
    def __init__(self, registry: ToolRegistry, *, runtime: ToolRuntime | None = None) -> None:
        self.registry = registry
        self.runtime = runtime or ToolRuntime()
        self._enabled_tool_names: list[str] = [GET_TOOL_NAME]
        self._executions: list[ToolExecution] = []

    def enable(self, name: str) -> None:
        if self.registry.get(name) is None:
            raise RuntimeError(f"Unknown tool: {name}")
        if name not in self._enabled_tool_names:
            self._enabled_tool_names.append(name)

    def tool_definitions(self) -> list[dict[str, Any]]:
        definitions: list[dict[str, Any]] = []
        for name in self._enabled_tool_names:
            tool = self.registry.get(name)
            if tool is not None:
                definitions.append(tool.tool_definition())
        return definitions

    def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        logger = get_logger("amber_blue.tools")
        if name not in self._enabled_tool_names:
            result = {"error": f"Tool is not enabled: {name}"}
            self._record_execution(name, arguments, result)
            logger.info(
                "tool.execute_denied",
                extra={
                    "event": "tool.execute_denied",
                    "context": {"tool_name": name, "arguments": arguments},
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
                    "context": {"tool_name": name, "arguments": arguments},
                },
            )
            return result
        logger.info(
            "tool.execute",
            extra={"event": "tool.execute", "context": {"tool_name": name, "arguments": arguments}},
        )
        result = tool.run(arguments, self)
        self._record_execution(name, arguments, result)
        logger.info(
            "tool.result",
            extra={"event": "tool.result", "context": {"tool_name": name, "result": result}},
        )
        return result

    @property
    def executions(self) -> tuple[ToolExecution, ...]:
        return tuple(self._executions)

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
