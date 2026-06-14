from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.adapters.codex
from src import cli
from src.config.config import get_settings
from src.config.workspace import init_workspace


def test_main_prints_runtime_errors_without_traceback(monkeypatch, capsys) -> None:
    def fail(_argv):
        raise RuntimeError("setup failed")

    monkeypatch.setattr(cli, "_main", fail)

    assert cli.main(["workspace", "configure", "dev"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: setup failed\n"
    assert "Traceback" not in captured.err


def test_run_builds_telegram_runtime_inside_event_loop(monkeypatch) -> None:
    events: list[str] = []

    class FakeApp:
        async def run_telegram_forever(self) -> None:
            asyncio.get_running_loop()
            events.append("ran")

    def fake_build_application(*, settings, enable_telegram: bool) -> FakeApp:
        asyncio.get_running_loop()
        assert settings == "settings"
        assert enable_telegram is True
        events.append("built")
        return FakeApp()

    monkeypatch.setattr("src.config.config.get_settings", lambda workspace: "settings")
    monkeypatch.setattr("src.runtime.build_application", fake_build_application)

    result = cli._run(SimpleNamespace(workspace="indiedreamers"))

    assert result == 0
    assert events == ["built", "ran"]


def test_codex_cgroup_setup_error_mentions_saved_workspace() -> None:
    message = cli._format_codex_setup_error(
        RuntimeError('Podman command failed (125): podman run\nError: could not find cgroup mount in "/proc/self/cgroup"'),
        Path("dev-workspace"),
    )

    assert "Workspace credentials were saved" in message
    assert "Podman cannot access cgroups" in message
    assert "amber workspace configure dev-workspace" in message
    assert 'could not find cgroup mount in "/proc/self/cgroup"' in message


def test_workspace_configure_checks_codex_before_secret_prompts(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AMBER_HOME", str(tmp_path / ".amber"))
    get_settings.cache_clear()
    init_workspace("indiedreamers")

    def fail_preflight(*_args):
        raise RuntimeError("sandbox failed")

    def fail_secret_prompt(*_args):
        raise AssertionError("secret prompt should not run before codex preflight")

    monkeypatch.setattr(cli, "_prepare_codex_sandbox_before_secrets", fail_preflight)
    monkeypatch.setattr(cli, "_prompt_required_secret", fail_secret_prompt)

    with pytest.raises(RuntimeError, match="sandbox failed"):
        cli._configure_workspace("indiedreamers")

    get_settings.cache_clear()


def test_codex_preflight_retries_with_resource_limits_disabled(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    data = {"codex": {"enforce_resource_limits": True}}
    attempts: list[tuple[bool, str | None]] = []
    enabled = SimpleNamespace(codex_enforce_resource_limits=True, codex_podman_cgroup_manager=None)
    disabled = SimpleNamespace(codex_enforce_resource_limits=False, codex_podman_cgroup_manager="cgroupfs")

    class FakeAdapter:
        def __init__(self, enforce_resource_limits: bool, cgroup_manager: str | None) -> None:
            self._enforce_resource_limits = enforce_resource_limits
            self._cgroup_manager = cgroup_manager

        def ensure_app_server(self) -> None:
            if self._enforce_resource_limits:
                raise RuntimeError(
                    'Podman command failed (125): podman run\nError: could not find cgroup mount in "/proc/self/cgroup"'
                )

    def fake_build(settings, **_kwargs):
        attempts.append((settings.codex_enforce_resource_limits, settings.codex_podman_cgroup_manager))
        return FakeAdapter(settings.codex_enforce_resource_limits, settings.codex_podman_cgroup_manager)

    monkeypatch.setattr(src.adapters.codex, "build_codex_adapter", fake_build)
    monkeypatch.setattr(cli, "_reload_workspace_settings", lambda _workspace: disabled)

    result = cli._prepare_codex_sandbox_before_secrets(enabled, data, config_path, tmp_path)

    assert isinstance(result, FakeAdapter)
    assert result._enforce_resource_limits is False
    assert result._cgroup_manager == "cgroupfs"
    assert attempts == [(True, None), (False, "cgroupfs")]
    assert data["codex"]["enforce_resource_limits"] is False
    assert data["codex"]["podman_cgroup_manager"] == "cgroupfs"
    assert "enforce_resource_limits = false" in config_path.read_text(encoding="utf-8")
    assert 'podman_cgroup_manager = "cgroupfs"' in config_path.read_text(encoding="utf-8")
