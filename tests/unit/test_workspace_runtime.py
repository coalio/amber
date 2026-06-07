from __future__ import annotations

from pathlib import Path

from src.adapters.codex import build_codex_adapter, exec_in_codex_sandbox
from src.ai.semantic.config import SemanticConfig
from src.config.config import get_settings
from src.config.workspace import init_workspace, install_user_service, render_user_service


def test_workspace_init_creates_fixed_layout(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AMBER_HOME", str(tmp_path / ".amber"))
    get_settings.cache_clear()

    workspace = init_workspace("indiedreamers")

    assert workspace == tmp_path / ".amber" / "workspaces" / "indiedreamers"
    assert (workspace / "config.toml").exists()
    assert (workspace / "prompts" / "AI_SYSTEM_WORK.md").exists()
    assert (workspace / "codex-skills" / "CodexRules" / "SKILL.md").exists()
    assert (workspace / "telegram").is_dir()
    assert (workspace / "memories").is_dir()
    assert (workspace / "runtime-state").is_dir()
    assert (workspace / "logs").is_dir()
    assert (workspace / "codex" / "work").is_dir()
    assert (workspace / "codex" / "github-auth").is_dir()
    assert (workspace / "codex" / "codex-home").is_dir()
    assert not (workspace / "system").exists()


def test_settings_load_workspace_toml_and_env_overrides(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AMBER_HOME", str(tmp_path / ".amber"))
    monkeypatch.setenv("AMBER_BLUE_AI_MODEL", "gpt-test")
    get_settings.cache_clear()

    workspace = init_workspace("indiedreamers")
    settings = get_settings("indiedreamers")

    assert settings.workspace_dir == workspace
    assert settings.ai_model == "gpt-test"
    assert settings.codex_container_name == "amber-indiedreamers-codex"
    assert settings.codex_podman_cgroup_manager is None
    assert settings.telegram_session_path == workspace / "telegram" / "telegram.session"
    assert settings.memories_dir == workspace / "memories"
    assert settings.codex_workdir == workspace / "codex" / "work"

    get_settings.cache_clear()


def test_semantic_prompt_composes_release_system_before_workspace_prompt(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AMBER_HOME", str(tmp_path / ".amber"))
    get_settings.cache_clear()

    init_workspace("indiedreamers")
    prompt = SemanticConfig.from_settings(get_settings("indiedreamers")).system_prompt

    orchestration_index = prompt.index("Amber receives a visible context frame")
    workspace_index = prompt.index("You are the semantic decision layer for Amber in a work-focused Telegram context.")
    assert orchestration_index < workspace_index

    get_settings.cache_clear()


def test_user_service_unit_uses_workspace_and_amber_home(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AMBER_HOME", str(tmp_path / ".amber"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    get_settings.cache_clear()

    workspace = init_workspace("indiedreamers")
    unit = render_user_service("indiedreamers")

    assert f"Environment=AMBER_HOME={tmp_path / '.amber'}" in unit
    assert f"ExecStart={tmp_path / '.amber' / 'bin' / 'amber'} run --workspace {workspace}" in unit
    path = install_user_service("indiedreamers")
    assert path == tmp_path / ".config" / "systemd" / "user" / "amber-indiedreamers.service"
    assert path.read_text(encoding="utf-8") == unit

    get_settings.cache_clear()

