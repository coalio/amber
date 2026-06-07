#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.adapters.codex import build_codex_adapter, exec_in_codex_sandbox
from src.config.config import get_settings


def _progress(message: str) -> None:
    print(f"[codex-cli-auth] {message}", flush=True)


def _adapter():
    return build_codex_adapter(get_settings(), progress_callback=_progress)


def _read_secret(*env_names: str) -> str | None:
    for env_name in env_names:
        value = os.getenv(env_name)
        if value:
            return value.strip()
    if not sys.stdin.isatty():
        value = sys.stdin.read().strip()
        if value:
            return value
    return None


def prepare() -> int:
    adapter = _adapter()
    _progress("preparing sandbox")
    adapter.ensure_app_server()
    _progress("checking codex cli")
    return exec_in_codex_sandbox(adapter, ["codex", "--version"])


def login() -> int:
    return login_device()


def login_device() -> int:
    adapter = _adapter()
    _progress("preparing sandbox before Codex login")
    adapter.ensure_app_server()
    _progress("starting Codex device-auth login inside the sandbox")
    return exec_in_codex_sandbox(adapter, ["codex", "login", "--device-auth"], interactive=True)


def login_api_key() -> int:
    secret = _read_secret("OPENAI_API_KEY", "CODEX_API_KEY")
    if secret is None:
        print("Set OPENAI_API_KEY or CODEX_API_KEY, or pipe the key on stdin.", file=sys.stderr)
        return 2
    adapter = _adapter()
    _progress("preparing sandbox before Codex API-key login")
    adapter.ensure_app_server()
    _progress("logging Codex in with API key from stdin/env")
    return exec_in_codex_sandbox(adapter, ["codex", "login", "--with-api-key"], stdin_text=f"{secret}\n")


def login_access_token() -> int:
    secret = _read_secret("CODEX_ACCESS_TOKEN")
    if secret is None:
        print("Set CODEX_ACCESS_TOKEN, or pipe the access token on stdin.", file=sys.stderr)
        return 2
    adapter = _adapter()
    _progress("preparing sandbox before Codex access-token login")
    adapter.ensure_app_server()
    _progress("logging Codex in with access token from stdin/env")
    return exec_in_codex_sandbox(adapter, ["codex", "login", "--with-access-token"], stdin_text=f"{secret}\n")


def status() -> int:
    adapter = _adapter()
    _progress("preparing sandbox before checking Codex auth status")
    adapter.ensure_app_server()
    status_code = exec_in_codex_sandbox(adapter, ["codex", "login", "status"])
    if status_code == 0:
        return 0
    return exec_in_codex_sandbox(
        adapter,
        [
            "bash",
            "-lc",
            (
                "codex --version && "
                "if test -s \"$CODEX_HOME/auth.json\"; then "
                "echo \"Codex auth file exists at $CODEX_HOME/auth.json\"; "
                "else "
                "echo \"Codex auth file is missing at $CODEX_HOME/auth.json\" >&2; "
                "exit 1; "
                "fi"
            ),
        ],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Authorize or inspect the isolated Codex CLI account in the sandbox.")
    parser.add_argument("action", choices=["login", "login-device", "login-api-key", "login-access-token", "status", "prepare"])
    args = parser.parse_args()
    if args.action == "login":
        return login()
    if args.action == "login-device":
        return login_device()
    if args.action == "login-api-key":
        return login_api_key()
    if args.action == "login-access-token":
        return login_access_token()
    if args.action == "prepare":
        return prepare()
    return status()


if __name__ == "__main__":
    raise SystemExit(main())
