from __future__ import annotations

import subprocess

from src.ai.semantic.config import SemanticConfig
from src.config.config import get_settings
from src.config.workspace import (
    doctor_workspace,
    init_workspace,
    install_user_service,
    load_workspace_config,
    render_user_service,
    write_workspace_config,
)


def test_workspace_init_creates_fixed_layout(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AMBER_HOME", str(tmp_path / ".amber"))
    get_settings.cache_clear()

    workspace = init_workspace("indiedreamers")

    assert workspace == tmp_path / ".amber" / "workspaces" / "indiedreamers"
    assert (workspace / "config.toml").exists()
    assert (workspace / "prompts" / "AI_SYSTEM_WORK.md").exists()
    assert (workspace / "codex-skills" / "codex-development" / "SKILL.md").exists()
    assert (workspace / "codex-skills" / "codex-pr-reviews" / "SKILL.md").exists()
    assert (workspace / "codex-skills" / "python-style-rules" / "SKILL.md").exists()
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
    monkeypatch.setenv("AMBER_ATTENTION_SCORER", "modernbert")
    monkeypatch.setenv("AMBER_AI_MODEL", "gpt-test")
    get_settings.cache_clear()

    workspace = init_workspace("indiedreamers")
    settings = get_settings("indiedreamers")

    assert settings.workspace_dir == workspace
    assert settings.release_version == "0.3.1"
    assert settings.attention_scorer == "modernbert"
    assert settings.ai_model == "gpt-test"
    assert settings.codex_container_name == "amber-indiedreamers-codex"
    assert settings.codex_podman_cgroup_manager is None
    assert settings.telegram_session_path == workspace / "telegram" / "telegram.session"
    assert settings.memories_dir == workspace / "memories"
    assert settings.codex_workdir == workspace / "codex" / "work"

    get_settings.cache_clear()


def test_saving_workspace_config_preserves_nested_linear_status_defaults(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AMBER_HOME", str(tmp_path / ".amber"))
    get_settings.cache_clear()

    workspace = init_workspace("indiedreamers")
    config_path = workspace / "config.toml"
    data = load_workspace_config(config_path)
    data["linear"]["api_key"] = "test-linear-key"
    write_workspace_config(config_path, data)

    settings = get_settings("indiedreamers")

    assert settings.linear_project_statuses == {
        "planned": ("Planned",),
        "started": ("Started",),
        "completed": ("Completed",),
        "canceled": ("Canceled",),
    }
    assert '[linear.project.statuses]' in config_path.read_text(encoding="utf-8")

    get_settings.cache_clear()


def test_semantic_prompt_composes_release_system_before_workspace_prompt(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AMBER_HOME", str(tmp_path / ".amber"))
    get_settings.cache_clear()

    init_workspace("indiedreamers")
    prompt = SemanticConfig.from_settings(get_settings("indiedreamers")).system_prompt

    orchestration_index = prompt.index("Amber receives a visible context frame")
    workspace_index = prompt.index("You are the semantic decision layer for Amber in a work-focused Telegram context.")
    notification_policy_index = prompt.index("# Codex Notification Policy")
    assert orchestration_index < workspace_index
    assert workspace_index < notification_policy_index

    get_settings.cache_clear()


def test_release_notification_policy_overrides_stale_workspace_prompt(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AMBER_HOME", str(tmp_path / ".amber"))
    get_settings.cache_clear()

    workspace = init_workspace("indiedreamers")
    stale_instruction = "When codex_notification is present, Amber must always send a reply."
    (workspace / "prompts" / "AI_SYSTEM_WORK.md").write_text(stale_instruction, encoding="utf-8")
    prompt = SemanticConfig.from_settings(get_settings("indiedreamers")).system_prompt

    assert prompt.index(stale_instruction) < prompt.index("# Codex Notification Policy")
    assert "If Amber recently communicated the same concept in that chat, return `ignore`" in prompt

    get_settings.cache_clear()


def test_user_service_unit_uses_workspace_and_amber_home(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AMBER_HOME", str(tmp_path / ".amber"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    get_settings.cache_clear()

    workspace = init_workspace("indiedreamers")
    unit = render_user_service("indiedreamers")

    assert f"Environment=AMBER_HOME={tmp_path / '.amber'}" in unit
    assert f"ExecStart={tmp_path / '.amber' / 'bin' / 'amber'} run --workspace {workspace}" in unit
    assert "KillMode=process" in unit
    assert "rm --force --ignore amber-indiedreamers-codex amber-indiedreamers-codex-bootstrap" in unit
    assert "TimeoutStopSec=25s" in unit
    path = install_user_service("indiedreamers")
    assert path == tmp_path / ".config" / "systemd" / "user" / "amber-indiedreamers.service"
    assert path.read_text(encoding="utf-8") == unit

    get_settings.cache_clear()


def test_workspace_doctor_reports_codex_podman_prerequisites(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AMBER_HOME", str(tmp_path / ".amber"))
    get_settings.cache_clear()
    init_workspace("indiedreamers")

    def fake_which(command: str) -> str | None:
        if command in {"podman", "slirp4netns"}:
            return f"/usr/bin/{command}"
        return None

    def fake_run(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        if command[:3] == ["podman", "info", "--debug"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="host:\n  security:\n    rootless: true\n  cgroupVersion: v2\n",
                stderr="",
            )
        if command[:3] == ["podman", "run", "--help"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="--userns --network --cgroups --memory --cpus --pids-limit\n",
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("src.config.workspace.shutil.which", fake_which)
    monkeypatch.setattr("src.config.workspace.subprocess.run", fake_run)
    monkeypatch.setattr("src.config.workspace._local_cgroup_v2_detail", lambda: (True, "/sys/fs/cgroup is cgroup2fs"))

    checks = {check.name: check for check in doctor_workspace("indiedreamers", validate_external=True)}

    assert checks["podman-info"].ok is True
    assert checks["podman-rootless"].ok is True
    assert checks["podman-cgroup-v2"].ok is True
    assert checks["podman-network-helper"].ok is True
    assert checks["podman-run-flags"].ok is True
    assert checks["codex-resource-limits"].ok is True

    get_settings.cache_clear()
