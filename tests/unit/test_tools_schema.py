from __future__ import annotations

from src.tools.registry import default_tool_registry


def test_all_work_mode_tool_schemas_are_openai_strict() -> None:
    registry = default_tool_registry()

    for tool_name in registry.tool_names():
        tool = registry.get(tool_name)
        assert tool is not None
        definition = tool.tool_definition()
        assert definition["strict"] is True
        _assert_strict_schema(definition["parameters"])


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
