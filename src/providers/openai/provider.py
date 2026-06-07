from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, TypeVar

from openai import OpenAI
from openai._compat import model_parse_json
from openai.lib._pydantic import to_strict_json_schema

from src.providers.openai.models import get_openai_model_contract
from src.tools.registry import ToolSession
from src.utils.openai import extract_response_text


SchemaT = TypeVar("SchemaT")


@dataclass(frozen=True)
class _FunctionToolCall:
    name: str
    call_id: str
    arguments: dict[str, Any]


class OpenAIProvider:
    def __init__(self, api_key: str | None) -> None:
        if not api_key:
            raise RuntimeError("Missing OpenAI API key for model provider.")
        self._client = OpenAI(api_key=api_key)

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
        tools: ToolSession | None = None,
    ) -> SchemaT:
        contract = get_openai_model_contract(model)
        request: dict[str, Any] = {
            "model": model,
            "instructions": instructions,
            "input": input_items,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema.__name__,
                    "strict": True,
                    "schema": to_strict_json_schema(schema),
                }
            },
            "max_output_tokens": max_output_tokens,
        }
        if "temperature" not in contract.unsupported_request_fields:
            request["temperature"] = temperature
        if reasoning_effort is not None and contract.supports_reasoning_effort:
            request["reasoning"] = {"effort": reasoning_effort}
        response = self._create_response(request, tools=tools)
        output_text = extract_response_text(response)
        if not output_text:
            raise RuntimeError(f"Structured call returned no parsed output: {extract_response_text(response)}")
        return model_parse_json(schema, output_text)

    def generate_structured_with_metadata(
        self,
        *,
        model: str,
        instructions: str,
        input_items: list[dict[str, Any]],
        schema: type[SchemaT],
        max_output_tokens: int,
        temperature: float,
        reasoning_effort: str | None = None,
        previous_response_id: str | None = None,
        tools: ToolSession | None = None,
    ) -> tuple[SchemaT, str | None]:
        contract = get_openai_model_contract(model)
        request: dict[str, Any] = {
            "model": model,
            "instructions": instructions,
            "input": input_items,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema.__name__,
                    "strict": True,
                    "schema": to_strict_json_schema(schema),
                }
            },
            "max_output_tokens": max_output_tokens,
        }
        if previous_response_id is not None:
            request["previous_response_id"] = previous_response_id
        if "temperature" not in contract.unsupported_request_fields:
            request["temperature"] = temperature
        if reasoning_effort is not None and contract.supports_reasoning_effort:
            request["reasoning"] = {"effort": reasoning_effort}
        response = self._create_response(request, tools=tools)
        output_text = extract_response_text(response)
        if not output_text:
            raise RuntimeError(f"Structured call returned no parsed output: {extract_response_text(response)}")
        return model_parse_json(schema, output_text), getattr(response, "id", None)

    def generate_text(
        self,
        *,
        model: str,
        instructions: str,
        input_items: list[dict[str, Any]],
        max_output_tokens: int,
        temperature: float,
    ) -> str:
        contract = get_openai_model_contract(model)
        request: dict[str, Any] = {
            "model": model,
            "instructions": instructions,
            "input": input_items,
            "max_output_tokens": max_output_tokens,
        }
        if "temperature" not in contract.unsupported_request_fields:
            request["temperature"] = temperature
        response = self._client.responses.create(**request)
        return extract_response_text(response)

    def _create_response(self, request: dict[str, Any], *, tools: ToolSession | None = None):
        if tools is None:
            return self._client.responses.create(**request)

        active_request = dict(request)
        for _ in range(8):
            active_request["tools"] = tools.tool_definitions()
            response = self._client.responses.create(**active_request)
            tool_calls = self._function_tool_calls(response)
            if not tool_calls:
                return response
            response_id = getattr(response, "id", None)
            if not response_id:
                raise RuntimeError("Tool call response did not include an id for continuation.")
            active_request = dict(request)
            active_request["previous_response_id"] = response_id
            active_request["input"] = [
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": self._tool_output_text(tools.execute(call.name, call.arguments)),
                }
                for call in tool_calls
            ]
        raise RuntimeError("Tool call loop exceeded maximum iterations.")

    def _function_tool_calls(self, response: Any) -> list[_FunctionToolCall]:
        calls: list[_FunctionToolCall] = []
        for item in self._iter_response_output(response):
            if self._field(item, "type") != "function_call":
                continue
            name = self._field(item, "name")
            call_id = self._field(item, "call_id")
            if not isinstance(name, str) or not isinstance(call_id, str):
                continue
            calls.append(
                _FunctionToolCall(
                    name=name,
                    call_id=call_id,
                    arguments=self._parse_tool_arguments(self._field(item, "arguments")),
                )
            )
        return calls

    def _iter_response_output(self, response: Any) -> list[Any]:
        output = self._field(response, "output", [])
        if isinstance(output, list):
            return output
        return []

    def _field(self, item: Any, name: str, default: Any = None) -> Any:
        if isinstance(item, dict):
            return item.get(name, default)
        return getattr(item, name, default)

    def _parse_tool_arguments(self, arguments: Any) -> dict[str, Any]:
        if isinstance(arguments, dict):
            return arguments
        if not isinstance(arguments, str) or not arguments.strip():
            return {}
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
        return {}

    def _tool_output_text(self, result: Any) -> str:
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False)
