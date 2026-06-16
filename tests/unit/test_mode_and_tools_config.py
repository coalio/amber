from __future__ import annotations

import pytest
from pydantic import ValidationError

from src import runtime
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
    monkeypatch.setenv("AMBER_LINEAR_ISSUE_READY_TO_START_STATUSES", "Ready, Next")
    monkeypatch.setenv("AMBER_LINEAR_ISSUE_STATUS_STARTED", "Doing, Reviewing")

    settings = get_settings()
    config = LinearReceiverConfig.from_settings(settings)

    assert config.enabled is True
    assert config.api_key == "lin_api_key"
    assert config.api_url == "https://linear.example/graphql"
    assert config.poll_seconds == 1
    assert config.due_window_days == 3
    assert config.ready_to_start_statuses == ("Ready", "Next")
    assert settings.linear_issue_statuses["started"] == ("Doing", "Reviewing")
    assert settings.linear_issue_status_targets["in_progress"] == "Doing"
    assert settings.linear_issue_status_targets["under_review"] == "Reviewing"

    get_settings.cache_clear()


def test_linear_polling_config_is_validated_by_settings(monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("AMBER_LINEAR_POLL_SECONDS", "0")

    with pytest.raises(ValidationError):
        get_settings()

    get_settings.cache_clear()


def test_attention_scorer_defaults_to_heuristics(monkeypatch) -> None:
    monkeypatch.delenv("AMBER_ATTENTION_SCORER", raising=False)

    assert runtime._build_attention_scorer() is None


def test_modernbert_attention_scorer_is_explicit_opt_in(monkeypatch) -> None:
    class FakeScorer:
        pass

    class FakeModule:
        AttentionPolicyScorer = FakeScorer

    imported: list[str] = []

    def fake_import_module(name: str):
        imported.append(name)
        return FakeModule

    monkeypatch.setenv("AMBER_ATTENTION_SCORER", "modernbert")
    monkeypatch.setattr(runtime.importlib, "import_module", fake_import_module)

    scorer = runtime._build_attention_scorer()

    assert isinstance(scorer, FakeScorer)
    assert imported == ["src.attention.scoring.zero_shot"]


def test_modernbert_attention_scorer_can_use_configured_mode(monkeypatch) -> None:
    class FakeScorer:
        pass

    class FakeModule:
        AttentionPolicyScorer = FakeScorer

    imported: list[str] = []

    def fake_import_module(name: str):
        imported.append(name)
        return FakeModule

    monkeypatch.delenv("AMBER_ATTENTION_SCORER", raising=False)
    monkeypatch.setattr(runtime.importlib, "import_module", fake_import_module)

    scorer = runtime._build_attention_scorer(mode="modernbert")

    assert isinstance(scorer, FakeScorer)
    assert imported == ["src.attention.scoring.zero_shot"]
