from __future__ import annotations

from types import SimpleNamespace

from src.ai.semantic.schema import SemanticDecisionSchema
from src.providers.openai.provider import OpenAIProvider
from src.tools.registry import default_tool_registry


class FakeResponsesClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            output_text=(
                '{"action":"ignore","reply_to_message_id":null,"chat_id":1001001001,'
                '"draft_text":null,"referenced_memory_ids":[],"confidence":0.25,'
                '"notes":[],"trigger_message_id":null,"session_id":null}'
            )
        )


class FakeOpenAIClient:
    def __init__(self) -> None:
        self.responses = FakeResponsesClient()


class ToolCallingResponsesClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._responses = [
            SimpleNamespace(
                id="resp_get_tool",
                output_text="",
                output=[
                    SimpleNamespace(
                        type="function_call",
                        name="GetTool",
                        call_id="call_get_tool",
                        arguments='{"tool_name":"CodexRunTask"}',
                    )
                ],
            ),
            SimpleNamespace(
                id="resp_run_task",
                output_text="",
                output=[
                    SimpleNamespace(
                        type="function_call",
                        name="CodexRunTask",
                        call_id="call_run_task",
                        arguments=(
                            '{"task_description":"create a tmp folder and write a small script",'
                            '"context":{"repository_url":null,"project":null,"feature_label":null,'
                            '"requires_code_editing":true,"notes":"provider unit test"}}'
                        ),
                    )
                ],
            ),
            SimpleNamespace(
                id="resp_final",
                output_text=(
                    '{"action":"ignore","reply_to_message_id":null,"chat_id":1001001001,'
                    '"draft_text":null,"referenced_memory_ids":[],"confidence":0.25,'
                    '"notes":[],"trigger_message_id":null,"session_id":null}'
                ),
                output=[],
            ),
        ]

    def create(self, **kwargs):
        self.calls.append(kwargs)
        _assert_strict_tool_definitions(kwargs.get("tools", []))
        return self._responses.pop(0)


class ToolCallingOpenAIClient:
    def __init__(self) -> None:
        self.responses = ToolCallingResponsesClient()


def test_generate_structured_uses_explicit_strict_json_schema(monkeypatch) -> None:
    fake_client = FakeOpenAIClient()
    monkeypatch.setattr("src.providers.openai.provider.OpenAI", lambda api_key: fake_client)
    provider = OpenAIProvider(api_key="test-key")

    result = provider.generate_structured(
        model="gpt-5.4",
        instructions="Return a semantic decision.",
        input_items=[{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
        schema=SemanticDecisionSchema,
        max_output_tokens=1200,
        temperature=0.3,
        reasoning_effort="medium",
    )

    assert result.action == "ignore"
    assert len(fake_client.responses.calls) == 1
    call = fake_client.responses.calls[0]
    assert "text" in call
    assert "format" in call["text"]
    assert call["text"]["format"]["type"] == "json_schema"
    assert call["text"]["format"]["name"] == "SemanticDecisionSchema"
    assert call["text"]["format"]["strict"] is True
    assert call["text"]["format"]["schema"]["type"] == "object"
    assert call["text"]["format"]["schema"]["additionalProperties"] is False
    assert "temperature" not in call
    assert call["reasoning"] == {"effort": "medium"}


def test_generate_structured_runs_work_mode_tool_loop(monkeypatch) -> None:
    fake_client = ToolCallingOpenAIClient()
    monkeypatch.setattr("src.providers.openai.provider.OpenAI", lambda api_key: fake_client)
    provider = OpenAIProvider(api_key="test-key")

    result = provider.generate_structured(
        model="gpt-5.4",
        instructions="Return a semantic decision.",
        input_items=[{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
        schema=SemanticDecisionSchema,
        max_output_tokens=1200,
        temperature=0.3,
        reasoning_effort="medium",
        tools=default_tool_registry().new_session(),
    )

    assert result.action == "ignore"
    assert len(fake_client.responses.calls) == 3

    first_call = fake_client.responses.calls[0]
    assert [tool["name"] for tool in first_call["tools"]] == ["GetTool"]

    second_call = fake_client.responses.calls[1]
    assert second_call["previous_response_id"] == "resp_get_tool"
    assert [tool["name"] for tool in second_call["tools"]] == ["GetTool", "CodexRunTask"]
    assert len(second_call["input"]) == 1
    get_tool_output = second_call["input"][0]
    assert get_tool_output["type"] == "function_call_output"
    assert get_tool_output["call_id"] == "call_get_tool"
    assert "CodexRunTask" in get_tool_output["output"]

    third_call = fake_client.responses.calls[2]
    assert third_call["previous_response_id"] == "resp_run_task"
    assert third_call["input"] == [
        {
            "type": "function_call_output",
            "call_id": "call_run_task",
            "output": '{"error": "Adapter registry is not available."}',
        }
    ]


def _assert_strict_tool_definitions(tools: list[dict]) -> None:
    for tool in tools:
        assert tool["strict"] is True
        _assert_strict_schema(tool["parameters"])


def _assert_strict_schema(schema: dict) -> None:
    schema_type = schema.get("type")
    is_object = schema_type == "object" or (isinstance(schema_type, list) and "object" in schema_type)
    is_array = schema_type == "array" or (isinstance(schema_type, list) and "array" in schema_type)
    if is_object:
        properties = schema.get("properties", {})
        assert schema.get("additionalProperties") is False
        assert set(schema.get("required", [])) == set(properties)
        for property_schema in properties.values():
            if isinstance(property_schema, dict):
                _assert_strict_schema(property_schema)
    if is_array and isinstance(schema.get("items"), dict):
        _assert_strict_schema(schema["items"])
