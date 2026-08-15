from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any


def host_process_environment(
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    env = dict(os.environ if environment is None else environment)

    # restore the host loader path without disturbing the frozen parent process
    if os.name == "posix" and getattr(sys, "frozen", False):
        original = env.get("LD_LIBRARY_PATH_ORIG")
        if original is None:
            env.pop("LD_LIBRARY_PATH", None)
        else:
            env["LD_LIBRARY_PATH"] = original
    return env


def run_host_command(
    command: Sequence[str],
    *,
    runner: Callable[..., Any] = subprocess.run,
    env: Mapping[str, str] | None = None,
    **kwargs: Any,
) -> Any:
    return runner(command, env=host_process_environment(env), **kwargs)


def open_host_process(
    command: Sequence[str],
    *,
    opener: Callable[..., Any] = subprocess.Popen,
    env: Mapping[str, str] | None = None,
    **kwargs: Any,
) -> Any:
    return opener(command, env=host_process_environment(env), **kwargs)
