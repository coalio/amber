#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config.config import get_settings
from src.events.action import OutboundMessageSentEvent
from src.events.bus import EventBus
from src.events.codex import CodexQuestionReceivedEvent
from src.runtime import build_application
from src.tools.registry import ToolRuntime, default_tool_registry


DEFAULT_TASK = (
    "Create a small Python CLI that echoes a message. There is real nuance: it may become part "
    "of a larger CLI later, so clarify whether it should stay as a standalone script or be shaped "
    "as a reusable module with a CLI entrypoint before proceeding."
)


def _configure_work_mode(*, resume_open_question: bool = False) -> None:
    os.environ["AMBER_MODE"] = "work"
    os.environ.setdefault("AMBER_ENABLE_REAL_DELAYS", "0")
    os.environ.setdefault("AMBER_DISABLE_SLEEP_STATE", "1")
    os.environ.setdefault("AMBER_CONTEXT_DEBOUNCE_SECONDS", "2")
    os.environ.setdefault("AMBER_CONTEXT_INITIAL_ENGAGEMENT_DELAY_MIN_SECONDS", "0")
    os.environ.setdefault("AMBER_CONTEXT_INITIAL_ENGAGEMENT_DELAY_MAX_SECONDS", "0")
    if "AMBER_CODEX_APP_SERVER_PORT" not in os.environ and "AMBER_CODEX_APP_SERVER_URL" not in os.environ:
        port = _existing_codex_port() if resume_open_question else None
        port = port or _free_local_port()
        os.environ["AMBER_CODEX_APP_SERVER_PORT"] = str(port)
        os.environ["AMBER_CODEX_APP_SERVER_URL"] = f"http://127.0.0.1:{port}"
    get_settings.cache_clear()


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _existing_codex_port() -> int | None:
    cgroup_manager = os.environ.get("AMBER_CODEX_CGROUP_MANAGER", "cgroupfs")
    command = ["podman"]
    if cgroup_manager:
        command.append(f"--cgroup-manager={cgroup_manager}")
    command.extend(["port", "codex-sandbox"])
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if "127.0.0.1:" not in line:
            continue
        try:
            return int(line.rsplit(":", 1)[1])
        except ValueError:
            continue
    return None


def _tool_session(app: Any):
    registry = default_tool_registry()
    session = registry.new_session(
        runtime=ToolRuntime(
            memory_store=app.memory_store,
            adapter_registry=app.adapter_registry,
            state_store=app.state_store,
        )
    )
    session.enable("CodexRunTask")
    return session


async def run_live_codex_work_smoke(
    *,
    task_description: str,
    context: dict[str, Any],
    timeout_seconds: float,
    wait_for_completion: bool,
    resume_open_question: bool = False,
) -> None:
    _configure_work_mode(resume_open_question=resume_open_question)
    settings = get_settings()
    if settings.mode != "work":
        raise RuntimeError("Live Codex smoke must run with AMBER_MODE=work.")
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        raise RuntimeError("Missing Telegram API credentials.")
    if not settings.ai_api_key:
        raise RuntimeError("Missing AI API credentials.")

    app = build_application(enable_telegram=True)
    if app.telegram_client is None or app.receiver is None:
        raise RuntimeError("Telegram runtime was not configured.")
    if app.codex_receiver is None:
        raise RuntimeError("Codex receiver was not configured.")

    loop = asyncio.get_running_loop()
    first_outbound = asyncio.Event()
    completion_outbound = asyncio.Event()
    codex_question_seen = asyncio.Event()
    counters = {"outbound": 0}

    def on_codex_question(event: CodexQuestionReceivedEvent) -> None:
        print(
            f"[live-codex] Codex question event: task_id={event.payload.task_id} "
            f"candidates={[candidate.display_name for candidate in event.payload.candidate_people]}",
            flush=True,
        )
        loop.call_soon_threadsafe(codex_question_seen.set)

    def on_outbound(event: OutboundMessageSentEvent) -> None:
        if event.payload.no_send:
            return
        counters["outbound"] += 1
        print(
            f"[live-codex] Outbound #{counters['outbound']} sent to chat={event.payload.chat_id}: "
            f"{event.payload.ordered_messages}",
            flush=True,
        )
        loop.call_soon_threadsafe(first_outbound.set)
        if counters["outbound"] >= 2:
            loop.call_soon_threadsafe(completion_outbound.set)

    EventBus.subscribe("CodexQuestionReceivedEvent", on_codex_question)
    EventBus.subscribe("OutboundMessageSentEvent", on_outbound)

    app.codex_receiver.register()
    app.receiver.register()
    await app.telegram_client.start()
    await app.receiver.replay_open_question_backlog()
    app.action_layer.sync_presence_from_state()

    started_task: dict[str, Any] | None = None
    try:
        if resume_open_question:
            open_questions = app.state_store.snapshot().open_questions
            if not open_questions:
                raise RuntimeError("No open Codex question exists to resume.")
            print(f"[live-codex] Resuming open Codex question(s): {list(open_questions)}", flush=True)
        else:
            session = _tool_session(app)
            started_task = session.execute(
                "CodexRunTask",
                {
                    "task_description": task_description,
                    "context": context,
                },
            )
            print(f"[live-codex] CodexRunTask result: {started_task}", flush=True)
            if isinstance(started_task, dict) and started_task.get("error"):
                raise RuntimeError(str(started_task["error"]))

            await asyncio.wait_for(codex_question_seen.wait(), timeout=timeout_seconds)
            await asyncio.wait_for(first_outbound.wait(), timeout=timeout_seconds)
            print("[live-codex] First Telegram clarification message was sent.", flush=True)
            if not wait_for_completion:
                return

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            state = app.state_store.snapshot()
            open_questions = state.open_questions
            if not open_questions and counters["outbound"] >= 2:
                print("[live-codex] Codex clarification completed and appreciation message was sent.", flush=True)
                return
            remaining = max(deadline - time.monotonic(), 0.0)
            try:
                await asyncio.wait_for(completion_outbound.wait(), timeout=min(5.0, remaining))
            except asyncio.TimeoutError:
                continue
        raise TimeoutError("Timed out waiting for Codex clarification completion.")
    finally:
        app.scheduler.shutdown()
        if app.telegram_client is not None:
            await app.telegram_client.disconnect()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a live work-mode Codex clarification smoke test through Telegram.")
    parser.add_argument("--task", default=DEFAULT_TASK)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument(
        "--first-message-only",
        action="store_true",
        help="Stop after Amber sends the first clarification message instead of waiting for a user answer and CodexSendReply.",
    )
    parser.add_argument(
        "--resume-open-question",
        action="store_true",
        help="Do not start a new Codex task; wait for replies to the currently persisted open question.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(
        run_live_codex_work_smoke(
            task_description=args.task,
            context={
                "source": "live_codex_work_smoke",
                "headless": True,
                "nuance_tolerance": "zero",
            },
            timeout_seconds=args.timeout,
            wait_for_completion=not args.first_message_only,
            resume_open_question=args.resume_open_question,
        )
    )


if __name__ == "__main__":
    main()
