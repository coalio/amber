from __future__ import annotations

import re

from src import cli


def test_top_level_help_uses_limited_pink_accent(monkeypatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("TERM", "xterm-256color")

    help_text = cli._build_parser().format_help()
    ansi_codes = set(re.findall(r"\x1b\[[0-9;]*m", help_text))

    assert ansi_codes == {cli.HELP_ACCENT, cli.HELP_RESET}
    assert f"{cli.HELP_ACCENT}usage:{cli.HELP_RESET} {cli.HELP_ACCENT}amber{cli.HELP_RESET}" in help_text
    assert f"{cli.HELP_ACCENT}Amber{cli.HELP_RESET} workspace runtime" in help_text
    assert f"{cli.HELP_ACCENT}positional arguments:{cli.HELP_RESET}" in help_text
    assert f"{cli.HELP_ACCENT}options:{cli.HELP_RESET}" in help_text
    assert f"{cli.HELP_ACCENT}run{cli.HELP_RESET}                 Run Amber for a workspace." in help_text
    assert f"{cli.HELP_ACCENT}workspace{cli.HELP_RESET}           Manage Amber workspaces." in help_text
    assert f"{cli.HELP_ACCENT}service{cli.HELP_RESET}             Manage the optional systemd user service." in help_text
    assert f"{cli.HELP_ACCENT}version{cli.HELP_RESET}             Print Amber release information." in help_text
    assert f"[{cli.HELP_ACCENT}--workspace{cli.HELP_RESET} WORKSPACE]" in help_text
    assert f"{cli.HELP_ACCENT}--help{cli.HELP_RESET}" not in help_text
    assert f"{{{cli.HELP_ACCENT}run" not in help_text


def test_subcommand_help_accents_subcommand_rows(monkeypatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("TERM", "xterm-256color")

    parser = cli._build_parser()
    workspace_parser = next(action for action in parser._actions if action.dest == "command").choices["workspace"]
    help_text = workspace_parser.format_help()

    assert f"{cli.HELP_ACCENT}init{cli.HELP_RESET}                Create a workspace." in help_text
    assert f"{cli.HELP_ACCENT}configure{cli.HELP_RESET}           Configure and validate required auth." in help_text
    assert f"{cli.HELP_ACCENT}doctor{cli.HELP_RESET}              Check a workspace." in help_text
    assert f"{{{cli.HELP_ACCENT}init" not in help_text


def test_no_color_disables_help_accent(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("TERM", "xterm-256color")

    help_text = cli._build_parser().format_help()

    assert "\x1b[" not in help_text
