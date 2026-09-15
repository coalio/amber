from __future__ import annotations

import os
from typing import Any

import pytest

from src.adapters.codex.app_server import CodexTaskRunner
from src.hooks import CodexHookInstaller, HookConfig
from src.hooks.config import DEFAULT_REPOSITORY, DEFAULT_REVISION


def test_default_hook_installer_pins_and_installs_configured_repository() -> None:
    calls: list[list[str]] = []
    progress: list[str] = []
    installer = CodexHookInstaller(
        HookConfig(),
        container_name="codex-sandbox",
        sandbox_runner=calls.append,
        progress_callback=progress.append,
    )

    installer.install()
    installer.install()

    assert len(calls) == 1
    assert calls[0][:10] == [
        "exec",
        "--user",
        str(os.getuid()),
        "-e",
        "HOME=/codex-home",
        "-e",
        "CODEX_HOME=/codex-home/.codex",
        "-e",
        "GIT_TERMINAL_PROMPT=0",
        "codex-sandbox",
    ]
    script = calls[0][-1]
    assert f"git clone --quiet --no-checkout -- {DEFAULT_REPOSITORY}" in script
    assert f"checkout --quiet --detach {DEFAULT_REVISION}" in script
    assert 'CODEX_HOME=/codex-home/.codex sh "$source_dir/install.sh" $force_arg' in script
    assert "force_arg=--force" in script
    assert progress == ["installing configured codex hooks"]


def test_hook_installer_can_be_disabled() -> None:
    calls: list[list[str]] = []
    installer = CodexHookInstaller(
        HookConfig(repository=None),
        container_name="codex-sandbox",
        sandbox_runner=calls.append,
    )

    installer.install()

    assert installer.enabled is False
    assert calls == []


@pytest.mark.parametrize("revision", ["", "main branch", "--orphan"])
def test_hook_config_rejects_unsafe_revisions(revision: str) -> None:
    with pytest.raises(RuntimeError, match="hooks.revision"):
        HookConfig(revision=revision)


def test_task_runner_trusts_only_installed_global_hook_hashes() -> None:
    client = _FakeHookClient()
    runner = CodexTaskRunner("task-1", {"installed_hooks_enabled": True})
    runner.client = client  # type: ignore[assignment]

    runner._configure_installed_hooks()

    assert client.calls == [
        ("hooks/list", {"cwds": ["/work"]}),
        (
            "config/batchWrite",
            {
                "edits": [
                    {
                        "keyPath": "hooks.state",
                        "value": {
                            "/codex-home/.codex/hooks.json:session_start:0:0": {
                                "enabled": True,
                                "trusted_hash": "sha256:new",
                            }
                        },
                        "mergeStrategy": "upsert",
                    }
                ],
                "reloadUserConfig": True,
            },
        ),
    ]


def test_task_runner_requires_discoverable_hooks_when_enabled() -> None:
    client = _FakeHookClient(entries=[])
    runner = CodexTaskRunner("task-1", {"installed_hooks_enabled": True})
    runner.client = client  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="no global hooks were discovered"):
        runner._configure_installed_hooks()


def test_task_runner_disables_previously_installed_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeHookClient()
    runner = CodexTaskRunner("task-1", {"installed_hooks_enabled": False})
    runner.client = client  # type: ignore[assignment]
    monkeypatch.setattr("src.adapters.codex.app_server.os.path.exists", lambda _path: True)

    runner._configure_installed_hooks()

    state = client.calls[1][1]["edits"][0]["value"]
    assert state == {
        "/codex-home/.codex/hooks.json:session_start:0:0": {"enabled": False},
        "/codex-home/.codex/hooks.json:stop:0:0": {"enabled": False},
    }


class _FakeHookClient:
    def __init__(self, *, entries: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._entries = entries if entries is not None else [_hook_list_entry()]

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((method, params))
        if method == "hooks/list":
            return {"data": self._entries}
        return {}


def _hook_list_entry() -> dict[str, Any]:
    return {
        "cwd": "/work",
        "hooks": [
            {
                "key": "/codex-home/.codex/hooks.json:session_start:0:0",
                "source": "user",
                "sourcePath": "/codex-home/.codex/hooks.json",
                "currentHash": "sha256:new",
                "trustStatus": "untrusted",
                "enabled": True,
            },
            {
                "key": "/codex-home/.codex/hooks.json:stop:0:0",
                "source": "user",
                "sourcePath": "/codex-home/.codex/hooks.json",
                "currentHash": "sha256:existing",
                "trustStatus": "trusted",
                "enabled": True,
            },
            {
                "key": "/work/.codex/config.toml:stop:0:0",
                "source": "project",
                "sourcePath": "/work/.codex/config.toml",
                "currentHash": "sha256:project",
                "trustStatus": "untrusted",
                "enabled": True,
            },
        ],
    }
