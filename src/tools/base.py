from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.tools.registry import ToolSession


class BaseTool(ABC):
    name: str
    description: str
    arguments: dict[str, Any]
    required_arguments: tuple[str, ...] = ()
    brief: str | None = None

    @property
    def summary(self) -> str:
        return self.brief or self.description

    def parameter_schema(self) -> dict[str, Any]:
        return _strict_schema(
            {
                "type": "object",
                "properties": dict(self.arguments),
            }
        )

    def tool_definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameter_schema(),
            "strict": True,
        }

    def full_information(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "arguments": dict(self.arguments),
            "required_arguments": list(self.required_arguments),
            "parameters": self.parameter_schema(),
        }

    @abstractmethod
    def run(self, arguments: dict[str, Any], session: ToolSession) -> Any:
        ...


def _strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    strict = deepcopy(schema)
    schema_type = strict.get("type")
    is_object = schema_type == "object" or (isinstance(schema_type, list) and "object" in schema_type)
    is_array = schema_type == "array" or (isinstance(schema_type, list) and "array" in schema_type)
    if is_object:
        properties = strict.get("properties")
        if not isinstance(properties, dict):
            properties = {}
            strict["properties"] = properties
        strict["required"] = list(properties)
        strict["additionalProperties"] = False
        for name, property_schema in list(properties.items()):
            if isinstance(property_schema, dict):
                properties[name] = _strict_schema(property_schema)
    if is_array and isinstance(strict.get("items"), dict):
        strict["items"] = _strict_schema(strict["items"])
    return strict
