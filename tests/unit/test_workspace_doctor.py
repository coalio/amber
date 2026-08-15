from __future__ import annotations

import subprocess

from src import cli
from src.config.config import get_settings
from src.config.doctor import (
    CODEX_CONTAINER_REPAIR,
    DoctorCheck,
    DoctorContext,
    DoctorStage,
    doctor_workspace,
    recreate_codex_container,
    run_doctor_pipeline,
)
from src.config.workspace import init_workspace


def test_doctor_pipeline_accepts_an_appended_stage() -> None:
    observed: list[str] = []

    def diagnose(context: DoctorContext) -> list[DoctorCheck]:
        observed.append(str(context.workspace))
        return [DoctorCheck("new-symptom", True, "diagnosed")]

    checks = run_doctor_pipeline(
        DoctorContext(workspace="dev"),
        selected_scopes={"workspace"},
        pipeline=(DoctorStage("new-diagnostic", "workspace", diagnose),),
    )

    assert observed == ["dev"]
    assert checks == [DoctorCheck("new-symptom", True, "diagnosed")]


def test_container_stage_treats_an_absent_container_as_healthy(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("AMBER_HOME", str(tmp_path / ".amber"))
    get_settings.cache_clear()
    init_workspace("dev")

    monkeypatch.setattr(
        "src.config.doctor.shutil.which", lambda command: f"/usr/bin/{command}"
    )

    def fake_run(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        assert command[:3] == ["podman", "container", "exists"]
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="")

    monkeypatch.setattr("src.config.doctor.subprocess.run", fake_run)

    checks = doctor_workspace("dev", stages=("container",))

    assert [check.name for check in checks] == [
        "config",
        "podman",
        "codex-container-state",
    ]
    assert all(check.ok for check in checks)
    get_settings.cache_clear()


def test_container_stage_detects_stale_mount_and_workdir(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AMBER_HOME", str(tmp_path / ".amber"))
    get_settings.cache_clear()
    init_workspace("dev")
    settings = get_settings("dev")

    monkeypatch.setattr(
        "src.config.doctor.shutil.which", lambda command: f"/usr/bin/{command}"
    )
    monkeypatch.setattr("src.config.doctor._app_server_health_ok", lambda _url: True)

    def completed(
        args: list[str], returncode: int = 0, stdout: str = "", stderr: str = ""
    ):
        return subprocess.CompletedProcess(
            args, returncode, stdout=stdout, stderr=stderr
        )

    def fake_run_podman(
        _context: DoctorContext, args: list[str]
    ) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["container", "exists"]:
            return completed(args)
        if args[:2] == ["inspect", "-f"]:
            return completed(args, stdout="true\n")
        if args[:3] == ["exec", "--workdir", "/"] and args[-1] == "true":
            return completed(args)
        if "stat" in args:
            container_path = args[-1]
            host_paths = {
                "/work": settings.codex_workdir,
                "/github-auth": settings.codex_github_auth_dir,
                "/codex-home": settings.codex_home_dir,
            }
            host_stat = host_paths[container_path].stat()
            identity = (
                "0:0"
                if container_path == "/work"
                else f"{host_stat.st_dev}:{host_stat.st_ino}"
            )
            return completed(args, stdout=f"{identity}\n")
        if args == ["exec", settings.codex_container_name, "pwd"]:
            return completed(
                args, 127, stderr="crun: getcwd: No such file or directory"
            )
        raise AssertionError(f"unexpected Podman arguments: {args}")

    monkeypatch.setattr("src.config.doctor._run_podman", fake_run_podman)

    checks = {
        check.name: check for check in doctor_workspace("dev", stages=("container",))
    }

    assert checks["codex-container-runtime"].ok is True
    assert checks["codex-container-workdir-mount"].ok is False
    assert checks["codex-container-workdir-mount"].repair == CODEX_CONTAINER_REPAIR
    assert "stale workspace directory" in checks["codex-container-workdir-mount"].detail
    assert checks["codex-container-workdir"].ok is False
    assert checks["codex-container-workdir"].repair == CODEX_CONTAINER_REPAIR
    get_settings.cache_clear()


def test_doctor_repair_recreates_and_reruns_selected_stages(
    monkeypatch, capsys
) -> None:
    calls: list[tuple[str, object]] = []
    results = iter(
        [
            [
                DoctorCheck(
                    "codex-container-workdir",
                    False,
                    "stale",
                    repair=CODEX_CONTAINER_REPAIR,
                )
            ],
            [DoctorCheck("codex-container-workdir", True, "healthy")],
        ]
    )

    def fake_doctor(workspace: str, **kwargs):
        calls.append(("doctor", (workspace, kwargs)))
        return next(results)

    def fake_recreate(workspace: str, *, progress_callback=None) -> None:
        calls.append(("recreate", workspace))
        if progress_callback:
            progress_callback("container ready")

    monkeypatch.setattr("src.config.doctor.doctor_workspace", fake_doctor)
    monkeypatch.setattr("src.config.doctor.recreate_codex_container", fake_recreate)

    status = cli.main(
        ["workspace", "doctor", "dev", "--stage", "container", "--repair"]
    )

    assert status == 0
    assert [call[0] for call in calls] == ["doctor", "recreate", "doctor"]
    assert calls[0][1][1]["stages"] == ["container"]
    output = capsys.readouterr().out
    assert "[repair] recreating the Codex sandbox container" in output
    assert "[ok] codex-container-workdir: healthy" in output


def test_container_repair_preserves_workspace_data_and_does_not_touch_service(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("AMBER_HOME", str(tmp_path / ".amber"))
    get_settings.cache_clear()
    workspace = init_workspace("dev")
    work_file = workspace / "codex" / "work" / "keep.txt"
    work_file.write_text("keep me", encoding="utf-8")
    calls: list[object] = []

    def fake_run(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    class FakeAdapter:
        def ensure_app_server(self) -> None:
            calls.append("ensure-app-server")

    def fake_build(settings, *, progress_callback=None):
        calls.append((settings.workspace_dir, progress_callback))
        return FakeAdapter()

    monkeypatch.setattr("src.config.doctor.subprocess.run", fake_run)
    monkeypatch.setattr("src.adapters.codex.build_codex_adapter", fake_build)

    recreate_codex_container("dev")

    cleanup = calls[0]
    assert isinstance(cleanup, list)
    assert cleanup[-5:] == [
        "rm",
        "--force",
        "--ignore",
        "amber-dev-codex",
        "amber-dev-codex-bootstrap",
    ]
    assert all("systemctl" not in str(call) for call in calls)
    assert calls[-1] == "ensure-app-server"
    assert work_file.read_text(encoding="utf-8") == "keep me"
    get_settings.cache_clear()
