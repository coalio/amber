from __future__ import annotations

import os
import subprocess
from collections.abc import Callable

from src.adapters.codex.adapter import CodexAdapter
from src.config.config import Settings


ProgressCallback = Callable[[str], None]


def build_codex_adapter(
    settings: Settings,
    *,
    progress_callback: ProgressCallback | None = None,
    command_runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> CodexAdapter:
    # preserve the workspace isolation contract in one place so auth helpers,
    # runtime composition, and setup flows cannot drift onto different mounts
    return CodexAdapter(
        workdir=settings.codex_workdir,
        app_server_url=settings.codex_app_server_url,
        app_server_port=settings.codex_app_server_port,
        podman_executable=settings.codex_podman_executable,
        cgroup_manager=settings.codex_podman_cgroup_manager,
        enforce_resource_limits=settings.codex_enforce_resource_limits,
        container_name=settings.codex_container_name,
        app_server_command=settings.codex_app_server_command,
        github_auth_dir=settings.codex_github_auth_dir,
        codex_home_dir=settings.codex_home_dir,
        codex_model=settings.codex_model,
        codex_reasoning_effort=settings.codex_reasoning_effort,
        auto_update=settings.codex_auto_update,
        system_prompt_path=settings.codex_system_prompt_path,
        rules_skill_path=settings.codex_rules_skill_path,
        command_runner=command_runner,
        progress_callback=progress_callback,
        release_version=settings.release_version,
    )


def exec_in_codex_sandbox(
    adapter: CodexAdapter,
    args: list[str],
    *,
    interactive: bool = False,
    stdin_text: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> int:
    pipe_stdin = stdin_text is not None
    command: list[str] = [adapter._podman_executable]

    # mirror the container environment used by the long-running Codex app-server
    # so login/status helpers write into the same isolated workspace auth stores
    if adapter._cgroup_manager:
        command.append(f"--cgroup-manager={adapter._cgroup_manager}")
    command.append("exec")
    if interactive:
        command.append("-it")
    elif pipe_stdin:
        command.append("-i")
    command.extend(
        [
            "--user",
            str(os.getuid()),
            "-e",
            "HOME=/codex-home",
            "-e",
            "CODEX_HOME=/codex-home/.codex",
            "-e",
            "GH_CONFIG_DIR=/github-auth",
            adapter._container_name,
            *args,
        ]
    )

    result = runner(command, check=False, input=stdin_text, text=stdin_text is not None)
    return int(result.returncode)


def require_sandbox_success(
    adapter: CodexAdapter,
    args: list[str],
    *,
    label: str,
    interactive: bool = False,
    stdin_text: str | None = None,
) -> None:
    status = exec_in_codex_sandbox(adapter, args, interactive=interactive, stdin_text=stdin_text)
    if status != 0:
        raise RuntimeError(f"{label} failed with exit code {status}.")
