from __future__ import annotations

from typing import Any

from src.adapters.linear.adapter import LinearAdapter
from src.adapters.registry import AdapterRegistry


def set_linear_status(
    adapter_registry: AdapterRegistry,
    *,
    issue_id: str,
    status: str,
    note: str | None = None,
) -> dict[str, Any]:
    adapter = adapter_registry.require("linear")
    if not isinstance(adapter, LinearAdapter):
        raise RuntimeError("Configured linear adapter has the wrong type.")
    return adapter.set_task_status(issue_id=issue_id, status=status, note=note)
