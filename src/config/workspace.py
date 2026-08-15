from __future__ import annotations

import os
import shlex
import shutil
import socket
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from src.config.config import (
    WORKSPACE_NAME_RE,
    amber_home,
    default_config_path,
    get_settings,
    resource_codex_skill_dir,
    resource_prompt_dir,
    workspace_dir,
)
from src.config.codex_skills import CODEX_SKILL_NAMES
from src.config.doctor import codex_container_cleanup_command


EDITABLE_PROMPTS = (
    "AI_SYSTEM_CASUAL.md",
    "AI_SYSTEM_WORK.md",
    "AI_ACTION_CONTRACT.md",
    "AI_INTERRUPTION.md",
    "MEMORY.md",
)


def init_workspace(name: str, *, overwrite: bool = False) -> Path:
    workspace_name = _validate_workspace_name(name)
    target = amber_home() / "workspaces" / workspace_name
    target.mkdir(parents=True, exist_ok=True)

    for relative in (
        "prompts",
        *(f"codex-skills/{name}" for name in CODEX_SKILL_NAMES),
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
    _copy_codex_skills(target, overwrite=overwrite)
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
    _render_toml_table(lines, data, table_path=())
    return "\n".join(lines).rstrip() + "\n"


def _render_toml_table(lines: list[str], values: dict[str, Any], *, table_path: tuple[str, ...]) -> None:
    # write each table's scalar values before its child tables
    if table_path:
        if lines:
            lines.append("")
        lines.append(f"[{'.'.join(table_path)}]")

    for key, value in values.items():
        if not isinstance(value, dict):
            lines.append(f"{key} = {_toml_value(value)}")

    # preserve nested config tables instead of serializing them as Python strings
    for key, value in values.items():
        if isinstance(value, dict):
            _render_toml_table(lines, value, table_path=(*table_path, key))


def service_unit_name(workspace: str | Path) -> str:
    resolved = workspace_dir(workspace)
    return f"amber-{resolved.name}.service"


def service_unit_path(workspace: str | Path) -> Path:
    xdg_config = Path(os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config"))).expanduser()
    return xdg_config / "systemd" / "user" / service_unit_name(workspace)


def render_user_service(workspace: str | Path) -> str:
    resolved = workspace_dir(workspace)
    executable = amber_home() / "bin" / "amber"
    cleanup_command = codex_container_cleanup_command(resolved)
    return "\n".join(
        [
            "[Unit]",
            f"Description=Amber workspace {resolved.name}",
            "After=network-online.target",
            "Wants=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            "KillMode=process",
            f"Environment=AMBER_HOME={amber_home()}",
            f"ExecStart={_systemd_escape(str(executable))} run --workspace {_systemd_escape(str(resolved))}",
            f"ExecStop={_systemd_ignored_shell_command(cleanup_command)}",
            "TimeoutStopSec=25s",
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


def _copy_codex_skills(target: Path, *, overwrite: bool) -> None:
    for name in CODEX_SKILL_NAMES:
        source = resource_codex_skill_dir() / name / "SKILL.md"
        _copy_if_needed(source, target / "codex-skills" / name / "SKILL.md", overwrite=overwrite)


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


def _systemd_escape(value: str) -> str:
    return shlex.quote(value)


def _systemd_ignored_shell_command(command: list[str]) -> str:
    shell_command = f"{shlex.join(command)} >/dev/null 2>&1 || true"
    return f"-/bin/sh -lc {_systemd_escape(shell_command)}"
