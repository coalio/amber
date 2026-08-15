from __future__ import annotations

import os

from src.utils.process import host_process_environment, open_host_process, run_host_command


def test_host_process_environment_removes_frozen_library_path(monkeypatch) -> None:
    monkeypatch.setattr("src.utils.process.sys.frozen", True, raising=False)
    env = host_process_environment(
        {"LD_LIBRARY_PATH": "/release/_internal", "AMBER_TEST_VALUE": "preserved"}
    )

    assert "LD_LIBRARY_PATH" not in env
    assert env["AMBER_TEST_VALUE"] == "preserved"


def test_host_process_environment_restores_original_library_path(monkeypatch) -> None:
    monkeypatch.setattr("src.utils.process.sys.frozen", True, raising=False)
    env = host_process_environment(
        {
            "LD_LIBRARY_PATH": "/release/_internal:/host/lib",
            "LD_LIBRARY_PATH_ORIG": "/host/lib",
        }
    )

    assert env["LD_LIBRARY_PATH"] == "/host/lib"


def test_host_process_environment_preserves_empty_original_path(monkeypatch) -> None:
    monkeypatch.setattr("src.utils.process.sys.frozen", True, raising=False)
    env = host_process_environment(
        {"LD_LIBRARY_PATH": "/release/_internal", "LD_LIBRARY_PATH_ORIG": ""}
    )

    assert env["LD_LIBRARY_PATH"] == ""


def test_host_process_helpers_sanitize_runner_and_opener_environment(
    monkeypatch,
) -> None:
    observed: list[dict[str, str]] = []

    def record(command, *, env, **_kwargs):
        observed.append(env)
        return command

    monkeypatch.setenv("LD_LIBRARY_PATH", "/release/_internal")
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    monkeypatch.setattr("src.utils.process.sys.frozen", True, raising=False)

    assert run_host_command(["systemctl"], runner=record) == ["systemctl"]
    assert open_host_process(["python"], opener=record) == ["python"]
    assert all("LD_LIBRARY_PATH" not in env for env in observed)
    assert os.environ["LD_LIBRARY_PATH"] == "/release/_internal"


def test_source_process_preserves_library_path(monkeypatch) -> None:
    monkeypatch.delattr("src.utils.process.sys.frozen", raising=False)

    env = host_process_environment({"LD_LIBRARY_PATH": "/host/custom/lib"})

    assert env["LD_LIBRARY_PATH"] == "/host/custom/lib"
