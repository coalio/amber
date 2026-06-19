#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config.config import get_settings, workspace_dir
from src.adapters.linear import LinearGraphQLClient, LinearIssue
from src.config.workspace import load_workspace_config, write_workspace_config
from src.utils.time import local_now


DEFAULTS = {
    "enabled": True,
    "api_url": "https://api.linear.app/graphql",
    "poll_seconds": 60.0,
    "due_window_days": 2,
    "status_in_progress": "In Progress",
    "status_under_review": "Under Review",
    "status_completed": "Done",
}


def _progress(message: str) -> None:
    print(f"[linear-auth] {message}", flush=True)


def _read_key_from_stdin_or_prompt() -> str:
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return getpass.getpass("Linear personal API key: ").strip()


def configure(workspace: str | None) -> int:
    api_key = _read_key_from_stdin_or_prompt()
    if not api_key:
        print("No Linear API key provided.", file=sys.stderr)
        return 2
    config_path = workspace_dir(workspace) / "config.toml"
    if not config_path.exists():
        print(f"Workspace config is missing: {config_path}", file=sys.stderr)
        return 2
    data: dict[str, Any] = load_workspace_config(config_path)
    linear = data.setdefault("linear", {})
    if not isinstance(linear, dict):
        linear = {}
        data["linear"] = linear
    linear.update({**DEFAULTS, "api_key": api_key})
    write_workspace_config(config_path, data)
    get_settings.cache_clear()
    _progress(f"wrote Linear settings to {config_path}")
    return status(workspace)


def status(workspace: str | None) -> int:
    get_settings.cache_clear()
    settings = get_settings(workspace)
    if not settings.linear_api_key:
        print("Linear API key is not configured. Set AMBER_LINEAR_API_KEY or run configure.", file=sys.stderr)
        return 2
    client = LinearGraphQLClient(api_key=settings.linear_api_key, api_url=settings.linear_api_url)
    _progress("checking Linear viewer")
    try:
        viewer_id = client.viewer_id()
        issues = client.assigned_issues(assignee_id=viewer_id)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    due = _due_window_issues(issues, settings.timezone_name, settings.linear_due_window_days)
    _progress(f"authenticated as Linear viewer {viewer_id[:8]}... (masked)")
    _progress(f"assigned issues: {len(issues)}")
    _progress(f"uncompleted assigned issues due today/tomorrow: {len(due)}")
    for issue in due[:10]:
        due_date = issue.due_date.isoformat() if issue.due_date is not None else "no due date"
        print(f"- {issue.identifier} due {due_date}: {issue.title}")
    return 0


def _due_window_issues(issues: list[LinearIssue], timezone_name: str, due_window_days: int) -> list[LinearIssue]:
    today = local_now(timezone_name).date()
    window_end = today.fromordinal(today.toordinal() + due_window_days - 1)
    return [
        issue
        for issue in issues
        if not issue.is_terminal and issue.due_date is not None and issue.due_date <= window_end
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure or verify Amber's Linear API key.")
    parser.add_argument("--workspace", help="Workspace name or path.")
    parser.add_argument("action", choices=["configure", "status"])
    args = parser.parse_args()
    if args.action == "configure":
        return configure(args.workspace)
    return status(args.workspace)


if __name__ == "__main__":
    raise SystemExit(main())
