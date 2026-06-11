from __future__ import annotations

import os
import pty
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

from src import cli
from src.cli_input import choice_menu_supported
from src.cli_input import read_choice
from src.cli_input import _read_masked_chars
from src.cli_input import _read_choice_index


def test_masked_secret_reader_echoes_asterisks_without_secret() -> None:
    chars = iter("sk-test\x7fZ\n")
    output: list[str] = []

    value = _read_masked_chars(lambda: next(chars, ""), output.append)

    assert value == "sk-tesZ"
    assert "".join(output) == "*******\b \b*\n"
    assert "sk-test" not in "".join(output)


def test_masked_secret_reader_ctrl_u_clears_pasted_input() -> None:
    chars = iter("old-secret\x15new-secret\n")
    output: list[str] = []

    value = _read_masked_chars(lambda: next(chars, ""), output.append)

    assert value == "new-secret"
    assert "old-secret" not in "".join(output)


def test_choice_reader_selects_with_down_arrow_and_enter() -> None:
    chars = iter("\x1b[B\n")
    output: list[str] = []

    selected = _read_choice_index(
        lambda: next(chars, ""),
        output.append,
        "Codex CLI auth method",
        ("api-key", "device", "access-token"),
        0,
    )

    assert selected == 1
    assert "  > device" in "".join(output)


def test_choice_reader_selects_with_number_key() -> None:
    chars = iter("3\n")
    output: list[str] = []

    selected = _read_choice_index(
        lambda: next(chars, ""),
        output.append,
        "Codex CLI auth method",
        ("api-key", "device", "access-token"),
        0,
    )

    assert selected == 2


def test_codex_auth_method_uses_choice_menu_when_supported(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_read_choice(label: str, choices: tuple[str, ...], default_index: int) -> int:
        seen["label"] = label
        seen["choices"] = choices
        seen["default_index"] = default_index
        return 1

    monkeypatch.setattr(cli, "choice_menu_supported", lambda: True)
    monkeypatch.setattr(cli, "read_choice", fake_read_choice)

    method = cli._prompt_choice("Codex CLI auth method", cli.CODEX_AUTH_METHODS, "api-key")

    assert method == "device"
    assert seen == {
        "label": "Codex CLI auth method",
        "choices": cli.CODEX_AUTH_METHODS,
        "default_index": 0,
    }


def test_choice_menu_supported_uses_controlling_tty_when_stdio_is_not_tty(monkeypatch) -> None:
    with _temporary_pty() as (_master_fd, slave_name):
        monkeypatch.setenv("AMBER_TTY", slave_name)
        monkeypatch.setenv("TERM", "xterm-256color")
        monkeypatch.setattr("sys.stdin", _FakeStream(is_tty=False))
        monkeypatch.setattr("sys.stderr", _FakeStream(is_tty=False))

        assert choice_menu_supported()


def test_read_choice_uses_controlling_tty_when_stdio_is_not_tty(monkeypatch) -> None:
    with _temporary_pty() as (master_fd, slave_name):
        monkeypatch.setenv("AMBER_TTY", slave_name)
        monkeypatch.setattr("sys.stdin", _FakeStream(is_tty=False))
        monkeypatch.setattr("sys.stderr", _FakeStream(is_tty=False))
        feeder = threading.Thread(target=_delayed_write, args=(master_fd, b"\x1b[B\n"), daemon=True)

        feeder.start()
        selected = read_choice("Codex CLI auth method", ("api-key", "device", "access-token"), 0)
        feeder.join(timeout=1)

        assert selected == 1


class _FakeStream:
    def __init__(self, *, is_tty: bool) -> None:
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


@contextmanager
def _temporary_pty() -> Iterator[tuple[int, str]]:
    master_fd, slave_fd = pty.openpty()
    try:
        yield master_fd, os.ttyname(slave_fd)
    finally:
        for fd in (master_fd, slave_fd):
            try:
                os.close(fd)
            except OSError:
                pass


def _delayed_write(fd: int, payload: bytes) -> None:
    time.sleep(0.05)
    os.write(fd, payload)
