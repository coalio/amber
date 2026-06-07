from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.ai.semantic.config import SemanticConfig
from src.config.config import get_settings
from src.receiver.linear.config import LinearReceiverConfig


def test_semantic_tools_are_enabled_only_in_work_mode(monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("AMBER_MODE", "casual")
    casual_config = SemanticConfig.from_settings(get_settings())

    get_settings.cache_clear()
    monkeypatch.setenv("AMBER_MODE", "work")
    work_config = SemanticConfig.from_settings(get_settings())

    assert casual_config.tool_registry is None
    assert work_config.tool_registry is not None

    get_settings.cache_clear()


def test_linear_receiver_config_comes_from_settings(monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("AMBER_LINEAR_ENABLED", "1")
    monkeypatch.setenv("AMBER_LINEAR_API_KEY", "lin_api_key")
    monkeypatch.setenv("AMBER_LINEAR_API_URL", "https://linear.example/graphql")
    monkeypatch.setenv("AMBER_LINEAR_POLL_SECONDS", "1")
    monkeypatch.setenv("AMBER_LINEAR_DUE_WINDOW_DAYS", "3")

    config = LinearReceiverConfig.from_settings(get_settings())

    assert config.enabled is True
    assert config.api_key == "lin_api_key"
    assert config.api_url == "https://linear.example/graphql"
    assert config.poll_seconds == 1
    assert config.due_window_days == 3

    get_settings.cache_clear()


def test_linear_polling_config_is_validated_by_settings(monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("AMBER_LINEAR_POLL_SECONDS", "0")

    with pytest.raises(ValidationError):
        get_settings()

    get_settings.cache_clear()
