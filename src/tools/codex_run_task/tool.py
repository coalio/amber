from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.tools.base import BaseTool
from src.workflows.task_dispatch import TaskDispatchWorkflow

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
        return dispatch_codex_task(arguments, session)



def dispatch_codex_task(arguments: dict[str, Any], session: ToolSession) -> dict[str, Any]:
    workflow = session.runtime.task_dispatcher or TaskDispatchWorkflow(
        session.runtime.adapter_registry, session.runtime.state_store,
    )
    return workflow.dispatch(arguments, session.runtime.invocation)
