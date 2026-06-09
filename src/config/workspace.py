from __future__ import annotations

import os
import shlex
import shutil
import socket
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import request

from src.config.config import (
    WORKSPACE_NAME_RE,
    amber_home,
    default_config_path,
    get_settings,
    resource_codex_skill_dir,
    resource_prompt_dir,
    workspace_dir,
)


EDITABLE_PROMPTS = (
    "AI_SYSTEM_CASUAL.md",
    "AI_SYSTEM_WORK.md",
    "AI_ACTION_CONTRACT.md",
    "AI_INTERRUPTION.md",
    "MEMORY.md",
)
@dataclass(frozen=True)
class DoctorCheck:
    name: str
    ok: bool
    detail: str


def init_workspace(name: str, *, overwrite: bool = False) -> Path:
    workspace_name = _validate_workspace_name(name)
    target = amber_home() / "workspaces" / workspace_name
    target.mkdir(parents=True, exist_ok=True)

    for relative in (
        "prompts",
        "codex-skills/CodexRules",
        "telegram",
        "memories",
        "runtime-state",
        "logs",
        "codex/work",
        "codex/github-auth",
        "codex/codex-home",
    ):
        (target / relative).mkdir(parents=True, exist_ok=True)

    _copy_editable_prompts(target, overwrite=overwrite)
    _copy_codex_rules(target, overwrite=overwrite)
    _write_initial_config(target, workspace_name, overwrite=overwrite)
    return target


def load_workspace_config(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    if not isinstance(data, dict):
        raise RuntimeError(f"Workspace config must contain a TOML table: {path}")
    return data


def write_workspace_config(path: Path, data: dict[str, Any]) -> None:
    path.write_text(render_toml(data), encoding="utf-8")
    path.chmod(0o600)
    get_settings.cache_clear()


def render_toml(data: dict[str, Any]) -> str:
    lines: list[str] = []
    scalars = {key: value for key, value in data.items() if not isinstance(value, dict)}
    sections = {key: value for key, value in data.items() if isinstance(value, dict)}
    for key, value in scalars.items():
        lines.append(f"{key} = {_toml_value(value)}")
    for section, values in sections.items():
        if lines and lines[-1] != "":
            lines.append("")
        lines.append(f"[{section}]")
        for key, value in values.items():
            lines.append(f"{key} = {_toml_value(value)}")
    return "\n".join(lines).rstrip() + "\n"


def doctor_workspace(workspace: str | Path, *, validate_external: bool = False, include_service: bool = False) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    try:
        settings = get_settings(workspace)
        checks.append(DoctorCheck("config", True, f"loaded {settings.workspace_dir / 'config.toml'}"))
    except Exception as exc:
        return [DoctorCheck("config", False, str(exc))]

    required_files = [
        settings.ai_orchestration_prompt_path,
        settings.codex_system_prompt_path,
        settings.ai_system_casual_prompt_path,
        settings.ai_system_work_prompt_path,
        settings.ai_action_contract_prompt_path,
        settings.ai_interruption_prompt_path,
        settings.memory_prompt_path,
        settings.codex_rules_skill_path,
    ]
    missing = [str(path) for path in required_files if not path.exists()]
    checks.append(DoctorCheck("resources", not missing, "all prompts and skills exist" if not missing else "\n".join(missing)))

    checks.append(_required_value_check("ai.api_key", settings.ai_api_key))
    checks.append(_required_value_check("telegram.api_id", settings.telegram_api_id))
    checks.append(_required_value_check("telegram.api_hash", settings.telegram_api_hash))
    checks.append(_required_value_check("linear.api_key", settings.linear_api_key))

    podman_path = shutil.which(settings.codex_podman_executable)
    checks.append(
        DoctorCheck(
            "podman",
            podman_path is not None,
            f"found {podman_path}" if podman_path else f"`{settings.codex_podman_executable}` was not found on PATH",
        )
    )
    if podman_path and validate_external:
        podman_info = _podman_info(settings.codex_podman_executable)
        checks.append(_podman_info_check(podman_info))
        checks.append(_podman_rootless_check(podman_info))
        cgroup_check = _podman_cgroup_v2_check(podman_info)
        checks.append(cgroup_check)
        checks.append(_podman_network_helper_check())
        run_flags_check = _podman_run_flags_check(settings.codex_podman_executable)
        checks.append(run_flags_check)
        checks.append(_codex_resource_limits_check(settings.codex_enforce_resource_limits, cgroup_check, run_flags_check))

    checks.append(_port_check(settings.codex_app_server_port, settings.codex_app_server_url))
    checks.append(
        DoctorCheck(
            "telegram-session",
            settings.telegram_session_path.exists(),
            f"found {settings.telegram_session_path}" if settings.telegram_session_path.exists() else f"missing {settings.telegram_session_path}",
        )
    )
    checks.append(_codex_auth_check(settings.codex_home_dir))
    checks.append(_github_auth_check(settings.codex_github_auth_dir))

    if validate_external and settings.linear_api_key:
        checks.append(_linear_check(settings.linear_api_key, settings.linear_api_url))
    if include_service:
        checks.append(service_status_check(settings.workspace_dir))
    return checks


def service_unit_name(workspace: str | Path) -> str:
    resolved = workspace_dir(workspace)
    return f"amber-{resolved.name}.service"


def service_unit_path(workspace: str | Path) -> Path:
    xdg_config = Path(os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config"))).expanduser()
    return xdg_config / "systemd" / "user" / service_unit_name(workspace)


def render_user_service(workspace: str | Path) -> str:
    resolved = workspace_dir(workspace)
    executable = amber_home() / "bin" / "amber"
    return "\n".join(
        [
            "[Unit]",
            f"Description=Amber workspace {resolved.name}",
            "After=network-online.target",
            "Wants=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            f"Environment=AMBER_HOME={amber_home()}",
            f"ExecStart={_systemd_escape(str(executable))} run --workspace {_systemd_escape(str(resolved))}",
            "Restart=on-failure",
            "RestartSec=5",
            "WorkingDirectory=%h",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )


def install_user_service(workspace: str | Path, *, enable: bool = False, now: bool = False) -> Path:
    path = service_unit_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_user_service(workspace), encoding="utf-8")
    if enable or now:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        command = ["systemctl", "--user", "enable"]
        if now:
            command.append("--now")
        command.append(service_unit_name(workspace))
        subprocess.run(command, check=True)
    return path


def uninstall_user_service(workspace: str | Path) -> None:
    name = service_unit_name(workspace)
    subprocess.run(["systemctl", "--user", "disable", "--now", name], check=False)
    path = service_unit_path(workspace)
    if path.exists():
        path.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)


def service_status_check(workspace: str | Path) -> DoctorCheck:
    name = service_unit_name(workspace)
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


def first_available_port(*, start: int = 8765, end: int = 8865) -> int:
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"No free local TCP port found in range {start}-{end}.")


def _validate_workspace_name(name: str) -> str:
    normalized = name.strip().lower()
    if not WORKSPACE_NAME_RE.fullmatch(normalized):
        raise RuntimeError("Workspace names must be lowercase slugs containing only a-z, 0-9, and hyphen.")
    return normalized


def _copy_editable_prompts(target: Path, *, overwrite: bool) -> None:
    source = resource_prompt_dir()
    for filename in EDITABLE_PROMPTS:
        _copy_if_needed(source / filename, target / "prompts" / filename, overwrite=overwrite)


def _copy_codex_rules(target: Path, *, overwrite: bool) -> None:
    source = resource_codex_skill_dir() / "CodexRules" / "SKILL.md"
    _copy_if_needed(source, target / "codex-skills" / "CodexRules" / "SKILL.md", overwrite=overwrite)


def _copy_if_needed(source: Path, destination: Path, *, overwrite: bool) -> None:
    if destination.exists() and not overwrite:
        return
    if not source.exists():
        raise RuntimeError(f"Required Amber resource is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _write_initial_config(target: Path, workspace_name: str, *, overwrite: bool) -> None:
    destination = target / "config.toml"
    if destination.exists() and not overwrite:
        return
    port = first_available_port()
    text = default_config_path().read_text(encoding="utf-8")
    text = text.replace("{workspace_name}", workspace_name)
    text = text.replace("app_server_port = 8765", f"app_server_port = {port}")
    text = text.replace("app_server_url = \"http://127.0.0.1:8765\"", f"app_server_url = \"http://127.0.0.1:{port}\"")
    destination.write_text(text, encoding="utf-8")
    destination.chmod(0o600)


def _toml_value(value: Any) -> str:
    if value is None:
        return '"none"'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return _quote_toml_string(str(value))


def _quote_toml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


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
    detail = "podman info completed" if ok else output or f"podman info exited {result.returncode}"
    return DoctorCheck("podman-info", ok, detail)


def _podman_rootless_check(result: subprocess.CompletedProcess[str]) -> DoctorCheck:
    if result.returncode != 0:
        return DoctorCheck("podman-rootless", False, "podman info did not complete")
    output = result.stdout or result.stderr or ""
    ok = _podman_info_has_true(output, "rootless")
    return DoctorCheck("podman-rootless", ok, "rootless podman is enabled" if ok else "podman is not running rootless")


def _podman_cgroup_v2_check(result: subprocess.CompletedProcess[str]) -> DoctorCheck:
    local_ok, local_detail = _local_cgroup_v2_detail()
    if result.returncode != 0:
        return DoctorCheck("podman-cgroup-v2", False, f"podman info did not complete; {local_detail}")
    output = result.stdout or result.stderr or ""
    podman_ok = _podman_info_mentions(output, "cgroupversion", "v2") or _podman_info_mentions(output, "cgroup version", "v2")
    ok = local_ok and podman_ok
    if ok:
        return DoctorCheck("podman-cgroup-v2", True, f"podman reports cgroup v2; {local_detail}")
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
    required = ("--userns", "--network", "--cgroups", "--memory", "--cpus", "--pids-limit")
    missing = [flag for flag in required if flag not in output]
    ok = result.returncode == 0 and not missing
    if ok:
        return DoctorCheck("podman-run-flags", True, "podman run supports the required sandbox flags")
    detail = ", ".join(missing) if missing else output.strip() or f"podman run --help exited {result.returncode}"
    return DoctorCheck("podman-run-flags", False, f"missing required flags: {detail}")


def _codex_resource_limits_check(
    enforce_resource_limits: bool,
    cgroup_check: DoctorCheck,
    run_flags_check: DoctorCheck,
) -> DoctorCheck:
    if not enforce_resource_limits:
        return DoctorCheck("codex-resource-limits", True, "resource limits disabled in codex config")
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
    return f'"{normalized_key}":"{normalized_value}"' in compact or f"{normalized_key}:{normalized_value}" in compact


def _port_check(port: int, app_server_url: str) -> DoctorCheck:
    if _health_url_ok(app_server_url):
        return DoctorCheck("codex-port", True, f"{app_server_url} is already serving Amber Codex health")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return DoctorCheck("codex-port", False, f"127.0.0.1:{port} is unavailable")
    return DoctorCheck("codex-port", True, f"127.0.0.1:{port} is available")


def _health_url_ok(app_server_url: str) -> bool:
    try:
        with request.urlopen(f"{app_server_url.rstrip('/')}/health", timeout=1) as response:
            return response.status == 200
    except OSError:
        return False


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
    return DoctorCheck("linear-auth", True, f"authenticated as Linear viewer {viewer_id[:8]}... (masked)")


def _systemd_escape(value: str) -> str:
    return shlex.quote(value)
