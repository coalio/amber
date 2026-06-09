from __future__ import annotations

from pathlib import Path

from src import cli


def test_main_prints_runtime_errors_without_traceback(monkeypatch, capsys) -> None:
    def fail(_argv):
        raise RuntimeError("setup failed")

    monkeypatch.setattr(cli, "_main", fail)

    assert cli.main(["workspace", "configure", "dev"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: setup failed\n"
    assert "Traceback" not in captured.err


def test_codex_cgroup_setup_error_mentions_saved_workspace() -> None:
    message = cli._format_codex_setup_error(
        RuntimeError('Podman command failed (125): podman run\nError: could not find cgroup mount in "/proc/self/cgroup"'),
        Path("dev-workspace"),
    )

    assert "Workspace credentials were saved" in message
    assert "Podman cannot access cgroups" in message
    assert "amber workspace configure dev-workspace" in message
    assert 'could not find cgroup mount in "/proc/self/cgroup"' in message
