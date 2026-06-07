from __future__ import annotations

from typing import Any, Protocol, TypeVar


SchemaT = TypeVar("SchemaT")


class ProviderSelectionConfig(Protocol):
    provider_name: str
    api_key: str | None


class ModelProvider(Protocol):
    def generate_structured(
        self,
        *,
        model: str,
        instructions: str,
        input_items: list[dict[str, Any]],
        schema: type[SchemaT],
        max_output_tokens: int,
        temperature: float,
        reasoning_effort: str | None = None,
        tools: Any | None = None,
    ) -> SchemaT:
        ...

    def generate_text(
        self,
        *,
        model: str,
        instructions: str,
        input_items: list[dict[str, Any]],
        max_output_tokens: int,
        temperature: float,
    ) -> str:
        ...
