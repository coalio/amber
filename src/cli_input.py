from __future__ import annotations

import sys
import termios
import tty
from typing import Callable


def read_masked_secret(prompt: str) -> str:
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    print(prompt, end="", flush=True)
    try:
        tty.setcbreak(fd)
        return _read_masked_chars(lambda: sys.stdin.read(1), lambda text: print(text, end="", flush=True))
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


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
