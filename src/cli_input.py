from __future__ import annotations

import os
import select
import sys
import termios
import tty
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager


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

    with _choice_terminal() as terminal:
        return _read_choice_from_terminal(prompt, choices, default_index, terminal)


def choice_menu_supported() -> bool:
    # require a terminal target that can safely receive cursor-control redraws
    if headless_enabled():
        return False
    if os.getenv("TERM") == "dumb":
        return False
    if sys.stdin.isatty() and sys.stderr.isatty():
        return True
    return _external_tty_available()


def headless_enabled() -> bool:
    return _truthy_env("AMBER_HEADLESS")


def _truthy_env(name: str) -> bool:
    value = os.getenv(name, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


ChoiceTerminal = tuple[int, Callable[[], str], Callable[[str], None], object]


@contextmanager
def _choice_terminal() -> Iterator[ChoiceTerminal]:
    # use stdio first so direct CLI runs keep normal stream behavior
    if sys.stdin.isatty() and sys.stderr.isatty():
        yield (
            sys.stdin.fileno(),
            lambda: sys.stdin.read(1),
            lambda text: _write_stream(sys.stderr, text),
            sys.stdin,
        )
        return

    # fall back to the controlling terminal for installer-nested configure flows
    encoding = getattr(sys.stdin, "encoding", None) or "utf-8"
    fd = os.open(_tty_path(), os.O_RDWR | os.O_NOCTTY)
    try:
        yield (
            fd,
            lambda: os.read(fd, 1).decode(encoding, errors="ignore"),
            lambda text: os.write(fd, text.encode(encoding, errors="replace")),
            fd,
        )
    finally:
        os.close(fd)


def _read_choice_from_terminal(
    prompt: str,
    choices: Sequence[str],
    default_index: int,
    terminal: ChoiceTerminal,
) -> int:
    fd, read_char, write_text, select_target = terminal
    old_settings = termios.tcgetattr(fd)
    try:
        # scope cbreak mode to the active menu so later secret prompts behave normally
        tty.setcbreak(fd)
        return _read_choice_index(
            read_char,
            write_text,
            prompt,
            choices,
            default_index,
            lambda timeout: bool(select.select([select_target], [], [], timeout)[0]),
        )
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _write_stream(stream: object, text: str) -> None:
    write = getattr(stream, "write")
    flush = getattr(stream, "flush")
    write(text)
    flush()


def _tty_path() -> str:
    return os.getenv("AMBER_TTY") or "/dev/tty"


def _external_tty_available() -> bool:
    try:
        fd = os.open(_tty_path(), os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return False
    try:
        return os.isatty(fd)
    finally:
        os.close(fd)


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
