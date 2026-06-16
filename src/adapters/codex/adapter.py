from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib import error, parse, request

from src.adapters.base import BaseAdapter
from src.utils.ids import new_event_id
from src.utils.logging import get_logger


@dataclass(frozen=True)
class CodexQuestion:
    app_server_id: str
    task_id: str
    tool_call_id: str
    questions: list[str]
    task_description: str
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CodexNotification:
    app_server_id: str
    task_id: str
    notification_id: str
    message: str
    task_description: str
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CodexTaskCompleted:
    app_server_id: str
    task_id: str
    status: str
    task_description: str
    context: dict[str, Any] = field(default_factory=dict)
    thread_id: str | None = None
    turn_id: str | None = None


@dataclass(frozen=True)
class CodexPullRequestEvent:
    app_server_id: str
    task_id: str
    event_type: str
    pr_url: str
    repository: str
    pr_number: int | None = None
    branch: str | None = None
    title: str | None = None
    summary: str | None = None
    task_description: str = ""
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CodexTask:
    app_server_id: str
    task_id: str
    status: str
    thread_id: str | None = None
    turn_id: str | None = None


CodexQuestionHandler = Callable[[CodexQuestion], None]
CodexNotificationHandler = Callable[[CodexNotification], None]
CodexTaskCompletedHandler = Callable[[CodexTaskCompleted], None]
CodexPullRequestEventHandler = Callable[[CodexPullRequestEvent], None]


class CodexAdapter(BaseAdapter):
    name = "codex"

    def __init__(
        self,
        *,
        workdir: Path | None = None,
        app_server_url: str = "http://127.0.0.1:8765",
        app_server_port: int = 8765,
        podman_executable: str = "podman",
        cgroup_manager: str | None = None,
        enforce_resource_limits: bool = True,
        container_name: str = "codex-sandbox",
        image: str = "ubuntu:24.04",
        app_server_command: str | None = None,
        github_auth_dir: Path | None = None,
        codex_home_dir: Path | None = None,
        codex_model: str | None = "gpt-5.5",
        codex_reasoning_effort: str | None = "xhigh",
        auto_update: bool = True,
        system_prompt_path: Path | None = None,
        rules_skill_path: Path | None = None,
        command_runner=subprocess.run,
        progress_callback: Callable[[str], None] | None = None,
    ) -> None:
        self._workdir = workdir or Path.home() / "codex-sandbox" / "work"
        self._github_auth_dir = github_auth_dir or Path.home() / "codex-sandbox" / "github-auth"
        self._codex_home_dir = codex_home_dir or Path.home() / "codex-sandbox" / "codex-home"
        self._codex_model = codex_model
        self._codex_reasoning_effort = codex_reasoning_effort
        self._auto_update = auto_update
        self._system_prompt_path = (
            system_prompt_path or Path(__file__).resolve().parents[2] / "config" / "system" / "CODEX_SYSTEM.md"
        )
        self._rules_skill_path = (
            rules_skill_path
            or Path(__file__).resolve().parents[2] / "config" / "skills" / "CodexRules" / "SKILL.md"
        )
        self._app_server_url = app_server_url.rstrip("/")
        self._app_server_port = app_server_port
        self._podman_executable = podman_executable
        self._cgroup_manager = cgroup_manager
        self._enforce_resource_limits = enforce_resource_limits
        self._container_name = container_name
        self._image = image
        self._runtime_image = "amber-codex-sandbox:ubuntu-24.04-codex-cli"
        self._app_server_command = app_server_command
        self._command_runner = command_runner
        self._progress_callback = progress_callback
        self._handlers: dict[str, CodexQuestionHandler] = {}
        self._notification_handlers: dict[str, CodexNotificationHandler] = {}
        self._task_completed_handlers: dict[str, CodexTaskCompletedHandler] = {}
        self._pull_request_handlers: dict[str, CodexPullRequestEventHandler] = {}
        self._poll_thread: threading.Thread | None = None
        self._poll_stop = threading.Event()
        self._last_event_seq = 0
        self._completed_tool_call_keys: set[tuple[str, str, str]] = set()
        self._codex_update_checked = False
        self._codex_update_lock = threading.Lock()
        self._logger = get_logger("amber.adapters.codex")

    def preflight(self) -> None:
        if shutil.which(self._podman_executable) is None:
            raise RuntimeError(
                f"CodexAdapter requires rootless podman, but `{self._podman_executable}` was not found on PATH."
            )

    def subscribe_questions(self, handler: CodexQuestionHandler) -> str:
        subscription_id = new_event_id()
        self._handlers[subscription_id] = handler
        return subscription_id

    def unsubscribe(self, subscription_id: str) -> None:
        self._handlers.pop(subscription_id, None)
        self._notification_handlers.pop(subscription_id, None)
        if hasattr(self, "_task_completed_handlers"):
            self._task_completed_handlers.pop(subscription_id, None)
        if hasattr(self, "_pull_request_handlers"):
            self._pull_request_handlers.pop(subscription_id, None)

    def subscribe_notifications(self, handler: CodexNotificationHandler) -> str:
        subscription_id = new_event_id()
        self._notification_handlers[subscription_id] = handler
        return subscription_id

    def subscribe_task_completed(self, handler: CodexTaskCompletedHandler) -> str:
        if not hasattr(self, "_task_completed_handlers"):
            self._task_completed_handlers = {}
        subscription_id = new_event_id()
        self._task_completed_handlers[subscription_id] = handler
        return subscription_id

    def subscribe_pull_request_events(self, handler: CodexPullRequestEventHandler) -> str:
        if not hasattr(self, "_pull_request_handlers"):
            self._pull_request_handlers = {}
        subscription_id = new_event_id()
        self._pull_request_handlers[subscription_id] = handler
        return subscription_id

    def start_task(self, *, task_description: str, context: dict[str, Any] | None = None) -> CodexTask:
        return self._start_or_continue_task(task_description=task_description, context=context)

    def continue_task(
        self,
        *,
        thread_id: str,
        task_description: str,
        context: dict[str, Any] | None = None,
    ) -> CodexTask:
        return self._start_or_continue_task(
            task_description=task_description,
            context=context,
            thread_id=thread_id,
        )

    def _start_or_continue_task(
        self,
        *,
        task_description: str,
        context: dict[str, Any] | None = None,
        thread_id: str | None = None,
    ) -> CodexTask:
        self._logger.info(
            "codex.task_start_requested",
            extra={
                "event": "codex.task_start_requested",
                "context": {"task_description": task_description, "task_context": context or {}, "thread_id": thread_id},
            },
        )
        self.ensure_app_server()
        raw_context = context or {}
        payload = {
            "task_description": task_description,
            "context": raw_context,
            "thread_id": thread_id,
            "system_prompt": self._system_prompt(),
            "codex_model": self._codex_model,
            "codex_reasoning_effort": self._codex_reasoning_effort,
            "codex_rules_skill": {
                "name": "CodexRules",
                "path": "/codex-home/.codex/skills/CodexRules/SKILL.md",
                "use_for_task": self._requires_codex_rules(task_description, raw_context),
            },
            "mode": "headless",
            "nuance_tolerance": "zero",
            "clarification_policy": {
                "ask_only_if_material": True,
                "material_question_scope": [
                    "task objective",
                    "architecture",
                    "data model",
                    "integration boundary",
                    "user-facing behavior",
                    "safety constraint",
                    "acceptance criteria",
                ],
                "default_without_asking": [
                    "filenames",
                    "minor output formatting",
                    "obvious CLI spelling",
                    "boilerplate",
                    "small implementation details",
                ],
                "interaction_style": (
                    "Ask Amber one meaningful question at a time, with enough natural task context, "
                    "and do not provide a fixed user-facing template."
                ),
            },
            "tools": [
                {
                    "name": "AmberAskUserQuestion",
                    "description": (
                        "Ask Amber to gather clarification from the appropriate allowlisted person only when "
                        "the answer could materially change the implementation objective, architecture, "
                        "integration boundary, user-facing behavior, safety constraints, or acceptance criteria."
                    ),
                },
                {
                    "name": "AmberNotifyUser",
                    "description": (
                        "Ask Amber to notify the user about progress or completion. "
                        "This tool does not expect a response."
                    ),
                },
                {
                    "name": "AmberReportPullRequest",
                    "description": (
                        "Report pull request lifecycle events for Amber-managed Linear work. "
                        "Call with event_type=opened when the PR is opened, and event_type=merged after it is merged."
                    ),
                },
            ],
        }
        response = self._post_json("/tasks", payload)
        self._ensure_event_polling()
        task = CodexTask(
            app_server_id=str(response.get("app_server_id") or "codex"),
            task_id=str(response.get("task_id") or new_event_id()),
            status=str(response.get("status") or "started"),
            thread_id=_optional_response_str(response.get("thread_id")),
            turn_id=_optional_response_str(response.get("turn_id")),
        )
        self._logger.info(
            "codex.task_started",
            extra={
                "event": "codex.task_started",
                "context": {"app_server_id": task.app_server_id, "task_id": task.task_id, "status": task.status},
            },
        )
        return task

    def submit_tool_output(
        self,
        *,
        app_server_id: str,
        task_id: str,
        tool_call_id: str,
        output: dict[str, Any],
    ) -> dict[str, Any]:
        self.ensure_app_server()
        response = self._post_json(
            f"/tasks/{task_id}/tool-output",
            {
                "app_server_id": app_server_id,
                "tool_call_id": tool_call_id,
                "output": output,
            },
        )
        self._completed_tool_call_keys.add((app_server_id, task_id, tool_call_id))
        self._logger.info(
            "codex.tool_output_submitted",
            extra={
                "event": "codex.tool_output_submitted",
                "context": {
                    "app_server_id": app_server_id,
                    "task_id": task_id,
                    "tool_call_id": tool_call_id,
                    "response": response,
                },
            },
        )
        return response

    def emit_question_for_tests(self, question: CodexQuestion) -> None:
        self._emit_question(question)

    def emit_notification_for_tests(self, notification: CodexNotification) -> None:
        self._emit_notification(notification)

    def emit_task_completed_for_tests(self, task_completed: CodexTaskCompleted) -> None:
        self._emit_task_completed(task_completed)

    def emit_pull_request_event_for_tests(self, pull_request: CodexPullRequestEvent) -> None:
        self._emit_pull_request_event(pull_request)

    def _emit_question(self, question: CodexQuestion) -> None:
        self._logger.info(
            "codex.question_received",
            extra={
                "event": "codex.question_received",
                "context": {
                    "app_server_id": question.app_server_id,
                    "task_id": question.task_id,
                    "tool_call_id": question.tool_call_id,
                    "questions": question.questions,
                    "task_description": question.task_description,
                },
            },
        )
        for handler in list(self._handlers.values()):
            handler(question)

    def _emit_notification(self, notification: CodexNotification) -> None:
        self._logger.info(
            "codex.notification_received",
            extra={
                "event": "codex.notification_received",
                "context": {
                    "app_server_id": notification.app_server_id,
                    "task_id": notification.task_id,
                    "notification_id": notification.notification_id,
                    "message": notification.message,
                    "task_description": notification.task_description,
                },
            },
        )
        for handler in list(self._notification_handlers.values()):
            handler(notification)

    def _emit_task_completed(self, task_completed: CodexTaskCompleted) -> None:
        self._logger.info(
            "codex.task_completed",
            extra={
                "event": "codex.task_completed",
                "context": {
                    "app_server_id": task_completed.app_server_id,
                    "task_id": task_completed.task_id,
                    "status": task_completed.status,
                },
            },
        )
        for handler in list(self._task_completed_handlers.values()):
            handler(task_completed)

    def _emit_pull_request_event(self, pull_request: CodexPullRequestEvent) -> None:
        self._logger.info(
            "codex.pull_request_event",
            extra={
                "event": "codex.pull_request_event",
                "context": {
                    "app_server_id": pull_request.app_server_id,
                    "task_id": pull_request.task_id,
                    "event_type": pull_request.event_type,
                    "pr_url": pull_request.pr_url,
                    "repository": pull_request.repository,
                },
            },
        )
        for handler in list(self._pull_request_handlers.values()):
            handler(pull_request)

    def ensure_app_server(self) -> None:
        self.preflight()
        self._progress("preparing codex sandbox directories")
        self._workdir.mkdir(parents=True, exist_ok=True)
        self._github_auth_dir.mkdir(parents=True, exist_ok=True)
        self._github_auth_dir.chmod(0o700)
        self._codex_home_dir.mkdir(parents=True, exist_ok=True)
        self._codex_home_dir.chmod(0o700)
        if self._container_uses_runtime_image() and self._app_server_is_healthy():
            self._ensure_codex_updated()
            self._progress("codex app-server is already running")
            self._ensure_event_polling()
            return
        self._ensure_dependency_image()
        self._ensure_container()
        self._install_app_server_script()
        self._recreate_container_if_port_forward_is_stale()
        self._ensure_codex_updated()
        self._stop_unhealthy_app_server_process()
        self._start_app_server_process()
        self._wait_until_ready()

    def _ensure_codex_updated(self) -> None:
        if not self._auto_update or self._codex_update_checked:
            return
        with self._codex_update_lock:
            if self._codex_update_checked:
                return
            self._progress("checking for codex cli updates")
            self._run(
                [
                    "exec",
                    "--user",
                    "root",
                    "-e",
                    "HOME=/root",
                    "-e",
                    "CODEX_HOME=/tmp/codex-update",
                    "-e",
                    "npm_config_cache=/tmp/npm-cache",
                    self._container_name,
                    "bash",
                    "-lc",
                    'mkdir -p "$CODEX_HOME" "$npm_config_cache" && codex update',
                ]
            )
            self._codex_update_checked = True

    def _app_server_is_healthy(self) -> bool:
        try:
            payload = self._get_json("/health", timeout=1)
        except OSError:
            return False
        return (
            payload.get("ok") is True
            and payload.get("runner") == "codex-cli"
            and payload.get("yolo_mode") is True
        )

    def _container_uses_runtime_image(self) -> bool:
        if not self._podman_success(["container", "exists", self._container_name]):
            return False
        image_name = self._podman_output(["inspect", "-f", "{{.Config.Image}}", self._container_name]).strip()
        return self._is_runtime_image_name(image_name)

    def _ensure_container(self) -> None:
        self._progress("ensuring codex sandbox container")
        if self._podman_success(["container", "exists", self._container_name]):
            image_name = self._podman_output(["inspect", "-f", "{{.Config.Image}}", self._container_name]).strip()
            if not self._is_runtime_image_name(image_name):
                self._progress("removing codex sandbox container from older image")
                self._run(["rm", "-f", self._container_name])
            else:
                running = self._podman_output(["inspect", "-f", "{{.State.Running}}", self._container_name]).strip()
                if running != "true":
                    self._progress("removing non-running codex sandbox container")
                    self._run(["rm", "-f", self._container_name])
                else:
                    return
        if self._podman_success(["container", "exists", self._container_name]):
            running = self._podman_output(["inspect", "-f", "{{.State.Running}}", self._container_name]).strip()
            if running != "true":
                self._progress("removing non-running codex sandbox container")
                self._run(["rm", "-f", self._container_name])
            else:
                return
        args = [
            "run",
            "-d",
            "--name",
            self._container_name,
            "--userns=keep-id",
            "--user",
            str(os.getuid()),
            "--network=slirp4netns",
            "-p",
            f"127.0.0.1:{self._app_server_port}:{self._app_server_port}",
            "-e",
            "GH_CONFIG_DIR=/github-auth",
            "-e",
            "HOME=/codex-home",
            "-e",
            "CODEX_HOME=/codex-home/.codex",
            "-e",
            "GIT_TERMINAL_PROMPT=0",
        ]
        if self._enforce_resource_limits:
            args.extend(
                [
                    "--memory=4g",
                    "--cpus=2",
                    "--pids-limit=512",
                ]
            )
        args.extend(
            [
                "--cap-drop=all",
                "--security-opt=no-new-privileges",
                "-v",
                f"{self._workdir}:/work:Z",
                "-v",
                f"{self._github_auth_dir}:/github-auth:Z",
                "-v",
                f"{self._codex_home_dir}:/codex-home:Z",
                "-w",
                "/work",
                self._runtime_image,
                "bash",
                "-c",
                "while true; do sleep 3600; done",
            ]
        )
        self._progress("creating codex sandbox container")
        self._run(args)

    def _recreate_container_if_port_forward_is_stale(self) -> None:
        if self._app_server_is_healthy():
            return
        if not self._container_app_server_is_healthy():
            return
        self._progress("recreating codex sandbox container with stale port forward")
        self._run(["rm", "-f", self._container_name])
        self._ensure_container()

    def _container_app_server_is_healthy(self) -> bool:
        return self._podman_success(
            [
                "exec",
                self._container_name,
                "python3",
                "/work/.amber_codex_app_server.py",
                "--health-check",
                "--host",
                "127.0.0.1",
                "--port",
                str(self._app_server_port),
            ]
        )

    def _is_runtime_image_name(self, image_name: str) -> bool:
        return image_name == self._runtime_image or image_name == f"localhost/{self._runtime_image}"

    def _ensure_dependency_image(self) -> None:
        if self._podman_success(["image", "exists", self._runtime_image]):
            self._progress("codex dependency image is ready")
            return
        self._progress("building codex dependency image; first run can take a few minutes")
        bootstrap_name = f"{self._container_name}-bootstrap"
        self._podman_success(["rm", "-f", bootstrap_name])
        self._progress("starting dependency bootstrap container")
        args = [
            "run",
            "-d",
            "--name",
            bootstrap_name,
            "--userns=keep-id",
            "--user",
            "root",
            "--network=slirp4netns",
        ]
        args.extend(
            [
                "-v",
                f"{self._workdir}:/work:Z",
                "-w",
                "/work",
                self._image,
                "bash",
                "-lc",
                "while true; do sleep 3600; done",
            ]
        )
        self._run(args)
        try:
            self._progress("installing codex sandbox dependencies with apt")
            self._run(
                [
                    "exec",
                    "--user",
                    "root",
                    bootstrap_name,
                    "bash",
                    "-lc",
                    (
                        "apt update && "
                        "apt install -y curl git gh nodejs npm python3 python3-pip build-essential && "
                        "npm install -g @openai/codex"
                    ),
                ]
            )
            self._progress("committing codex dependency image")
            self._run(["commit", bootstrap_name, self._runtime_image])
        finally:
            self._progress("removing dependency bootstrap container")
            self._podman_success(["rm", "-f", bootstrap_name])

    def _install_app_server_script(self) -> None:
        self._progress("installing codex app-server script")
        source = self._app_server_script_source()
        shutil.copyfile(source, self._workdir / ".amber_codex_app_server.py")
        self._install_codex_rules_skill()

    def _app_server_script_source(self) -> Path:
        candidates = (
            Path(__file__).with_name("app_server.py"),
            Path(sys.executable).resolve().parent / "resources" / "codex" / "app_server.py",
        )
        for candidate in candidates:
            if candidate.exists():
                return candidate
        raise FileNotFoundError(
            "Codex app-server script is missing from this Amber release. "
            f"Looked in: {', '.join(str(candidate) for candidate in candidates)}"
        )

    def _install_codex_rules_skill(self) -> None:
        if not self._rules_skill_path.exists():
            return
        work_skill_dir = self._workdir / ".amber_codex_skills" / "CodexRules"
        home_skill_dir = self._codex_home_dir / ".codex" / "skills" / "CodexRules"
        work_skill_dir.mkdir(parents=True, exist_ok=True)
        home_skill_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._rules_skill_path, work_skill_dir / "SKILL.md")
        shutil.copyfile(self._rules_skill_path, home_skill_dir / "SKILL.md")

    def _start_app_server_process(self) -> None:
        self._progress("starting codex app-server process")
        command = self._app_server_command or (
            f"python3 /work/.amber_codex_app_server.py --host 0.0.0.0 --port {self._app_server_port}"
        )
        command = command.replace("{port}", str(self._app_server_port)).replace("{workdir}", "/work")
        pidfile = "/work/.amber_codex_app_server.pid"
        logfile = "/work/.amber_codex_app_server.log"
        self._run(
            [
                "exec",
                "--user",
                str(os.getuid()),
                "-e",
                "HOME=/codex-home",
                "-e",
                "CODEX_HOME=/codex-home/.codex",
                "-e",
                "GH_CONFIG_DIR=/github-auth",
                self._container_name,
                "bash",
                "-lc",
                (
                    f"if test -f {shlex.quote(pidfile)} && kill -0 $(cat {shlex.quote(pidfile)}) 2>/dev/null; then "
                    "exit 0; "
                    "fi; "
                    f"nohup {command} >{shlex.quote(logfile)} 2>&1 & echo $! > {shlex.quote(pidfile)}"
                ),
            ]
        )

    def _stop_unhealthy_app_server_process(self) -> None:
        if self._app_server_is_healthy():
            return
        pidfile = "/work/.amber_codex_app_server.pid"
        self._run(
            [
                "exec",
                "--user",
                str(os.getuid()),
                self._container_name,
                "bash",
                "-lc",
                (
                    f"if test -f {shlex.quote(pidfile)}; then "
                    f"kill $(cat {shlex.quote(pidfile)}) 2>/dev/null || true; "
                    f"rm -f {shlex.quote(pidfile)}; "
                    "fi"
                ),
            ]
        )

    def _wait_until_ready(self) -> None:
        self._progress("waiting for codex app-server health check")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self._app_server_is_healthy():
                return
            time.sleep(0.5)
        raise RuntimeError("Codex app-server did not become ready within 30 seconds.")

    def _progress(self, message: str) -> None:
        self._logger.info("codex.progress", extra={"event": "codex.progress", "context": {"message": message}})
        if self._progress_callback is not None:
            self._progress_callback(message)

    def _ensure_event_polling(self) -> None:
        if self._poll_thread is not None and self._poll_thread.is_alive():
            return
        self._poll_stop.clear()
        self._poll_thread = threading.Thread(target=self._poll_events, name="codex-adapter-events", daemon=True)
        self._poll_thread.start()

    def _poll_events(self) -> None:
        while not self._poll_stop.is_set():
            try:
                payload = self._get_json(f"/events?{parse.urlencode({'after': self._last_event_seq})}", timeout=10)
                events = [item for item in payload.get("events", []) if isinstance(item, dict)]
                completed_keys = {
                    self._event_key(item)
                    for item in events
                    if item.get("type") == "CodexToolOutputReceived"
                }
                self._completed_tool_call_keys.update(key for key in completed_keys if all(key))
                for item in events:
                    seq = int(item.get("seq") or 0)
                    self._last_event_seq = max(self._last_event_seq, seq)
                    if item.get("type") == "AmberAskUserQuestion":
                        if self._event_key(item) in self._completed_tool_call_keys:
                            continue
                        self._emit_question(
                            CodexQuestion(
                                app_server_id=str(item.get("app_server_id") or "codex"),
                                task_id=str(item.get("task_id") or ""),
                                tool_call_id=str(item.get("tool_call_id") or ""),
                                questions=[str(question) for question in item.get("questions", [])],
                                task_description=str(item.get("task_description") or ""),
                                context=dict(item.get("context") or {}),
                            )
                        )
                    elif item.get("type") == "AmberNotifyUser":
                        self._emit_notification(
                            CodexNotification(
                                app_server_id=str(item.get("app_server_id") or "codex"),
                                task_id=str(item.get("task_id") or ""),
                                notification_id=str(item.get("notification_id") or new_event_id()),
                                message=str(item.get("message") or ""),
                                task_description=str(item.get("task_description") or ""),
                                context=dict(item.get("context") or {}),
                            )
                        )
                    elif item.get("type") == "CodexTaskCompleted":
                        self._emit_task_completed(
                            CodexTaskCompleted(
                                app_server_id=str(item.get("app_server_id") or "codex"),
                                task_id=str(item.get("task_id") or ""),
                                status=str(item.get("status") or ""),
                                task_description=str(item.get("task_description") or ""),
                                context=dict(item.get("context") or {}),
                                thread_id=_optional_response_str(item.get("thread_id")),
                                turn_id=_optional_response_str(item.get("turn_id")),
                            )
                        )
                    elif item.get("type") == "AmberReportPullRequest":
                        self._emit_pull_request_event(
                            CodexPullRequestEvent(
                                app_server_id=str(item.get("app_server_id") or "codex"),
                                task_id=str(item.get("task_id") or ""),
                                event_type=str(item.get("event_type") or ""),
                                pr_url=str(item.get("pr_url") or ""),
                                repository=str(item.get("repository") or ""),
                                pr_number=_optional_response_int(item.get("pr_number")),
                                branch=_optional_response_str(item.get("branch")),
                                title=_optional_response_str(item.get("title")),
                                summary=_optional_response_str(item.get("summary")),
                                task_description=str(item.get("task_description") or ""),
                                context=dict(item.get("context") or {}),
                            )
                        )
            except (OSError, ValueError, TypeError):
                time.sleep(1)

    def _event_key(self, item: dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(item.get("app_server_id") or "codex"),
            str(item.get("task_id") or ""),
            str(item.get("tool_call_id") or ""),
        )

    def _system_prompt(self) -> str:
        try:
            return self._system_prompt_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return ""

    def _requires_codex_rules(self, task_description: str, context: dict[str, Any]) -> bool:
        explicit = context.get("requires_code_editing")
        if isinstance(explicit, bool):
            return explicit
        text = f"{task_description} {json.dumps(context, sort_keys=True, default=str)}".lower()
        read_only_markers = (
            "read-only",
            "read only",
            "explain",
            "summarize",
            "inspect",
            "investigate",
            "triage",
            "review without changes",
            "no code changes",
        )
        edit_markers = (
            "add ",
            "build ",
            "change ",
            "create ",
            "delete ",
            "edit ",
            "fix ",
            "implement ",
            "make ",
            "modify ",
            "patch ",
            "refactor ",
            "remove ",
            "rename ",
            "update ",
            "write ",
            "open a pr",
            "pull request",
        )
        if any(marker in text for marker in edit_markers):
            return True
        return not any(marker in text for marker in read_only_markers)

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        command = [*self._podman_command_prefix(), *args]
        result = self._command_runner(
            command,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            detail = str(result.stderr or result.stdout or "").strip()
            self._logger.error(
                "codex.podman_command_failed",
                extra={
                    "event": "codex.podman_command_failed",
                    "context": {"command": command, "returncode": result.returncode, "detail": detail},
                },
            )
            raise RuntimeError(f"Podman command failed ({result.returncode}): {' '.join(command)}\n{detail}")
        return result

    def _podman_success(self, args: list[str]) -> bool:
        result = self._command_runner(
            [*self._podman_command_prefix(), *args],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return result.returncode == 0

    def _podman_output(self, args: list[str]) -> str:
        result = self._run(args)
        return str(result.stdout or "")

    def _podman_command_prefix(self) -> list[str]:
        command = [self._podman_executable]
        if self._cgroup_manager:
            command.append(f"--cgroup-manager={self._cgroup_manager}")
        return command

    def _get_json(self, path: str, *, timeout: float = 30) -> dict[str, Any]:
        with request.urlopen(f"{self._app_server_url}{path}", timeout=timeout) as response:
            body = response.read().decode("utf-8")
        if not body.strip():
            return {}
        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            raise RuntimeError("Codex app-server returned a non-object JSON response.")
        return parsed

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self._app_server_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=30) as response:
                body = response.read().decode("utf-8")
        except error.URLError as exc:
            raise RuntimeError(f"Codex app-server request failed: {exc}") from exc
        if not body.strip():
            return {}
        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            raise RuntimeError("Codex app-server returned a non-object JSON response.")
        return parsed


def _optional_response_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_response_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
