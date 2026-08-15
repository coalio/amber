from __future__ import annotations

import json
import shutil
import socket
import subprocess
from collections.abc import Callable, Iterable, Sequence, Set
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib import request

from src.config.config import Settings, get_settings, workspace_dir


DoctorRepair = Literal["recreate-codex-container"]
DoctorStageScope = Literal[
    "always", "workspace", "podman", "container", "integrations", "service"
]
DoctorStageRunner = Callable[["DoctorContext"], list["DoctorCheck"]]

CODEX_CONTAINER_REPAIR: DoctorRepair = "recreate-codex-container"
DOCTOR_STAGE_SCOPES = ("workspace", "podman", "container", "integrations", "service")
DEFAULT_DOCTOR_STAGE_SCOPES = ("workspace", "podman", "container", "integrations")


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str
    repair: DoctorRepair | None = None


@dataclass(frozen=True)
class DoctorStage:
    name: str
    scope: DoctorStageScope
    run: DoctorStageRunner


@dataclass
class DoctorContext:
    workspace: str | Path
    validate_external: bool = False
    settings: Settings | None = None
    podman_path: str | None = None
    container_exists: bool = False
    container_running: bool = False
    container_runtime_ok: bool = False


def doctor_workspace(
    workspace: str | Path,
    *,
    validate_external: bool = False,
    include_service: bool = False,
    stages: Iterable[str] | None = None,
) -> list[DoctorCheck]:
    selected_scopes = set(stages or DEFAULT_DOCTOR_STAGE_SCOPES)
    if include_service:
        selected_scopes.add("service")
    unknown_scopes = selected_scopes.difference(DOCTOR_STAGE_SCOPES)
    if unknown_scopes:
        choices = ", ".join(DOCTOR_STAGE_SCOPES)
        unknown = ", ".join(sorted(unknown_scopes))
        raise RuntimeError(f"Unknown doctor stage: {unknown}. Choose from: {choices}.")
    if "container" in selected_scopes:
        # container diagnostics depend on the podman discovery stage
        selected_scopes.add("podman")

    context = DoctorContext(workspace=workspace, validate_external=validate_external)
    return run_doctor_pipeline(context, selected_scopes=selected_scopes)


def run_doctor_pipeline(
    context: DoctorContext,
    *,
    selected_scopes: Set[str],
    pipeline: Sequence[DoctorStage] | None = None,
) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []

    # keep stage ordering as the only orchestration contract for new diagnostics
    for stage in pipeline or DOCTOR_PIPELINE:
        if stage.scope != "always" and stage.scope not in selected_scopes:
            continue
        stage_checks = stage.run(context)
        checks.extend(stage_checks)
        if stage.scope == "always" and any(not check.ok for check in stage_checks):
            break
    return checks


def recreate_codex_container(
    workspace: str | Path,
    *,
    progress_callback: Callable[[str], None] | None = None,
) -> None:
    settings = get_settings(workspace)
    cleanup = codex_container_cleanup_command(settings.workspace_dir)

    # remove both runtime and bootstrap containers while preserving bind-mounted data
    result = subprocess.run(
        cleanup,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        detail = _command_failure_detail(result)
        raise RuntimeError(f"Could not remove the unhealthy Codex sandbox: {detail}")

    # recreate through the production adapter so image, mounts, and health checks stay aligned
    from src.adapters.codex import build_codex_adapter

    adapter = build_codex_adapter(settings, progress_callback=progress_callback)
    adapter.ensure_app_server()


def codex_container_cleanup_command(workspace: str | Path) -> list[str]:
    settings = get_settings(workspace)
    executable = (
        shutil.which(settings.codex_podman_executable)
        or settings.codex_podman_executable
    )
    command = [executable]
    if settings.codex_podman_cgroup_manager:
        command.append(f"--cgroup-manager={settings.codex_podman_cgroup_manager}")
    command.extend(
        [
            "rm",
            "--force",
            "--ignore",
            settings.codex_container_name,
            f"{settings.codex_container_name}-bootstrap",
        ]
    )
    return command


def stop_workspace_codex_containers(workspace: str | Path) -> None:
    # keep service stop best-effort when workspace config or podman is unavailable
    try:
        command = codex_container_cleanup_command(workspace)
    except Exception:
        return
    try:
        subprocess.run(
            command, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except OSError:
        return


def service_status_check(workspace: str | Path) -> DoctorCheck:
    name = f"amber-{workspace_dir(workspace).name}.service"
    result = subprocess.run(
        ["systemctl", "--user", "is-enabled", name],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode == 0:
        return DoctorCheck("systemd-user-service", True, f"{name} is enabled")
    detail = (result.stderr or result.stdout or "").strip() or f"{name} is not enabled"
    return DoctorCheck("systemd-user-service", False, detail)


def _stage_config(context: DoctorContext) -> list[DoctorCheck]:
    try:
        context.settings = get_settings(context.workspace)
    except Exception as exc:
        return [DoctorCheck("config", False, str(exc))]
    return [
        DoctorCheck(
            "config", True, f"loaded {context.settings.workspace_dir / 'config.toml'}"
        )
    ]


def _stage_workspace_resources(context: DoctorContext) -> list[DoctorCheck]:
    settings = _settings(context)
    required_files = [
        settings.ai_orchestration_prompt_path,
        settings.ai_notification_policy_prompt_path,
        settings.codex_system_prompt_path,
        settings.ai_system_casual_prompt_path,
        settings.ai_system_work_prompt_path,
        settings.ai_action_contract_prompt_path,
        settings.ai_interruption_prompt_path,
        settings.memory_prompt_path,
        *settings.codex_skill_paths,
    ]
    missing = [str(path) for path in required_files if not path.exists()]
    detail = "all prompts and skills exist" if not missing else "\n".join(missing)
    return [DoctorCheck("resources", not missing, detail)]


def _stage_workspace_runtime(context: DoctorContext) -> list[DoctorCheck]:
    settings = _settings(context)
    return [_port_check(settings.codex_app_server_port, settings.codex_app_server_url)]


def _stage_integration_config(context: DoctorContext) -> list[DoctorCheck]:
    settings = _settings(context)
    checks = [
        _required_value_check("ai.api_key", settings.ai_api_key),
        _required_value_check("telegram.api_id", settings.telegram_api_id),
        _required_value_check("telegram.api_hash", settings.telegram_api_hash),
        _required_value_check("linear.api_key", settings.linear_api_key),
        DoctorCheck(
            "telegram-session",
            settings.telegram_session_path.exists(),
            f"found {settings.telegram_session_path}"
            if settings.telegram_session_path.exists()
            else f"missing {settings.telegram_session_path}",
        ),
        _codex_auth_check(settings.codex_home_dir),
        _github_auth_check(settings.codex_github_auth_dir),
    ]
    return checks


def _stage_podman_command(context: DoctorContext) -> list[DoctorCheck]:
    settings = _settings(context)
    context.podman_path = shutil.which(settings.codex_podman_executable)
    detail = (
        f"found {context.podman_path}"
        if context.podman_path
        else f"`{settings.codex_podman_executable}` was not found on PATH"
    )
    return [DoctorCheck("podman", context.podman_path is not None, detail)]


def _stage_podman_external(context: DoctorContext) -> list[DoctorCheck]:
    if not context.validate_external or not context.podman_path:
        return []
    settings = _settings(context)
    podman_info = _podman_info(settings.codex_podman_executable)
    cgroup_check = _podman_cgroup_v2_check(podman_info)
    run_flags_check = _podman_run_flags_check(settings.codex_podman_executable)
    return [
        _podman_info_check(podman_info),
        _podman_rootless_check(podman_info),
        cgroup_check,
        _podman_network_helper_check(),
        run_flags_check,
        _codex_resource_limits_check(
            settings.codex_enforce_resource_limits, cgroup_check, run_flags_check
        ),
    ]


def _stage_container_state(context: DoctorContext) -> list[DoctorCheck]:
    if not context.podman_path:
        return []
    settings = _settings(context)
    exists = _run_podman(
        context, ["container", "exists", settings.codex_container_name]
    )
    if exists.returncode == 1:
        return [
            DoctorCheck(
                "codex-container-state",
                True,
                "not created; Amber will create it on demand",
            )
        ]
    if exists.returncode != 0:
        return [
            DoctorCheck("codex-container-state", False, _command_failure_detail(exists))
        ]

    context.container_exists = True
    running = _run_podman(
        context, ["inspect", "-f", "{{.State.Running}}", settings.codex_container_name]
    )
    context.container_running = (
        running.returncode == 0 and (running.stdout or "").strip() == "true"
    )
    if context.container_running:
        return [
            DoctorCheck(
                "codex-container-state",
                True,
                f"{settings.codex_container_name} is running",
            )
        ]
    detail = (
        _command_failure_detail(running)
        if running.returncode != 0
        else f"{settings.codex_container_name} is not running"
    )
    return [
        DoctorCheck(
            "codex-container-state", False, detail, repair=CODEX_CONTAINER_REPAIR
        )
    ]


def _stage_container_runtime(context: DoctorContext) -> list[DoctorCheck]:
    if not context.container_running:
        return []
    settings = _settings(context)
    result = _run_podman(
        context, ["exec", "--workdir", "/", settings.codex_container_name, "true"]
    )
    context.container_runtime_ok = result.returncode == 0
    detail = (
        "OCI runtime can execute commands from /"
        if context.container_runtime_ok
        else _command_failure_detail(result)
    )
    return [
        DoctorCheck(
            "codex-container-runtime",
            context.container_runtime_ok,
            detail,
            repair=None if context.container_runtime_ok else CODEX_CONTAINER_REPAIR,
        )
    ]


def _stage_container_mounts(context: DoctorContext) -> list[DoctorCheck]:
    if not context.container_runtime_ok:
        return []
    settings = _settings(context)
    mounts = (
        ("codex-container-workdir-mount", settings.codex_workdir, "/work"),
        (
            "codex-container-github-auth-mount",
            settings.codex_github_auth_dir,
            "/github-auth",
        ),
        ("codex-container-home-mount", settings.codex_home_dir, "/codex-home"),
    )
    return [
        _container_mount_check(context, name, host_path, container_path)
        for name, host_path, container_path in mounts
    ]


def _stage_container_workdir(context: DoctorContext) -> list[DoctorCheck]:
    if not context.container_runtime_ok:
        return []
    settings = _settings(context)
    result = _run_podman(context, ["exec", settings.codex_container_name, "pwd"])
    ok = result.returncode == 0 and (result.stdout or "").strip() == "/work"
    detail = (
        "default working directory is /work" if ok else _command_failure_detail(result)
    )
    return [
        DoctorCheck(
            "codex-container-workdir",
            ok,
            detail,
            repair=None if ok else CODEX_CONTAINER_REPAIR,
        )
    ]


def _stage_container_app_server(context: DoctorContext) -> list[DoctorCheck]:
    if not context.container_runtime_ok:
        return []
    settings = _settings(context)
    ok = _app_server_health_ok(settings.codex_app_server_url)
    detail = (
        "Codex app-server health endpoint is ready"
        if ok
        else "Codex app-server health endpoint is unavailable"
    )
    return [
        DoctorCheck(
            "codex-app-server",
            ok,
            detail,
            repair=None if ok else CODEX_CONTAINER_REPAIR,
        )
    ]


def _stage_external_integrations(context: DoctorContext) -> list[DoctorCheck]:
    settings = _settings(context)
    if not context.validate_external or not settings.linear_api_key:
        return []
    return [_linear_check(settings.linear_api_key, settings.linear_api_url)]


def _stage_service(context: DoctorContext) -> list[DoctorCheck]:
    return [service_status_check(_settings(context).workspace_dir)]


DOCTOR_PIPELINE = (
    DoctorStage("config", "always", _stage_config),
    DoctorStage("workspace-resources", "workspace", _stage_workspace_resources),
    DoctorStage("workspace-runtime", "workspace", _stage_workspace_runtime),
    DoctorStage("integration-config", "integrations", _stage_integration_config),
    DoctorStage("podman-command", "podman", _stage_podman_command),
    DoctorStage("podman-external", "podman", _stage_podman_external),
    DoctorStage("container-state", "container", _stage_container_state),
    DoctorStage("container-runtime", "container", _stage_container_runtime),
    DoctorStage("container-mounts", "container", _stage_container_mounts),
    DoctorStage("container-workdir", "container", _stage_container_workdir),
    DoctorStage("container-app-server", "container", _stage_container_app_server),
    DoctorStage("external-integrations", "integrations", _stage_external_integrations),
    DoctorStage("service", "service", _stage_service),
)


def _settings(context: DoctorContext) -> Settings:
    if context.settings is None:
        raise RuntimeError("Doctor config stage did not load workspace settings.")
    return context.settings


def _run_podman(
    context: DoctorContext, args: list[str]
) -> subprocess.CompletedProcess[str]:
    settings = _settings(context)
    command = [settings.codex_podman_executable]
    if settings.codex_podman_cgroup_manager:
        command.append(f"--cgroup-manager={settings.codex_podman_cgroup_manager}")
    command.extend(args)
    try:
        return subprocess.run(
            command,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        return subprocess.CompletedProcess(command, 127, stdout="", stderr=str(exc))


def _container_mount_check(
    context: DoctorContext,
    name: str,
    host_path: Path,
    container_path: str,
) -> DoctorCheck:
    settings = _settings(context)
    try:
        host_stat = host_path.stat()
    except OSError as exc:
        return DoctorCheck(
            name,
            False,
            f"workspace mount source is unavailable: {exc}",
            repair=CODEX_CONTAINER_REPAIR,
        )

    # compare kernel object identity so a recreated pathname cannot mask a stale bind mount
    result = _run_podman(
        context,
        [
            "exec",
            "--workdir",
            "/",
            settings.codex_container_name,
            "stat",
            "-Lc",
            "%d:%i",
            container_path,
        ],
    )
    expected = f"{host_stat.st_dev}:{host_stat.st_ino}"
    actual = (result.stdout or "").strip()
    ok = result.returncode == 0 and actual == expected
    if ok:
        detail = f"{container_path} matches the current workspace directory"
    elif result.returncode != 0:
        detail = _command_failure_detail(result)
    else:
        detail = f"{container_path} is bound to a stale workspace directory"
    return DoctorCheck(name, ok, detail, repair=None if ok else CODEX_CONTAINER_REPAIR)


def _command_failure_detail(result: subprocess.CompletedProcess[str]) -> str:
    output = str(result.stderr or result.stdout or "").strip()
    if not output:
        return f"command exited {result.returncode}"
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return "; ".join(lines[:2])


def _required_value_check(name: str, value: str | None) -> DoctorCheck:
    return DoctorCheck(name, bool(value), "configured" if value else "missing")


def _podman_info(podman_executable: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [podman_executable, "info", "--debug"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _podman_info_check(result: subprocess.CompletedProcess[str]) -> DoctorCheck:
    output = (result.stdout or result.stderr or "").strip()
    ok = result.returncode == 0
    detail = (
        "podman info completed"
        if ok
        else output or f"podman info exited {result.returncode}"
    )
    return DoctorCheck("podman-info", ok, detail)


def _podman_rootless_check(result: subprocess.CompletedProcess[str]) -> DoctorCheck:
    if result.returncode != 0:
        return DoctorCheck("podman-rootless", False, "podman info did not complete")
    output = result.stdout or result.stderr or ""
    ok = _podman_info_has_true(output, "rootless")
    return DoctorCheck(
        "podman-rootless",
        ok,
        "rootless podman is enabled" if ok else "podman is not running rootless",
    )


def _podman_cgroup_v2_check(result: subprocess.CompletedProcess[str]) -> DoctorCheck:
    local_ok, local_detail = _local_cgroup_v2_detail()
    if result.returncode != 0:
        return DoctorCheck(
            "podman-cgroup-v2", False, f"podman info did not complete; {local_detail}"
        )
    output = result.stdout or result.stderr or ""
    podman_ok = _podman_info_mentions(
        output, "cgroupversion", "v2"
    ) or _podman_info_mentions(output, "cgroup version", "v2")
    ok = local_ok and podman_ok
    if ok:
        return DoctorCheck(
            "podman-cgroup-v2", True, f"podman reports cgroup v2; {local_detail}"
        )
    if not local_ok:
        return DoctorCheck("podman-cgroup-v2", False, local_detail)
    return DoctorCheck("podman-cgroup-v2", False, "podman does not report cgroup v2")


def _podman_network_helper_check() -> DoctorCheck:
    path = shutil.which("slirp4netns")
    return DoctorCheck(
        "podman-network-helper",
        path is not None,
        f"found {path}" if path else "`slirp4netns` was not found on PATH",
    )


def _podman_run_flags_check(podman_executable: str) -> DoctorCheck:
    result = subprocess.run(
        [podman_executable, "run", "--help"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    output = result.stdout or result.stderr or ""
    required = ("--userns", "--network", "--memory", "--cpus", "--pids-limit")
    missing = [flag for flag in required if flag not in output]
    ok = result.returncode == 0 and not missing
    if ok:
        return DoctorCheck(
            "podman-run-flags", True, "podman run supports the required sandbox flags"
        )
    detail = (
        ", ".join(missing)
        if missing
        else output.strip() or f"podman run --help exited {result.returncode}"
    )
    return DoctorCheck("podman-run-flags", False, f"missing required flags: {detail}")


def _codex_resource_limits_check(
    enforce_resource_limits: bool,
    cgroup_check: DoctorCheck,
    run_flags_check: DoctorCheck,
) -> DoctorCheck:
    if not enforce_resource_limits:
        return DoctorCheck(
            "codex-resource-limits", True, "resource limits disabled in codex config"
        )
    ok = cgroup_check.ok and run_flags_check.ok
    detail = (
        "resource limits enabled and cgroup v2 is available"
        if ok
        else "resource limits require working Podman cgroup v2 support; disable codex.enforce_resource_limits to bypass limits"
    )
    return DoctorCheck("codex-resource-limits", ok, detail)


def _local_cgroup_v2_detail() -> tuple[bool, str]:
    result = subprocess.run(
        ["stat", "-fc", "%T", "/sys/fs/cgroup"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    fs_type = (result.stdout or "").strip()
    if result.returncode != 0:
        detail = (result.stderr or "").strip() or "could not inspect /sys/fs/cgroup"
        return False, detail
    if fs_type != "cgroup2fs":
        return False, f"/sys/fs/cgroup is {fs_type}, expected cgroup2fs"
    try:
        mountinfo = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"could not read /proc/self/mountinfo: {exc}"
    if " - cgroup2 " not in mountinfo:
        return False, "/proc/self/mountinfo does not show a cgroup2 mount"
    return True, "/sys/fs/cgroup is cgroup2fs"


def _podman_info_has_true(output: str, key: str) -> bool:
    compact = "".join(output.lower().split())
    return f'"{key.lower()}":true' in compact or f"{key.lower()}:true" in compact


def _podman_info_mentions(output: str, key: str, value: str) -> bool:
    compact = "".join(output.lower().split())
    normalized_key = key.lower().replace(" ", "")
    normalized_value = value.lower()
    return (
        f'"{normalized_key}":"{normalized_value}"' in compact
        or f"{normalized_key}:{normalized_value}" in compact
    )


def _port_check(port: int, app_server_url: str) -> DoctorCheck:
    if _app_server_health_ok(app_server_url):
        return DoctorCheck(
            "codex-port",
            True,
            f"{app_server_url} is already serving Amber Codex health",
        )
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return DoctorCheck("codex-port", False, f"127.0.0.1:{port} is unavailable")
    return DoctorCheck("codex-port", True, f"127.0.0.1:{port} is available")


def _app_server_health_ok(app_server_url: str) -> bool:
    try:
        with request.urlopen(
            f"{app_server_url.rstrip('/')}/health", timeout=1
        ) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("ok") is True
        and payload.get("runner") == "codex-cli"
        and payload.get("yolo_mode") is True
    )


def _codex_auth_check(codex_home_dir: Path) -> DoctorCheck:
    auth_path = codex_home_dir / ".codex" / "auth.json"
    return DoctorCheck(
        "codex-cli-auth",
        auth_path.exists() and auth_path.stat().st_size > 0,
        f"found {auth_path}" if auth_path.exists() else f"missing {auth_path}",
    )


def _github_auth_check(github_auth_dir: Path) -> DoctorCheck:
    hosts_path = github_auth_dir / "hosts.yml"
    return DoctorCheck(
        "codex-github-auth",
        hosts_path.exists() and hosts_path.stat().st_size > 0,
        f"found {hosts_path}" if hosts_path.exists() else f"missing {hosts_path}",
    )


def _linear_check(api_key: str, api_url: str) -> DoctorCheck:
    from src.adapters.linear import LinearGraphQLClient

    try:
        viewer_id = LinearGraphQLClient(api_key=api_key, api_url=api_url).viewer_id()
    except Exception as exc:
        return DoctorCheck("linear-auth", False, str(exc))
    return DoctorCheck(
        "linear-auth",
        True,
        f"authenticated as Linear viewer {viewer_id[:8]}... (masked)",
    )
