from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.tools.base import BaseTool

if TYPE_CHECKING:
    from src.tools.registry import ToolSession


class ManageMemory(BaseTool):
    name = "ManageMemory"
    description = "Create, update, rewrite, or forget Amber memory for a specific person."
    brief = "Manage normal memories and expertise/project-owner tags."
    arguments = {
        "operation": {
            "type": "string",
            "enum": ["create_memory", "create_expertise", "rewrite_memory", "forget_memory"],
            "description": "Memory operation to perform.",
        },
        "sender_id": {"type": "string", "description": "Telegram sender id for the target person."},
        "display_name": {"type": "string", "description": "Display name for the target person."},
        "text": {"type": ["string", "null"], "description": "Memory text for create/rewrite operations."},
        "tags": {"type": "array", "items": {"type": "string"}, "description": "Normal memory tags."},
        "memory_id": {"type": ["string", "null"], "description": "Target memory id for rewrite/forget operations."},
        "expertise_tags": {"type": "array", "items": {"type": "string"}, "description": "Expertise tags to add."},
        "project_owner_tags": {"type": "array", "items": {"type": "string"}, "description": "Project ownership tags to add."},
    }
    required_arguments = (
        "operation",
        "sender_id",
        "display_name",
        "text",
        "tags",
        "memory_id",
        "expertise_tags",
        "project_owner_tags",
    )

    def run(self, arguments: dict[str, Any], session: ToolSession) -> dict[str, Any]:
        memory_store = session.runtime.memory_store
        if memory_store is None:
            return {"error": "Memory store is not available."}
        operation = str(arguments.get("operation") or "")
        sender_id = str(arguments.get("sender_id") or "").strip()
        display_name = str(arguments.get("display_name") or "").strip()
        if not sender_id or not display_name:
            return {"error": "sender_id and display_name are required."}
        tags = [str(item).strip() for item in (arguments.get("tags") or []) if str(item).strip()]
        try:
            if operation == "create_memory":
                entry = memory_store.create_memory(sender_id, display_name, str(arguments.get("text") or ""), tags)
                return {"memory": entry.model_dump(mode="json")}
            if operation == "create_expertise":
                profile = memory_store.update_profile_tags(
                    sender_id,
                    display_name,
                    expertise_tags=[str(item).strip() for item in (arguments.get("expertise_tags") or []) if str(item).strip()],
                    project_owner_tags=[str(item).strip() for item in (arguments.get("project_owner_tags") or []) if str(item).strip()],
                )
                text = str(arguments.get("text") or "").strip()
                memory = None
                if text:
                    memory = memory_store.create_memory(sender_id, display_name, text, tags or ["expertise"])
                return {
                    "profile": profile.model_dump(mode="json"),
                    "memory": memory.model_dump(mode="json") if memory is not None else None,
                }
            if operation == "rewrite_memory":
                memory_id = str(arguments.get("memory_id") or "").strip()
                updated = memory_store.rewrite_memory(sender_id, display_name, memory_id, str(arguments.get("text") or ""), tags)
                return {"memory": updated.model_dump(mode="json") if updated is not None else None}
            if operation == "forget_memory":
                memory_id = str(arguments.get("memory_id") or "").strip()
                return {"forgot": memory_store.forget_memory(sender_id, display_name, memory_id)}
        except ValueError as exc:
            return {"error": str(exc)}
        return {"error": f"Unknown memory operation: {operation}"}
