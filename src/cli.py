from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from src.cli_input import choice_menu_supported, headless_enabled, read_choice, read_masked_secret


CODEX_AUTH_METHODS = ("api-key", "device", "access-token")
HELP_ACCENT = "\033[38;5;218m"
HELP_RESET = "\033[0m"
HELP_COMMANDS = {
    "configure",
    "doctor",
    "init",
    "install",
    "run",
    "service",
    "start",
    "status",
    "stop",
    "uninstall",
    "version",
    "workspace",
}
HELP_ACCENT_OPTIONS = {"--workspace"}


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None and args.workspace:
        args.command = "run"
    if args.command == "run":
        return _run(args)
    if args.command == "workspace":
        return _workspace(args)
    if args.command == "service":
        return _service(args)
    if args.command == "version":
        return _version()
    parser.print_help()
    return 2


class AmberArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("color", False)
        super().__init__(*args, **kwargs)

    def format_help(self) -> str:
        return _accent_help(super().format_help())

    def format_usage(self) -> str:
        return _accent_help(super().format_usage())


def _accent_help(text: str) -> str:
    if not _help_color_enabled():
        return text
    return "".join(_accent_help_line(line) for line in text.splitlines(keepends=True))


def _help_color_enabled() -> bool:
    if os.getenv("NO_COLOR") is not None or os.getenv("TERM") == "dumb":
        return False
    force_color = os.getenv("FORCE_COLOR") or os.getenv("CLICOLOR_FORCE")
    if force_color and force_color != "0":
        return True
    return sys.stdout.isatty()


def _accent_help_line(line: str) -> str:
    body, newline = _split_line_ending(line)
    stripped = body.lstrip()
    indent = body[: len(body) - len(stripped)]

    # emphasize the usage label and the command name without recoloring arguments broadly
    if stripped.startswith("usage: "):
        return _accent_usage_line(body) + newline
    if stripped in {"positional arguments:", "options:"}:
        return f"{indent}{_accent(stripped)}{newline}"
    if stripped.startswith("Amber "):
        return f"{indent}{_accent('Amber')}{stripped[len('Amber'):]}{newline}"

    command_line = _accent_command_action_line(indent, stripped)
    if command_line is not None:
        return command_line + newline
    return _accent_option_tokens(body) + newline


def _split_line_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    return line, ""


def _accent_usage_line(line: str) -> str:
    usage = "usage: "
    if line.startswith(usage):
        line = f"{_accent('usage:')} {line[len(usage):]}"
    line = re.sub(r"(?<=\s)amber(?=\s|$)", _accent("amber"), line, count=1)
    return _accent_option_tokens(line)


def _accent_command_action_line(indent: str, stripped: str) -> str | None:
    match = re.match(r"^(\S+)(.*)$", stripped)
    if match is None or match.group(1) not in HELP_COMMANDS:
        return None
    return f"{indent}{_accent(match.group(1))}{match.group(2)}"


def _accent_option_tokens(text: str) -> str:
    for option in sorted(HELP_ACCENT_OPTIONS, key=len, reverse=True):
        text = re.sub(rf"(?<![\w-]){re.escape(option)}(?![\w-])", _accent(option), text)
    return text


def _accent(text: str) -> str:
    return f"{HELP_ACCENT}{text}{HELP_RESET}"


def _build_parser() -> argparse.ArgumentParser:
    parser = AmberArgumentParser(prog="amber", description="Amber workspace runtime")
    parser.add_argument("--workspace", help="Workspace name or path. With no command, aliases to `run`.")
    subparsers = parser.add_subparsers(dest="command", parser_class=AmberArgumentParser)

    run_parser = subparsers.add_parser("run", help="Run Amber for a workspace.")
    run_parser.add_argument("--workspace", required=True, help="Workspace name or path.")

    workspace_parser = subparsers.add_parser("workspace", help="Manage Amber workspaces.")
    workspace_subparsers = workspace_parser.add_subparsers(
        dest="workspace_command",
        required=True,
        parser_class=AmberArgumentParser,
    )
    init_parser = workspace_subparsers.add_parser("init", help="Create a workspace.")
    init_parser.add_argument("name")
    init_parser.add_argument("--overwrite", action="store_true", help="Overwrite seeded config, prompts, and skills.")
    configure_parser = workspace_subparsers.add_parser("configure", help="Configure and validate required auth.")
    configure_parser.add_argument("workspace")
    configure_parser.add_argument("--headless", action="store_true", help="Use environment/config values without prompting.")
    doctor_parser = workspace_subparsers.add_parser("doctor", help="Check a workspace.")
    doctor_parser.add_argument("workspace")
    doctor_parser.add_argument("--external", action="store_true", help="Run external auth checks where possible.")
    doctor_parser.add_argument("--service", action="store_true", help="Include systemd user service status.")

    service_parser = subparsers.add_parser("service", help="Manage the optional systemd user service.")
    service_subparsers = service_parser.add_subparsers(
        dest="service_command",
        required=True,
        parser_class=AmberArgumentParser,
    )
    install_parser = service_subparsers.add_parser("install", help="Install the systemd user unit.")
    install_parser.add_argument("--workspace", required=True)
    install_parser.add_argument("--enable", action="store_true")
    install_parser.add_argument("--now", action="store_true")
    for command in ("start", "stop", "status", "uninstall"):
        item = service_subparsers.add_parser(command, help=f"{command.title()} the systemd user unit.")
        item.add_argument("--workspace", required=True)

    subparsers.add_parser("version", help="Print Amber release information.")
    return parser


def _run(args: argparse.Namespace) -> int:
    asyncio.run(_run_telegram_app(args.workspace))
    return 0


async def _run_telegram_app(workspace: str | Path) -> None:
    from src.config.config import get_settings
    from src.runtime import build_application

    # build telegram runtime inside the loop that will own telethon callbacks
    settings = get_settings(workspace)
    app = build_application(settings=settings, enable_telegram=True)
    await app.run_telegram_forever()


def _workspace(args: argparse.Namespace) -> int:
    from src.config.workspace import doctor_workspace, init_workspace

    if args.workspace_command == "init":
        path = init_workspace(args.name, overwrite=args.overwrite)
        print(f"workspace created: {path}")
        return 0
    if args.workspace_command == "configure":
        if getattr(args, "headless", False):
            previous_headless = os.environ.get("AMBER_HEADLESS")
            os.environ["AMBER_HEADLESS"] = "1"
            try:
                return _configure_workspace(args.workspace)
            finally:
                if previous_headless is None:
                    os.environ.pop("AMBER_HEADLESS", None)
                else:
                    os.environ["AMBER_HEADLESS"] = previous_headless
        return _configure_workspace(args.workspace)
    if args.workspace_command == "doctor":
        checks = doctor_workspace(args.workspace, validate_external=args.external, include_service=args.service)
        return _print_checks(checks)
    raise RuntimeError(f"Unknown workspace command: {args.workspace_command}")


def _service(args: argparse.Namespace) -> int:
    from src.config.workspace import install_user_service, service_unit_name, uninstall_user_service

    unit_name = service_unit_name(args.workspace)
    if args.service_command == "install":
        path = install_user_service(args.workspace, enable=args.enable or args.now, now=args.now)
        print(f"installed user service: {path}")
        return 0
    if args.service_command == "uninstall":
        uninstall_user_service(args.workspace)
        print(f"removed user service: {unit_name}")
        return 0
    if args.service_command in {"start", "stop", "status"}:
        command = ["systemctl", "--user", args.service_command, unit_name]
        return subprocess.run(command, check=False).returncode
    raise RuntimeError(f"Unknown service command: {args.service_command}")


def _version() -> int:
    from src.config.config import get_settings

    settings = get_settings()
    print(f"amber release: {settings.release_dir.name}")
    print(f"release dir: {settings.release_dir}")
    print(f"resources dir: {settings.resources_dir}")
    return 0


def _configure_workspace(workspace: str | Path) -> int:
    from src.config.config import get_settings, workspace_dir
    from src.config.workspace import doctor_workspace, load_workspace_config, write_workspace_config

    resolved = workspace_dir(workspace)
    config_path = resolved / "config.toml"
    if not config_path.exists():
        raise RuntimeError(f"Workspace config is missing. Run `amber workspace init {resolved.name}` first.")

    data = load_workspace_config(config_path)
    _ensure_section(data, "ai")
    _ensure_section(data, "telegram")
    _ensure_section(data, "linear")
    _ensure_section(data, "codex")

    print(f"Configuring Amber workspace: {resolved}")
    try:
        codex_adapter = _prepare_codex_sandbox_before_secrets(get_settings(resolved), data, config_path, resolved)
    except RuntimeError as exc:
        raise RuntimeError(_format_codex_setup_error(exc, resolved, credentials_saved=False)) from exc

    # collect and persist credentials before auth validation so every external
    # step reads from the same workspace-local config that runtime will use
    data["ai"]["api_key"] = _prompt_required_secret("AI/OpenAI API key", "AMBER_AI_API_KEY", data["ai"].get("api_key"))
    data["ai"]["model"] = _prompt_required("AI model", "AMBER_AI_MODEL", data["ai"].get("model"))
    data["telegram"]["api_id"] = _prompt_required("Telegram API ID", "API_ID", data["telegram"].get("api_id"))
    data["telegram"]["api_hash"] = _prompt_required_secret("Telegram API hash", "API_HASH", data["telegram"].get("api_hash"))
    data["linear"]["api_key"] = _prompt_required_secret("Linear API key", "AMBER_LINEAR_API_KEY", data["linear"].get("api_key"))
    data["linear"]["api_url"] = _prompt_required("Linear API URL", "AMBER_LINEAR_API_URL", data["linear"].get("api_url"))
    data["codex"]["model"] = _prompt_required("Codex model", "AMBER_CODEX_MODEL", data["codex"].get("model"))
    data["codex"]["reasoning_effort"] = _prompt_required(
        "Codex reasoning effort",
        "AMBER_CODEX_REASONING_EFFORT",
        data["codex"].get("reasoning_effort"),
    )
    write_workspace_config(config_path, data)

    # validate the required integrations in runtime order so setup fails at the
    # first missing dependency instead of leaving a partially-working workspace
    settings = get_settings(resolved)
    _validate_linear(settings.linear_api_key, settings.linear_api_url)
    asyncio.run(_validate_telegram(settings))
    try:
        _configure_codex_cli(settings, adapter=codex_adapter)
        _configure_codex_github(settings, adapter=codex_adapter)
    except RuntimeError as exc:
        raise RuntimeError(_format_codex_setup_error(exc, resolved)) from exc
    checks = doctor_workspace(resolved, validate_external=True)
    return _print_checks(checks)


def _prepare_codex_sandbox_before_secrets(settings: Any, data: dict[str, Any], config_path: Path, workspace: Path) -> Any:
    from src.adapters.codex import build_codex_adapter
    from src.config.workspace import write_workspace_config

    try:
        adapter = build_codex_adapter(
            settings,
            progress_callback=lambda message: print(f"[codex-preflight] {message}", flush=True),
        )
        adapter.ensure_app_server()
        return adapter
    except RuntimeError as exc:
        if not _should_retry_codex_cgroup_fallback(settings, exc):
            raise

    print(
        "codex preflight hit a Podman cgroup issue; retrying with Podman cgroupfs and Codex resource limits disabled.",
        file=sys.stderr,
    )
    _ensure_section(data, "codex")
    data["codex"]["enforce_resource_limits"] = False
    data["codex"]["podman_cgroup_manager"] = "cgroupfs"
    write_workspace_config(config_path, data)
    settings = _reload_workspace_settings(workspace)
    adapter = build_codex_adapter(
        settings,
        progress_callback=lambda message: print(f"[codex-preflight-cgroupfs] {message}", flush=True),
    )
    adapter.ensure_app_server()
    print(
        "codex preflight ok; saved codex.podman_cgroup_manager = cgroupfs and codex.enforce_resource_limits = false.",
        file=sys.stderr,
    )
    return adapter


def _should_retry_codex_cgroup_fallback(settings: Any, exc: RuntimeError) -> bool:
    if not _looks_like_codex_resource_limit_failure(exc):
        return False
    cgroup_manager = str(getattr(settings, "codex_podman_cgroup_manager", "") or "").strip().lower()
    return bool(getattr(settings, "codex_enforce_resource_limits", True)) or cgroup_manager != "cgroupfs"


def _looks_like_codex_resource_limit_failure(exc: RuntimeError) -> bool:
    message = str(exc).lower()
    markers = (
        "could not find cgroup mount",
        "cgroup",
        "--memory",
        "--cpus",
        "--pids-limit",
        "resource limit",
        "interactive authentication required",
        "unable to apply cgroup configuration",
        "not compatible with nocgroups",
        "nocgroups",
    )
    return any(marker in message for marker in markers)


def _reload_workspace_settings(workspace: Path) -> Any:
    from src.config.config import get_settings

    get_settings.cache_clear()
    return get_settings(workspace)


def _ensure_section(data: dict[str, Any], name: str) -> None:
    if not isinstance(data.get(name), dict):
        data[name] = {}


def _prompt_required(label: str, env_name: str, current: Any) -> str:
    existing = _existing_text(os.getenv(env_name) or current)
    if headless_enabled():
        if existing:
            return existing
        raise RuntimeError(f"{label} is required in headless mode. Set {env_name}.")
    prompt = f"{label}"
    if existing:
        prompt += f" [{_masked(existing) if 'KEY' in env_name or 'HASH' in env_name else existing}]"
    prompt += ": "
    raw = input(prompt).strip()
    value = raw or existing
    if not value:
        raise RuntimeError(f"{label} is required.")
    return value


def _prompt_required_secret(label: str, env_name: str, current: Any) -> str:
    existing = _existing_text(os.getenv(env_name) or current)
    if headless_enabled():
        if existing:
            return existing
        raise RuntimeError(f"{label} is required in headless mode. Set {env_name}.")
    prompt = f"{label}"
    if existing:
        prompt += f" [{_masked(existing)}]"
    prompt += ": "
    raw = _prompt_secret(prompt).strip()
    value = raw or existing
    if not value:
        raise RuntimeError(f"{label} is required.")
    return value


def _prompt_optional_secret(label: str, env_name: str, current: Any = None) -> str | None:
    existing = _existing_text(os.getenv(env_name) or current)
    if headless_enabled():
        return existing or None
    prompt = f"{label}"
    if existing:
        prompt += f" [{_masked(existing)}]"
    prompt += " (leave blank for interactive login): "
    raw = _prompt_secret(prompt).strip()
    value = raw or existing
    return value or None


def _prompt_secret(prompt: str) -> str:
    if not sys.stdin.isatty():
        return input(prompt)
    return read_masked_secret(prompt)


def _prompt_choice(label: str, choices: tuple[str, ...], default: str) -> str:
    if default not in choices:
        raise ValueError("default must be one of choices.")
    if headless_enabled():
        return default
    if choice_menu_supported():
        return choices[read_choice(label, choices, choices.index(default))]

    raw = input(f"{label} [{'/'.join(choices)}] ({default}): ").strip().lower()
    return raw or default


def _existing_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "none" else text


def _masked(value: str) -> str:
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}...{value[-4:]}"


def _validate_linear(api_key: str | None, api_url: str) -> None:
    from src.adapters.linear import LinearGraphQLClient

    if not api_key:
        raise RuntimeError("Linear API key is required.")
    viewer_id = LinearGraphQLClient(api_key=api_key, api_url=api_url).viewer_id()
    print(f"linear auth ok: {viewer_id[:8]}... (masked)")


async def _validate_telegram(settings: Any) -> None:
    from telethon import TelegramClient

    if not settings.telegram_api_id or not settings.telegram_api_hash:
        raise RuntimeError("Telegram API ID and hash are required.")
    settings.telegram_session_path.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(
        str(settings.telegram_session_path),
        int(settings.telegram_api_id),
        settings.telegram_api_hash,
    )
    await client.start()
    await client.disconnect()
    print(f"telegram auth ok: {settings.telegram_session_path}")


def _format_codex_setup_error(exc: RuntimeError, workspace: Path, *, credentials_saved: bool = True) -> str:
    message = str(exc)
    prefix = "Workspace credentials were saved, but " if credentials_saved else ""
    if 'could not find cgroup mount in "/proc/self/cgroup"' in message:
        return (
            f"{prefix}Codex sandbox setup failed because Podman cannot access cgroups "
            "in this environment.\n"
            "Rerun the installer and choose the cgroupfs/no-limits fallback, or set "
            'codex.podman_cgroup_manager = "cgroupfs" and codex.enforce_resource_limits = false, then rerun:\n'
            f"  amber workspace configure {workspace}\n"
            'Original Podman error: could not find cgroup mount in "/proc/self/cgroup"'
        )
    if "not compatible with nocgroups" in message.lower() or "interactive authentication required" in message.lower():
        return (
            f"{prefix}Codex sandbox setup failed because Podman rejected the current rootless cgroup mode.\n"
            "Rerun the installer and choose the cgroupfs/no-limits fallback, or set "
            'codex.podman_cgroup_manager = "cgroupfs" and codex.enforce_resource_limits = false, then rerun:\n'
            f"  amber workspace configure {workspace}\n"
            f"{message}"
        )
    if message.startswith("Podman command failed"):
        return (
            f"{prefix}Codex sandbox setup failed while running Podman.\n"
            "Fix the Podman error below, then rerun:\n"
            f"  amber workspace configure {workspace}\n"
            f"{message}"
        )
    return message


def _configure_codex_cli(settings: Any, *, adapter: Any | None = None) -> None:
    from src.adapters.codex import build_codex_adapter, require_sandbox_success

    adapter = adapter or build_codex_adapter(
        settings,
        progress_callback=lambda message: print(f"[codex-cli-auth] {message}", flush=True),
    )
    adapter.ensure_app_server()
    method = _prompt_choice("Codex CLI auth method", CODEX_AUTH_METHODS, "api-key")
    if method == "device":
        require_sandbox_success(adapter, ["codex", "login", "--device-auth"], interactive=True, label="codex device login")
    elif method == "access-token":
        token = _prompt_required_secret("Codex access token", "CODEX_ACCESS_TOKEN", None)
        require_sandbox_success(adapter, ["codex", "login", "--with-access-token"], stdin_text=f"{token}\n", label="codex access-token login")
    elif method == "api-key":
        key = _prompt_required_secret("Codex API key", "CODEX_API_KEY", settings.ai_api_key)
        require_sandbox_success(adapter, ["codex", "login", "--with-api-key"], stdin_text=f"{key}\n", label="codex api-key login")
    else:
        raise RuntimeError("Codex CLI auth method must be api-key, device, or access-token.")
    require_sandbox_success(adapter, ["codex", "login", "status"], label="codex login status")


def _configure_codex_github(settings: Any, *, adapter: Any | None = None) -> None:
    from src.adapters.codex import build_codex_adapter, require_sandbox_success

    adapter = adapter or build_codex_adapter(
        settings,
        progress_callback=lambda message: print(f"[codex-github-auth] {message}", flush=True),
    )
    adapter.ensure_app_server()
    token = _prompt_optional_secret("Codex sandbox GitHub token", "GH_TOKEN")
    if token:
        require_sandbox_success(
            adapter,
            ["gh", "auth", "login", "--hostname", "github.com", "--with-token"],
            stdin_text=f"{token}\n",
            label="github token login",
        )
    else:
        require_sandbox_success(
            adapter,
            [
                "gh",
                "auth",
                "login",
                "--hostname",
                "github.com",
                "--git-protocol",
                "https",
                "--scopes",
                "repo,read:org,workflow",
            ],
            interactive=True,
            label="github login",
        )
    require_sandbox_success(adapter, ["gh", "auth", "setup-git", "--hostname", "github.com"], label="github setup-git")
    require_sandbox_success(adapter, ["gh", "auth", "status", "--hostname", "github.com"], label="github auth status")


def _print_checks(checks: list[Any]) -> int:
    failed = False
    for check in checks:
        marker = "ok" if check.ok else "fail"
        print(f"[{marker}] {check.name}: {check.detail}")
        failed = failed or not check.ok
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--workspace":
        sys.argv.insert(1, "run")
    raise SystemExit(main())
