from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.adapters.registry import AdapterRegistry
from src.attention.memory.store import MemoryStore
from src.config.config import Settings
from src.state.store import GlobalStateStore
from src.tools.registry import ToolRegistry, ToolRuntime, default_tool_registry
from src.utils.files import read_markdown


@dataclass(frozen=True)
class SemanticConfig:
    provider_name: str
    api_key: str | None
    model: str
    system_prompt: str
    interruption_prompt: str
    action_contract_prompt: str
    memory_prompt: str
    max_output_tokens: int
    temperature: float
    reasoning_effort: str = "medium"
    tool_registry: ToolRegistry | None = None
    tool_runtime: ToolRuntime | None = None

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        memory_store: MemoryStore | None = None,
        adapter_registry: AdapterRegistry | None = None,
        state_store: GlobalStateStore | None = None,
        telegram_transport: Any | None = None,
        codex_workspace: Path | None = None,
    ) -> "SemanticConfig":
        tool_registry = default_tool_registry() if settings.mode == "work" else None
        tool_runtime = (
            ToolRuntime(
                memory_store=memory_store,
                adapter_registry=adapter_registry,
                state_store=state_store,
                telegram_transport=telegram_transport,
                codex_workspace=codex_workspace or settings.codex_workdir,
            )
            if tool_registry is not None
            else None
        )
        system_prompt = "\n\n".join(
            [
                read_markdown(settings.ai_orchestration_prompt_path),
                read_markdown(settings.ai_system_prompt_path),
            ]
        )
        if tool_registry is not None:
            system_prompt = "\n\n".join([system_prompt, tool_registry.prompt_summary()])
        return cls(
            provider_name=settings.ai_provider,
            api_key=settings.ai_api_key,
            model=settings.ai_model,
            system_prompt=system_prompt,
            interruption_prompt=read_markdown(settings.ai_interruption_prompt_path),
            action_contract_prompt=read_markdown(settings.ai_action_contract_prompt_path),
            memory_prompt=read_markdown(settings.memory_prompt_path),
            max_output_tokens=settings.ai_max_output_tokens,
            temperature=settings.ai_temperature,
            tool_registry=tool_registry,
            tool_runtime=tool_runtime,
        )
