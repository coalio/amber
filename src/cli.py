from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from telethon import TelegramClient

from src.adapters.codex import build_codex_adapter, require_sandbox_success
from src.adapters.linear import LinearGraphQLClient
from src.config.config import get_settings, workspace_dir
from src.config.workspace import (
    doctor_workspace,
    init_workspace,
    install_user_service,
    load_workspace_config,
    render_toml,
    service_unit_name,
    uninstall_user_service,
)
from src.runtime import build_application


def main(argv: list[str] | None = None) -> int:
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="amber", description="Amber workspace runtime")
    parser.add_argument("--workspace", help="Workspace name or path. With no command, aliases to `run`.")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run Amber for a workspace.")
    run_parser.add_argument("--workspace", required=True, help="Workspace name or path.")

    workspace_parser = subparsers.add_parser("workspace", help="Manage Amber workspaces.")
    workspace_subparsers = workspace_parser.add_subparsers(dest="workspace_command", required=True)
    init_parser = workspace_subparsers.add_parser("init", help="Create a workspace.")
    init_parser.add_argument("name")
    init_parser.add_argument("--overwrite", action="store_true", help="Overwrite seeded config, prompts, and skills.")
    configure_parser = workspace_subparsers.add_parser("configure", help="Configure and validate required auth.")
    configure_parser.add_argument("workspace")
    doctor_parser = workspace_subparsers.add_parser("doctor", help="Check a workspace.")
    doctor_parser.add_argument("workspace")
    doctor_parser.add_argument("--external", action="store_true", help="Run external auth checks where possible.")
    doctor_parser.add_argument("--service", action="store_true", help="Include systemd user service status.")

    service_parser = subparsers.add_parser("service", help="Manage the optional systemd user service.")
    service_subparsers = service_parser.add_subparsers(dest="service_command", required=True)
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
    settings = get_settings(args.workspace)
    app = build_application(settings=settings, enable_telegram=True)
    asyncio.run(app.run_telegram_forever())
    return 0


def _workspace(args: argparse.Namespace) -> int:
    if args.workspace_command == "init":
        path = init_workspace(args.name, overwrite=args.overwrite)
        print(f"workspace created: {path}")
        return 0
    if args.workspace_command == "configure":
        return _configure_workspace(args.workspace)
    if args.workspace_command == "doctor":
        checks = doctor_workspace(args.workspace, validate_external=args.external, include_service=args.service)
        return _print_checks(checks)
    raise RuntimeError(f"Unknown workspace command: {args.workspace_command}")


def _service(args: argparse.Namespace) -> int:
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
    settings = get_settings()
    print(f"amber release: {settings.release_dir.name}")
    print(f"release dir: {settings.release_dir}")
    print(f"resources dir: {settings.resources_dir}")
    return 0


def _configure_workspace(workspace: str | Path) -> int:
    resolved = workspace_dir(workspace)
    config_path = resolved / "config.toml"
    if not config_path.exists():
        raise RuntimeError(f"Workspace config is missing. Run `amber workspace init {resolved.name}` first.")

    # collect and persist credentials before validation so every external auth
    # step reads from the same workspace-local config that runtime will use
    data = load_workspace_config(config_path)
    _ensure_section(data, "ai")
    _ensure_section(data, "telegram")
    _ensure_section(data, "linear")
    _ensure_section(data, "codex")

    print(f"Configuring Amber workspace: {resolved}")
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
    config_path.write_text(render_toml(data), encoding="utf-8")
    config_path.chmod(0o600)
    get_settings.cache_clear()

    # validate the required integrations in runtime order so setup fails at the
    # first missing dependency instead of leaving a partially-working workspace
    settings = get_settings(resolved)
    _validate_linear(settings.linear_api_key, settings.linear_api_url)
    asyncio.run(_validate_telegram(settings))
    _configure_codex_cli(settings)
    _configure_codex_github(settings)
    checks = doctor_workspace(resolved, validate_external=True)
    return _print_checks(checks)


def _ensure_section(data: dict[str, Any], name: str) -> None:
    if not isinstance(data.get(name), dict):
        data[name] = {}


def _prompt_required(label: str, env_name: str, current: Any) -> str:
    existing = _existing_text(os.getenv(env_name) or current)
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
    prompt = f"{label}"
    if existing:
        prompt += f" [{_masked(existing)}]"
    prompt += ": "
    if sys.stdin.isatty():
        raw = getpass.getpass(prompt).strip()
    else:
        raw = input(prompt).strip()
    value = raw or existing
    if not value:
        raise RuntimeError(f"{label} is required.")
    return value


def _prompt_optional_secret(label: str, env_name: str, current: Any = None) -> str | None:
    existing = _existing_text(os.getenv(env_name) or current)
    prompt = f"{label}"
    if existing:
        prompt += f" [{_masked(existing)}]"
    prompt += " (leave blank for interactive login): "
    if sys.stdin.isatty():
        raw = getpass.getpass(prompt).strip()
    else:
        raw = input(prompt).strip()
    value = raw or existing
    return value or None


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
    if not api_key:
        raise RuntimeError("Linear API key is required.")
    viewer_id = LinearGraphQLClient(api_key=api_key, api_url=api_url).viewer_id()
    print(f"linear auth ok: {viewer_id[:8]}... (masked)")


async def _validate_telegram(settings: Any) -> None:
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


def _configure_codex_cli(settings: Any) -> None:
    adapter = build_codex_adapter(settings, progress_callback=lambda message: print(f"[codex-cli-auth] {message}", flush=True))
    adapter.ensure_app_server()
    method = input("Codex CLI auth method [api-key/device/access-token] (api-key): ").strip().lower() or "api-key"
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


def _configure_codex_github(settings: Any) -> None:
    adapter = build_codex_adapter(settings, progress_callback=lambda message: print(f"[codex-github-auth] {message}", flush=True))
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
