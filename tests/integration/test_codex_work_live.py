from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.integration
def test_live_work_mode_codex_task_sends_telegram_clarification() -> None:
    if os.getenv("AMBER_BLUE_RUN_LIVE_CODEX_WORK") != "1":
        pytest.skip("Set AMBER_BLUE_RUN_LIVE_CODEX_WORK=1 to send a live work-mode Telegram Codex clarification.")
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "live_codex_work_smoke.py"),
            "--first-message-only",
            "--timeout",
            os.getenv("AMBER_BLUE_LIVE_CODEX_TIMEOUT", "600"),
        ],
        cwd=ROOT,
        check=True,
    )
