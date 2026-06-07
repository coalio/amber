from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.tools.base import BaseTool

if TYPE_CHECKING:
    from src.tools.registry import ToolSession


class GetTool(BaseTool):
    name = "GetTool"
    description = "Read the full schema and enable a named work-mode tool for the current model turn."
    brief = "Always-enabled loader for reading and enabling another work-mode tool."
    arguments = {
        "tool_name": {
            "type": "string",
            "description": "Exact name of the tool to read and enable.",
        }
    }
    required_arguments = ("tool_name",)

    def run(self, arguments: dict[str, Any], session: ToolSession) -> dict[str, Any]:
        tool_name = str(arguments.get("tool_name") or "").strip()
        if not tool_name:
            return {"enabled": False, "error": "tool_name is required"}
        tool = session.registry.get(tool_name)
        if tool is None:
            return {
                "enabled": False,
                "error": f"Unknown tool: {tool_name}",
                "available_tools": session.registry.tool_names(exclude_get_tool=True),
            }
        session.enable(tool.name)
        return {
            "enabled": True,
            "tool": tool.full_information(),
        }
