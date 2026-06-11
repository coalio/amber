from __future__ import annotations

import os
import select
import sys
import termios
import tty
from collections.abc import Callable, Sequence


def read_masked_secret(prompt: str) -> str:
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    print(prompt, end="", flush=True)
    try:
        tty.setcbreak(fd)
        return _read_masked_chars(lambda: sys.stdin.read(1), lambda text: print(text, end="", flush=True))
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def read_choice(prompt: str, choices: Sequence[str], default_index: int = 0) -> int:
    if not choices:
        raise ValueError("choices must not be empty.")
    if default_index < 0 or default_index >= len(choices):
        raise ValueError("default_index is outside choices.")

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        return _read_choice_index(
            lambda: sys.stdin.read(1),
            lambda text: print(text, end="", file=sys.stderr, flush=True),
            prompt,
            choices,
            default_index,
            lambda timeout: bool(select.select([sys.stdin], [], [], timeout)[0]),
        )
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def choice_menu_supported() -> bool:
    return sys.stdin.isatty() and sys.stderr.isatty() and os.getenv("TERM") != "dumb"


def _read_masked_chars(read_char: Callable[[], str], write: Callable[[str], None]) -> str:
    chars: list[str] = []
    while True:
        char = read_char()
        if char == "":
            raise EOFError("EOF while reading secret input.")
        if char in ("\r", "\n"):
            write("\n")
            return "".join(chars)
        if char in ("\b", "\x7f"):
            if chars:
                chars.pop()
                write("\b \b")
            continue
        if char == "\x15":
            while chars:
                chars.pop()
                write("\b \b")
            continue
        if char < " ":
            continue
        chars.append(char)
        write("*")


def _read_choice_index(
    read_char: Callable[[], str],
    write: Callable[[str], None],
    prompt: str,
    choices: Sequence[str],
    selected: int,
    char_ready: Callable[[float], bool] | None = None,
) -> int:
    # keep fixed-choice setup flows keyboard-driven without accepting invalid text
    line_count = len(choices) + 1
    _draw_choice_menu(write, prompt, choices, selected)
    while True:
        char = read_char()
        if char == "":
            raise EOFError("EOF while reading choice input.")
        if char == "\x03":
            raise KeyboardInterrupt
        if char in ("\r", "\n"):
            write("\n")
            return selected
        if char == "\x1b":
            suffix = _read_escape_suffix(read_char, char_ready)
            if suffix == "[A":
                selected = (selected + len(choices) - 1) % len(choices)
            elif suffix == "[B":
                selected = (selected + 1) % len(choices)
        elif char in ("k", "K"):
            selected = (selected + len(choices) - 1) % len(choices)
        elif char in ("j", "J"):
            selected = (selected + 1) % len(choices)
        elif char in "123456789":
            index = int(char) - 1
            if index < len(choices):
                selected = index
        else:
            continue

        write(f"\033[{line_count}A")
        _draw_choice_menu(write, prompt, choices, selected)


def _read_escape_suffix(read_char: Callable[[], str], char_ready: Callable[[float], bool] | None) -> str:
    # avoid trapping users who press escape without completing an arrow key
    if char_ready is None:
        return read_char() + read_char()
    if not char_ready(0.1):
        return ""
    first = read_char()
    if not char_ready(0.1):
        return first
    return first + read_char()


def _draw_choice_menu(
    write: Callable[[str], None],
    prompt: str,
    choices: Sequence[str],
    selected: int,
) -> None:
    write(f"\033[2K{prompt}\n")
    for index, choice in enumerate(choices):
        marker = ">" if index == selected else " "
        write(f"\033[2K  {marker} {choice}\n")
