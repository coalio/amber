from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.tools.base import BaseTool

if TYPE_CHECKING:
    from src.tools.registry import ToolSession


class GetMemory(BaseTool):
    name = "GetMemory"
    description = "Read profile metadata and memories for a specific person."
    brief = "Fetch a person's profile, expertise/project tags, and matching memories."
    arguments = {
        "sender_id": {"type": "string", "description": "Telegram sender id for the person."},
        "query": {"type": ["string", "null"], "description": "Optional search query for relevant memories."},
        "limit": {"type": "integer", "description": "Maximum memories to return."},
    }
    required_arguments = ("sender_id", "query", "limit")

    def run(self, arguments: dict[str, Any], session: ToolSession) -> dict[str, Any]:
        if session.runtime.memory_store is None:
            return {"error": "Memory store is not available."}
        sender_id = str(arguments.get("sender_id") or "").strip()
        if not sender_id:
            return {"error": "sender_id is required."}
        try:
            limit = int(arguments.get("limit") or 10)
        except (TypeError, ValueError):
            return {"error": "limit must be an integer."}
        query = arguments.get("query")
        return session.runtime.memory_store.read_user_memories(
            sender_id,
            query=str(query) if query is not None else None,
            limit=limit,
        )
