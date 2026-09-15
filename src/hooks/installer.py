from __future__ import annotations

import hashlib
import os
import shlex
from collections.abc import Callable
from typing import Any

from src.hooks.config import HookConfig


SandboxRunner = Callable[[list[str]], Any]
ProgressCallback = Callable[[str], None]


class CodexHookInstaller:
    def __init__(
        self,
        config: HookConfig,
        *,
        container_name: str,
        sandbox_runner: SandboxRunner,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        self._config = config
        self._container_name = container_name
        self._sandbox_runner = sandbox_runner
        self._progress_callback = progress_callback
        self._installed = False

    @property
    def enabled(self) -> bool:
        return self._config.repository is not None

    def install(self) -> None:
        if self._installed or self._config.repository is None:
            return

        self._progress("installing configured codex hooks")
        self._sandbox_runner(
            [
                "exec",
                "--user",
                str(os.getuid()),
                "-e",
                "HOME=/codex-home",
                "-e",
                "CODEX_HOME=/codex-home/.codex",
                "-e",
                "GIT_TERMINAL_PROMPT=0",
                self._container_name,
                "bash",
                "-lc",
                self._install_script(),
            ]
        )
        self._installed = True

    def _install_script(self) -> str:
        repository = self._config.repository
        if repository is None:
            raise RuntimeError("Cannot build a hook installation command without a repository.")

        # isolate each configured source so repository changes never reuse stale checkout data
        source_id = hashlib.sha256(repository.encode("utf-8")).hexdigest()[:16]
        source_dir = f"/codex-home/.amber-hook-sources/{source_id}"
        staging_dir = f"{source_dir}.staging"
        ownership_file = "/codex-home/.amber-hook-sources/installed-repository"

        # replace only the amber-managed checkout after the requested revision resolves successfully
        return "\n".join(
            [
                "set -eu",
                f"source_dir={shlex.quote(source_dir)}",
                f"staging_dir={shlex.quote(staging_dir)}",
                f"ownership_file={shlex.quote(ownership_file)}",
                f"configured_repository={shlex.quote(repository)}",
                'mkdir -p "$(dirname -- "$source_dir")"',
                'rm -rf -- "$staging_dir"',
                (
                    "git clone --quiet --no-checkout -- "
                    f"{shlex.quote(repository)} \"$staging_dir\""
                ),
                (
                    'git -C "$staging_dir" checkout --quiet --detach '
                    f"{shlex.quote(self._config.revision)}"
                ),
                'test -f "$staging_dir/install.sh"',
                'rm -rf -- "$source_dir"',
                'mv -- "$staging_dir" "$source_dir"',
                "force_arg=",
                (
                    'if test -f "$ownership_file" '
                    '&& test "$(cat -- "$ownership_file")" = "$configured_repository"; then'
                ),
                "  force_arg=--force",
                "fi",
                'CODEX_HOME=/codex-home/.codex sh "$source_dir/install.sh" $force_arg',
                'printf "%s\\n" "$configured_repository" > "$ownership_file"',
            ]
        )

    def _progress(self, message: str) -> None:
        if self._progress_callback is not None:
            self._progress_callback(message)
