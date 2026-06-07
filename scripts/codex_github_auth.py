#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.adapters.codex import build_codex_adapter, exec_in_codex_sandbox
from src.config.config import get_settings


def _progress(message: str) -> None:
    print(f"[codex-auth] {message}", flush=True)


def _adapter():
    return build_codex_adapter(get_settings(), progress_callback=_progress)


def login() -> int:
    adapter = _adapter()
    _progress("preparing sandbox before GitHub login")
    adapter.ensure_app_server()
    _progress("starting GitHub login inside the codex sandbox")
    login_code = exec_in_codex_sandbox(
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
    )
    if login_code != 0:
        return login_code
    _progress("configuring git to use the sandbox GitHub credentials")
    return exec_in_codex_sandbox(adapter, ["gh", "auth", "setup-git", "--hostname", "github.com"])


def status() -> int:
    adapter = _adapter()
    _progress("preparing sandbox before checking GitHub auth status")
    adapter.ensure_app_server()
    status_code = exec_in_codex_sandbox(adapter, ["gh", "auth", "status", "--hostname", "github.com"])
    if status_code != 0:
        return status_code
    setup_code = exec_in_codex_sandbox(adapter, ["gh", "auth", "setup-git", "--hostname", "github.com"])
    if setup_code != 0:
        return setup_code
    return exec_in_codex_sandbox(adapter, ["gh", "api", "user", "--jq", ".login"])


def prepare() -> int:
    adapter = _adapter()
    _progress("preparing sandbox")
    adapter.ensure_app_server()
    _progress("sandbox is ready")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Authorize or inspect the dedicated Codex sandbox GitHub account.")
    parser.add_argument("action", choices=["login", "status", "prepare"])
    args = parser.parse_args()
    if args.action == "login":
        return login()
    if args.action == "prepare":
        return prepare()
    return status()


if __name__ == "__main__":
    raise SystemExit(main())
