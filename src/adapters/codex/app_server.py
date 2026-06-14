from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib import request
from urllib.parse import parse_qs, urlparse


APP_SERVER_ID = "codex-sandbox"
YOLO_MODE = True
TASKS: dict[str, dict[str, Any]] = {}
EVENTS: list[dict[str, Any]] = []
RUNNERS: dict[str, "CodexTaskRunner"] = {}
LOCK = threading.RLock()
CLARIFICATION_POLICY = {
    "ask_when": [
        "the answer could materially change the task objective",
        "the answer could materially change the architecture, data model, integration boundary, or acceptance criteria",
        "there is a real product or safety constraint ambiguity",
    ],
    "do_not_ask_for": [
        "filenames",
        "minor output formatting",
        "obvious CLI spelling",
        "boilerplate",
        "other small implementation defaults",
    ],
    "style": "Ask one meaningful question at a time. Give Amber enough task context, but do not provide a fixed user-facing template.",
}
USER_FACING_EVENT_TYPES = {"AmberAskUserQuestion", "AmberNotifyUser"}
ASSISTANT_TEXT_NOTIFICATION_METHODS = {"item/created", "item/updated", "item/completed", "turn/completed"}


def _health_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "app_server_id": APP_SERVER_ID,
        "runner": "codex-cli",
        "yolo_mode": YOLO_MODE,
    }


def _health_url_is_ready(host: str, port: int) -> bool:
    with request.urlopen(f"http://{host}:{port}/health", timeout=1) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return (
        isinstance(payload, dict)
        and payload.get("ok") is True
        and payload.get("runner") == "codex-cli"
        and payload.get("yolo_mode") is True
    )


@dataclass
class PendingToolCall:
    request_id: int
    task_id: str
    tool_call_id: str
    response_kind: str
    client: "JsonRpcClient"
    questions: list[dict[str, Any]] = field(default_factory=list)


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or "0")
    if length <= 0:
        return {}
    body = handler.rfile.read(length).decode("utf-8")
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise ValueError("Expected a JSON object.")
    return parsed


def _next_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _append_event(payload: dict[str, Any]) -> dict[str, Any]:
    event = dict(payload)
    event["seq"] = len(EVENTS) + 1
    event["created_at"] = time.time()
    EVENTS.append(event)
    return event


def _last_task_event_type(task_id: str) -> str:
    for event in reversed(EVENTS):
        if str(event.get("task_id") or "") == task_id:
            return str(event.get("type") or "")
    return ""


def _events_after(after: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for event in EVENTS:
        if int(event.get("seq") or 0) <= after:
            continue
        if event.get("type") == "AmberAskUserQuestion":
            task = TASKS.get(str(event.get("task_id") or ""))
            if task is not None and task.get("status") != "waiting_for_clarification":
                continue
        events.append(event)
    return events


def _content_response(value: Any, success: bool = True) -> dict[str, Any]:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, indent=2, sort_keys=True)
    return {
        "contentItems": [
            {
                "type": "inputText",
                "text": text,
            }
        ],
        "success": success,
    }


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dynamic_tools() -> list[dict[str, Any]]:
    return [
        {
            "namespace": "amber",
            "name": "AmberAskUserQuestion",
            "description": (
                "Ask Amber to gather clarification from the appropriate allowlisted person. "
                "Use this only when the answer can materially change the task objective, architecture, "
                "data model, integration boundary, user-facing behavior, safety constraint, or acceptance criteria."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "questions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "One or more concrete questions Amber should ask.",
                    },
                    "question": {
                        "type": "string",
                        "description": "A single question if only one clarification is needed.",
                    },
                    "task_context": {
                        "type": "string",
                        "description": "Natural-language context Amber can use to ask the question clearly.",
                    },
                    "context": {
                        "type": "object",
                        "description": "Structured context relevant to the question.",
                        "additionalProperties": True,
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "namespace": "amber",
            "name": "AmberNotifyUser",
            "description": (
                "Ask Amber to notify the user about progress or completion. This does not expect a response. "
                "If the user requested command output, script output, generated values, file paths, PR URLs, "
                "or other concrete results, include the exact result in the message. Do not merely say it was captured. "
                "Before ending a turn, use this tool for the final user-facing result unless you are using "
                "AmberAskUserQuestion to ask a material clarification."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The progress or completion note Amber should send in her own voice.",
                    },
                    "context": {
                        "type": "object",
                        "description": "Structured context for the notification.",
                        "additionalProperties": True,
                    },
                },
                "required": ["message"],
                "additionalProperties": False,
            },
        },
        {
            "namespace": "amber",
            "name": "AmberReportPullRequest",
            "description": (
                "Report pull request lifecycle events for Amber-managed Linear work. "
                "Use event_type=opened immediately after opening a PR. Use event_type=merged after the PR is merged."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "event_type": {
                        "type": "string",
                        "enum": ["opened", "merged"],
                        "description": "Pull request lifecycle event.",
                    },
                    "pr_url": {
                        "type": "string",
                        "description": "Canonical pull request URL.",
                    },
                    "repository": {
                        "type": "string",
                        "description": "Repository in owner/name form or canonical repository URL.",
                    },
                    "pr_number": {
                        "type": ["integer", "null"],
                        "description": "Pull request number if known.",
                    },
                    "branch": {
                        "type": ["string", "null"],
                        "description": "Head branch if known.",
                    },
                    "title": {
                        "type": ["string", "null"],
                        "description": "Pull request title if known.",
                    },
                    "summary": {
                        "type": ["string", "null"],
                        "description": "Short implementation or merge summary.",
                    },
                },
                "required": ["event_type", "pr_url", "repository"],
                "additionalProperties": False,
            },
        },
    ]


class JsonRpcClient:
    def __init__(
        self,
        *,
        command: list[str],
        cwd: str,
        env: dict[str, str],
        on_notification: Any,
        on_request: Any,
        on_exit: Any,
    ) -> None:
        self._command = command
        self._cwd = cwd
        self._env = env
        self._on_notification = on_notification
        self._on_request = on_request
        self._on_exit = on_exit
        self._next_id = 0
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._write_lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None

    def start(self) -> None:
        if self._process is not None:
            return
        self._process = subprocess.Popen(
            self._command,
            cwd=self._cwd,
            env=self._env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        threading.Thread(target=self._read_stdout, name="codex-jsonrpc-stdout", daemon=True).start()
        threading.Thread(target=self._read_stderr, name="codex-jsonrpc-stderr", daemon=True).start()
        threading.Thread(target=self._wait_for_exit, name="codex-jsonrpc-exit", daemon=True).start()

    def request(self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 120) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        response_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        self._pending[request_id] = response_queue
        self._send({"method": method, "id": request_id, "params": params or {}})
        try:
            message = response_queue.get(timeout=timeout)
        except queue.Empty as exc:
            self._pending.pop(request_id, None)
            raise RuntimeError(f"codex app-server request timed out: {method}") from exc
        if "error" in message:
            raise RuntimeError(json.dumps(message["error"], sort_keys=True))
        result = message.get("result") or {}
        if not isinstance(result, dict):
            raise RuntimeError(f"codex app-server returned non-object result for {method}")
        return result

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send({"method": method, "params": params or {}})

    def respond(self, request_id: int, result: dict[str, Any] | None = None, error: dict[str, Any] | None = None) -> None:
        if error is not None:
            self._send({"id": request_id, "error": error})
            return
        self._send({"id": request_id, "result": result or {}})

    def stop(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()

    def _send(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise RuntimeError("codex app-server process is not running")
        with self._write_lock:
            process.stdin.write(json.dumps(payload) + "\n")
            process.stdin.flush()

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                print(f"failed to parse codex app-server stdout: {line}", file=sys.stderr, flush=True)
                continue
            if isinstance(message, dict):
                self._dispatch(message)

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            line = line.rstrip()
            if line:
                print(f"codex app-server stderr: {line}", file=sys.stderr, flush=True)

    def _wait_for_exit(self) -> None:
        process = self._process
        if process is None:
            return
        code = process.wait()
        self._on_exit(code)

    def _dispatch(self, message: dict[str, Any]) -> None:
        if message.get("id") is not None and ("result" in message or "error" in message) and "method" not in message:
            request_id = int(message["id"])
            response_queue = self._pending.pop(request_id, None)
            if response_queue is not None:
                response_queue.put(message)
            return
        if message.get("method") and message.get("id") is not None:
            self._on_request(message)
            return
        if message.get("method"):
            self._on_notification(message)


class CodexTaskRunner:
    def __init__(self, task_id: str, payload: dict[str, Any]) -> None:
        self.task_id = task_id
        self.payload = payload
        self.client: JsonRpcClient | None = None
        self.pending_tool_calls: dict[str, PendingToolCall] = {}
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        self._last_assistant_message = ""
        self._done = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, name=f"codex-task-{self.task_id}", daemon=True).start()

    def submit_tool_output(self, tool_call_id: str, output: dict[str, Any]) -> bool:
        pending = self.pending_tool_calls.pop(tool_call_id, None)
        if pending is None:
            return False
        self._last_assistant_message = ""
        if pending.response_kind == "request_user_input":
            pending.client.respond(pending.request_id, self._user_input_response(pending.questions, output))
        else:
            pending.client.respond(pending.request_id, _content_response(output, True))
        with LOCK:
            task = TASKS.get(self.task_id)
            if task is not None:
                task["status"] = "running"
            _append_event(
                {
                    "type": "CodexToolOutputReceived",
                    "app_server_id": APP_SERVER_ID,
                    "task_id": self.task_id,
                    "tool_call_id": tool_call_id,
                }
            )
        return True

    def _run(self) -> None:
        with LOCK:
            TASKS[self.task_id]["status"] = "starting"
        try:
            self.client = JsonRpcClient(
                command=self._codex_command(),
                cwd="/work",
                env=self._env(),
                on_notification=self._on_notification,
                on_request=self._on_request,
                on_exit=self._on_exit,
            )
            self.client.start()
            self.client.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "amber",
                        "title": "Amber",
                        "version": "0.2.0",
                    },
                    "capabilities": {"experimentalApi": True},
                },
            )
            self.client.notify("initialized", {})
            self._start_thread()
            self._start_turn()
            self._done.wait(timeout=60 * 60 * 6)
        except Exception as exc:
            self._fail(str(exc))
        finally:
            if self.client is not None:
                self.client.stop()

    def _codex_command(self) -> list[str]:
        command = ["codex"]
        model = str(self.payload.get("codex_model") or "").strip()
        reasoning_effort = str(self.payload.get("codex_reasoning_effort") or "").strip()
        if model:
            command.extend(["-c", f"model={json.dumps(model)}"])
        if reasoning_effort:
            command.extend(["-c", f"model_reasoning_effort={json.dumps(reasoning_effort)}"])
        command.extend(["--dangerously-bypass-approvals-and-sandbox", "app-server"])
        return command

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["HOME"] = "/codex-home"
        env["CODEX_HOME"] = "/codex-home/.codex"
        env["GH_CONFIG_DIR"] = "/github-auth"
        env["GIT_TERMINAL_PROMPT"] = "0"
        return env

    def _start_thread(self) -> None:
        if self.client is None:
            raise RuntimeError("codex app-server client is not running")
        requested_thread_id = str(self.payload.get("thread_id") or "").strip()
        if requested_thread_id:
            self.thread_id = requested_thread_id
            with LOCK:
                TASKS[self.task_id]["thread_id"] = self.thread_id
            return
        params: dict[str, Any] = {
            "cwd": "/work",
            "serviceName": "amber",
            "developerInstructions": str(self.payload.get("system_prompt") or ""),
            "approvalPolicy": "never",
            "sandbox": "danger-full-access",
            "dynamicTools": _dynamic_tools(),
        }
        result = self.client.request("thread/start", params)
        thread = result.get("thread") if isinstance(result.get("thread"), dict) else {}
        self.thread_id = str(thread.get("id") or "")
        if not self.thread_id:
            raise RuntimeError("codex thread/start did not return a thread id")
        with LOCK:
            TASKS[self.task_id]["thread_id"] = self.thread_id

    def _start_turn(self) -> None:
        if self.client is None or not self.thread_id:
            raise RuntimeError("codex thread is not ready")
        model = str(self.payload.get("codex_model") or "").strip()
        reasoning_effort = str(self.payload.get("codex_reasoning_effort") or "").strip()
        params: dict[str, Any] = {
            "threadId": self.thread_id,
            "input": self._turn_input(),
            "cwd": "/work",
            "approvalPolicy": "never",
            "sandboxPolicy": {"type": "dangerFullAccess"},
        }
        if model:
            params["model"] = model
        if reasoning_effort:
            params["effort"] = reasoning_effort
        result = self.client.request("turn/start", params)
        turn = result.get("turn") if isinstance(result.get("turn"), dict) else {}
        self.turn_id = str(turn.get("id") or "")
        with LOCK:
            TASKS[self.task_id]["status"] = "running"
            TASKS[self.task_id]["turn_id"] = self.turn_id

    def _turn_input(self) -> list[dict[str, Any]]:
        text = self._task_prompt()
        skill = self.payload.get("codex_rules_skill") if isinstance(self.payload.get("codex_rules_skill"), dict) else {}
        if skill.get("use_for_task"):
            name = str(skill.get("name") or "CodexRules")
            path = str(skill.get("path") or "/codex-home/.codex/skills/CodexRules/SKILL.md")
            return [
                {"type": "text", "text": f"${name} {text}"},
                {"type": "skill", "name": name, "path": path},
            ]
        return [{"type": "text", "text": text}]

    def _task_prompt(self) -> str:
        context = self.payload.get("context") if isinstance(self.payload.get("context"), dict) else {}
        linear_identifier = str(context.get("linear_identifier") or context.get("linear_issue_id") or "").strip()
        linear_section = (
            "\n\n".join(
                [
                    "Linear task:",
                    (
                        f"Linear task ID: {linear_identifier}\n"
                        f"Linear issue UUID: {context.get('linear_issue_id') or ''}\n"
                        f"Linear URL: {context.get('linear_url') or ''}\n"
                        "If this task came from Linear, Amber manages its lifecycle outside Codex."
                    ),
                ]
            )
            if linear_identifier
            else ""
        )
        sections = [
            "Task:",
            str(self.payload.get("task_description") or "").strip(),
            "Context:",
            json.dumps(context, indent=2, sort_keys=True),
            "Clarification policy:",
            json.dumps(CLARIFICATION_POLICY, indent=2, sort_keys=True),
        ]
        if linear_section:
            sections.insert(2, linear_section)
        return "\n\n".join(sections)

    def _remember_assistant_message(self, method: str, params: dict[str, Any]) -> None:
        if method not in ASSISTANT_TEXT_NOTIFICATION_METHODS:
            return
        for item in self._assistant_items_from_params(params):
            text = self._assistant_text_from_item(item)
            if text:
                self._last_assistant_message = text

    def _assistant_items_from_params(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for key in ("item", "message"):
            value = params.get(key)
            if isinstance(value, dict):
                items.append(value)
        turn = params.get("turn")
        if isinstance(turn, dict):
            for key in ("message", "finalMessage", "lastMessage", "output", "response"):
                value = turn.get(key)
                if isinstance(value, dict):
                    items.append(value)
                elif isinstance(value, str) and value.strip():
                    items.append({"role": "assistant", "text": value})
            for key in ("items", "outputItems"):
                value = turn.get(key)
                if isinstance(value, list):
                    items.extend(item for item in value if isinstance(item, dict))
        return items

    def _assistant_text_from_item(self, item: dict[str, Any]) -> str:
        item_type = str(item.get("type") or "").lower()
        if any(marker in item_type for marker in ("tool", "function", "command", "approval", "reasoning")):
            return ""

        author = item.get("author") if isinstance(item.get("author"), dict) else {}
        role = str(item.get("role") or author.get("role") or item.get("authorRole") or "").lower()
        if role and role not in {"assistant", "model"}:
            return ""

        fragments: list[str] = []
        for key in ("text", "message"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                fragments.append(value.strip())
        self._append_text_content(fragments, item.get("content"))
        self._append_text_content(fragments, item.get("contentItems"))
        return "\n\n".join(dict.fromkeys(fragments))[:8000]

    def _append_text_content(self, fragments: list[str], value: Any) -> None:
        if isinstance(value, str) and value.strip():
            fragments.append(value.strip())
            return
        if not isinstance(value, list):
            return
        for part in value:
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "").lower()
            if part_type and not any(marker in part_type for marker in ("text", "message", "output")):
                continue
            for key in ("text", "content", "message"):
                text = part.get(key)
                if isinstance(text, str) and text.strip():
                    fragments.append(text.strip())

    def _on_notification(self, message: dict[str, Any]) -> None:
        method = str(message.get("method") or "")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        self._remember_assistant_message(method, params)
        if method == "turn/started":
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            self.turn_id = str(turn.get("id") or self.turn_id or "")
            with LOCK:
                TASKS[self.task_id]["status"] = "running"
                TASKS[self.task_id]["turn_id"] = self.turn_id
        elif method == "turn/completed":
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            status = str(turn.get("status") or params.get("status") or "completed")
            with LOCK:
                TASKS[self.task_id]["status"] = status
                self._append_terminal_notification_if_needed(status=status, reason="turn_completed")
                _append_event(
                    {
                        "type": "CodexTaskCompleted",
                        "app_server_id": APP_SERVER_ID,
                        "task_id": self.task_id,
                        "status": status,
                        "task_description": str(self.payload.get("task_description") or ""),
                        "context": self.payload.get("context") if isinstance(self.payload.get("context"), dict) else {},
                        "thread_id": self.thread_id,
                        "turn_id": self.turn_id,
                    }
                )
            self._done.set()
        elif method == "thread/started":
            thread = params.get("thread") if isinstance(params.get("thread"), dict) else {}
            if thread.get("id"):
                self.thread_id = str(thread["id"])

    def _append_terminal_notification_if_needed(
        self,
        *,
        status: str,
        reason: str,
        exit_code: int | None = None,
    ) -> None:
        task = TASKS.get(self.task_id)
        if task is None:
            return
        assistant_message = self._last_assistant_message.strip()
        if _last_task_event_type(self.task_id) in USER_FACING_EVENT_TYPES and not assistant_message:
            return
        notification_id = _next_id("amber_notify")
        source = "captured_assistant_message" if assistant_message else "missing_terminal_tool"
        message = assistant_message or self._terminal_guard_message(
            status=status,
            exit_code=exit_code,
        )
        context: dict[str, Any] = {
            "guardrail": "terminal_user_facing_event",
            "reason": reason,
            "source": source,
            "status": status,
        }
        if exit_code is not None:
            context["exit_code"] = exit_code
        task.setdefault("notifications", []).append(
            {
                "notification_id": notification_id,
                "message": message,
                "context": context,
            }
        )
        _append_event(
            {
                "type": "AmberNotifyUser",
                "app_server_id": APP_SERVER_ID,
                "task_id": self.task_id,
                "notification_id": notification_id,
                "message": message,
                "task_description": str(self.payload.get("task_description") or task.get("task_description") or ""),
                "context": context,
            }
        )

    def _terminal_guard_message(self, *, status: str, exit_code: int | None = None) -> str:
        if status in {"completed", "succeeded"}:
            return (
                "Codex finished without calling AmberNotifyUser or AmberAskUserQuestion. "
                "No final assistant output was available to forward."
            )
        if exit_code is not None:
            return (
                "Codex exited without calling AmberNotifyUser or AmberAskUserQuestion. "
                f"Status: {status}. Exit code: {exit_code}."
            )
        return (
            "Codex finished without calling AmberNotifyUser or AmberAskUserQuestion. "
            f"Status: {status}."
        )

    def _on_request(self, message: dict[str, Any]) -> None:
        method = str(message.get("method") or "")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        request_id = int(message.get("id") or 0)
        if method == "item/tool/call":
            self._handle_dynamic_tool_call(request_id, params)
        elif method == "item/tool/requestUserInput":
            self._handle_request_user_input(request_id, params)
        elif method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
            self._respond_to_approval(request_id, params)
        elif self.client is not None:
            self.client.respond(request_id, error={"code": -32601, "message": f"Unsupported server request: {method}"})

    def _handle_dynamic_tool_call(self, request_id: int, params: dict[str, Any]) -> None:
        tool_name = str(params.get("tool") or params.get("name") or params.get("toolName") or "")
        arguments = self._arguments(params.get("arguments"))
        bare_tool_name = tool_name.split(".")[-1]
        if bare_tool_name == "AmberAskUserQuestion":
            self._ask_user(request_id, params, arguments)
        elif bare_tool_name == "AmberNotifyUser":
            self._notify_user(request_id, arguments)
        elif bare_tool_name == "AmberReportPullRequest":
            self._report_pull_request(request_id, arguments)
        elif self.client is not None:
            self.client.respond(request_id, _content_response(f"Unknown tool: {tool_name}", False))

    def _handle_request_user_input(self, request_id: int, params: dict[str, Any]) -> None:
        questions = [item for item in params.get("questions", []) if isinstance(item, dict)]
        if not questions:
            questions = [{"id": "question", "question": str(params.get("message") or "What should I clarify?")}]
        tool_call_id = str(params.get("requestId") or params.get("itemId") or _next_id("amber_ask"))
        question_texts = [str(item.get("question") or item.get("header") or item.get("id") or "") for item in questions]
        self._register_pending_question(
            request_id=request_id,
            tool_call_id=tool_call_id,
            questions=question_texts,
            context={"request_user_input": params},
            response_kind="request_user_input",
            question_metadata=questions,
        )

    def _ask_user(self, request_id: int, params: dict[str, Any], arguments: dict[str, Any]) -> None:
        tool_call_id = str(params.get("itemId") or params.get("toolCallId") or params.get("callId") or _next_id("amber_ask"))
        questions = self._question_texts(arguments)
        context = arguments.get("context") if isinstance(arguments.get("context"), dict) else {}
        if arguments.get("task_context"):
            context = {**context, "task_context": str(arguments["task_context"])}
        self._register_pending_question(
            request_id=request_id,
            tool_call_id=tool_call_id,
            questions=questions,
            context=context,
            response_kind="dynamic_tool",
            question_metadata=[{"id": f"question_{index}", "question": question} for index, question in enumerate(questions)],
        )

    def _register_pending_question(
        self,
        *,
        request_id: int,
        tool_call_id: str,
        questions: list[str],
        context: dict[str, Any],
        response_kind: str,
        question_metadata: list[dict[str, Any]],
    ) -> None:
        if self.client is None:
            return
        self._last_assistant_message = ""
        self.pending_tool_calls[tool_call_id] = PendingToolCall(
            request_id=request_id,
            task_id=self.task_id,
            tool_call_id=tool_call_id,
            response_kind=response_kind,
            client=self.client,
            questions=question_metadata,
        )
        with LOCK:
            task = TASKS.get(self.task_id)
            if task is not None:
                task["status"] = "waiting_for_clarification"
                task.setdefault("pending_tool_calls", []).append(tool_call_id)
            _append_event(
                {
                    "type": "AmberAskUserQuestion",
                    "app_server_id": APP_SERVER_ID,
                    "task_id": self.task_id,
                    "tool_call_id": tool_call_id,
                    "questions": questions,
                    "task_description": str(self.payload.get("task_description") or ""),
                    "context": {
                        **context,
                        "clarification_policy": CLARIFICATION_POLICY,
                    },
                }
            )

    def _notify_user(self, request_id: int, arguments: dict[str, Any]) -> None:
        message = str(arguments.get("message") or "").strip()
        if not message:
            if self.client is not None:
                self.client.respond(request_id, _content_response("message is required", False))
            return
        context = arguments.get("context") if isinstance(arguments.get("context"), dict) else {}
        notification_id = _next_id("amber_notify")
        self._last_assistant_message = ""
        with LOCK:
            task = TASKS.get(self.task_id)
            if task is not None:
                task.setdefault("notifications", []).append({"notification_id": notification_id, "message": message})
            _append_event(
                {
                    "type": "AmberNotifyUser",
                    "app_server_id": APP_SERVER_ID,
                    "task_id": self.task_id,
                    "notification_id": notification_id,
                    "message": message,
                    "task_description": str(self.payload.get("task_description") or ""),
                    "context": context,
                }
            )
        if self.client is not None:
            self.client.respond(request_id, _content_response({"notified": True, "notification_id": notification_id}, True))

    def _report_pull_request(self, request_id: int, arguments: dict[str, Any]) -> None:
        event_type = str(arguments.get("event_type") or "").strip()
        pr_url = str(arguments.get("pr_url") or "").strip()
        repository = str(arguments.get("repository") or "").strip()
        if event_type not in {"opened", "merged"}:
            if self.client is not None:
                self.client.respond(request_id, _content_response("event_type must be opened or merged", False))
            return
        if not pr_url or not repository:
            if self.client is not None:
                self.client.respond(request_id, _content_response("pr_url and repository are required", False))
            return
        pr_number = _optional_int(arguments.get("pr_number"))
        branch = _optional_str(arguments.get("branch"))
        title = _optional_str(arguments.get("title"))
        summary = _optional_str(arguments.get("summary"))
        with LOCK:
            task = TASKS.get(self.task_id)
            if task is not None:
                task["pr_url"] = pr_url
                task["pr_number"] = pr_number
                task["pr_status"] = event_type
                task["pr_repository"] = repository
                task["pr_branch"] = branch
                task["pr_title"] = title
                task["pr_summary"] = summary
            _append_event(
                {
                    "type": "AmberReportPullRequest",
                    "app_server_id": APP_SERVER_ID,
                    "task_id": self.task_id,
                    "event_type": event_type,
                    "pr_url": pr_url,
                    "repository": repository,
                    "pr_number": pr_number,
                    "branch": branch,
                    "title": title,
                    "summary": summary,
                    "task_description": str(self.payload.get("task_description") or ""),
                    "context": self.payload.get("context") if isinstance(self.payload.get("context"), dict) else {},
                }
            )
        if self.client is not None:
            self.client.respond(
                request_id,
                _content_response(
                    {
                        "reported": True,
                        "event_type": event_type,
                        "pr_url": pr_url,
                        "repository": repository,
                        "pr_number": pr_number,
                    },
                    True,
                ),
            )

    def _respond_to_approval(self, request_id: int, params: dict[str, Any]) -> None:
        available = params.get("availableDecisions") if isinstance(params.get("availableDecisions"), list) else []
        decision: Any = "acceptForSession" if "acceptForSession" in available else "accept"
        if available and decision not in available:
            decision = available[0]
        if self.client is not None:
            self.client.respond(request_id, {"decision": decision})

    def _on_exit(self, code: int) -> None:
        with LOCK:
            task = TASKS.get(self.task_id)
            if task is not None and task.get("status") not in {"completed", "failed", "interrupted"}:
                status = "failed" if code else "completed"
                task["status"] = status
                task["exit_code"] = code
                self._append_terminal_notification_if_needed(status=status, reason="process_exit", exit_code=code)
                if status == "completed":
                    _append_event(
                        {
                            "type": "CodexTaskCompleted",
                            "app_server_id": APP_SERVER_ID,
                            "task_id": self.task_id,
                            "status": status,
                            "task_description": str(self.payload.get("task_description") or ""),
                            "context": self.payload.get("context") if isinstance(self.payload.get("context"), dict) else {},
                            "thread_id": self.thread_id,
                            "turn_id": self.turn_id,
                        }
                    )
        self._done.set()

    def _fail(self, message: str) -> None:
        with LOCK:
            task = TASKS.get(self.task_id)
            if task is not None:
                task["status"] = "failed"
                task["error"] = message
            _append_event(
                {
                    "type": "AmberNotifyUser",
                    "app_server_id": APP_SERVER_ID,
                    "task_id": self.task_id,
                    "notification_id": _next_id("amber_notify"),
                    "message": f"Codex task failed before completion: {message}",
                    "task_description": str(self.payload.get("task_description") or ""),
                    "context": {"error": message},
                }
            )
        self._done.set()

    def _arguments(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return {"question": raw}
            if isinstance(parsed, dict):
                return parsed
        return {}

    def _question_texts(self, arguments: dict[str, Any]) -> list[str]:
        raw_questions = arguments.get("questions")
        if isinstance(raw_questions, list):
            questions = [str(item).strip() for item in raw_questions if str(item).strip()]
            if questions:
                return questions
        for key in ("question", "message", "clarification"):
            value = str(arguments.get(key) or "").strip()
            if value:
                return [value]
        return ["What objective, architectural, or acceptance-criteria ambiguity would materially change this implementation?"]

    def _user_input_response(self, questions: list[dict[str, Any]], output: dict[str, Any]) -> dict[str, Any]:
        raw_answers = output.get("answers")
        answers = [str(item) for item in raw_answers] if isinstance(raw_answers, list) else []
        fallback = str(output.get("summary") or "")
        mapped: dict[str, Any] = {}
        for index, question in enumerate(questions):
            question_id = str(question.get("id") or f"question_{index}")
            answer = answers[index] if index < len(answers) else fallback
            mapped[question_id] = {"answers": [answer] if answer else []}
        return {"answers": mapped}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            _json_response(self, 200, _health_payload())
            return
        if parsed.path == "/events":
            query = parse_qs(parsed.query)
            after = int((query.get("after") or ["0"])[0])
            with LOCK:
                events = _events_after(after)
            _json_response(self, 200, {"events": events})
            return
        _json_response(self, 404, {"error": "not_found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = _read_json(self)
        except (json.JSONDecodeError, ValueError) as exc:
            _json_response(self, 400, {"error": str(exc)})
            return

        if parsed.path == "/tasks":
            self._create_task(payload)
            return
        if parsed.path.startswith("/tasks/") and parsed.path.endswith("/tool-output"):
            task_id = parsed.path.split("/")[2]
            self._receive_tool_output(task_id, payload)
            return
        if parsed.path.startswith("/tasks/") and parsed.path.endswith("/notify-user"):
            task_id = parsed.path.split("/")[2]
            self._receive_notification(task_id, payload)
            return
        _json_response(self, 404, {"error": "not_found"})

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _create_task(self, payload: dict[str, Any]) -> None:
        task_id = _next_id("task")
        task = {
            "task_id": task_id,
            "task_description": str(payload.get("task_description") or ""),
            "context": payload.get("context") if isinstance(payload.get("context"), dict) else {},
            "status": "queued",
            "tool_outputs": [],
            "notifications": [],
            "system_prompt": str(payload.get("system_prompt") or ""),
            "clarification_policy": CLARIFICATION_POLICY,
        }
        runner = CodexTaskRunner(task_id, payload)
        with LOCK:
            TASKS[task_id] = task
            RUNNERS[task_id] = runner
            _append_event(
                {
                    "type": "CodexTaskStarted",
                    "app_server_id": APP_SERVER_ID,
                    "task_id": task_id,
                    "task_description": task["task_description"],
                }
            )
        runner.start()
        _json_response(self, 200, self._task_start_response(task_id))

    def _task_start_response(self, task_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            with LOCK:
                task = TASKS.get(task_id) or {}
                thread_id = _optional_str(task.get("thread_id"))
                turn_id = _optional_str(task.get("turn_id"))
                status = str(task.get("status") or "started")
            if thread_id and (turn_id or status in {"failed", "completed", "interrupted"}):
                break
            time.sleep(0.05)
        with LOCK:
            task = TASKS.get(task_id) or {}
            return {
                "app_server_id": APP_SERVER_ID,
                "task_id": task_id,
                "status": str(task.get("status") or "started"),
                "thread_id": _optional_str(task.get("thread_id")),
                "turn_id": _optional_str(task.get("turn_id")),
            }

    def _receive_notification(self, task_id: str, payload: dict[str, Any]) -> None:
        message = str(payload.get("message") or "").strip()
        if not message:
            _json_response(self, 400, {"error": "message is required"})
            return
        with LOCK:
            task = TASKS.get(task_id)
            if task is None:
                _json_response(self, 404, {"error": "unknown_task"})
                return
            notification = {
                "notification_id": _next_id("amber_notify"),
                "message": message,
                "context": payload.get("context") if isinstance(payload.get("context"), dict) else {},
            }
            task["notifications"].append(notification)
            _append_event(
                {
                    "type": "AmberNotifyUser",
                    "app_server_id": APP_SERVER_ID,
                    "task_id": task_id,
                    "notification_id": notification["notification_id"],
                    "message": message,
                    "task_description": str(task.get("task_description") or ""),
                    "context": notification["context"],
                }
            )
        _json_response(
            self,
            200,
            {
                "app_server_id": APP_SERVER_ID,
                "task_id": task_id,
                "notification_id": notification["notification_id"],
                "status": "notified",
            },
        )

    def _receive_tool_output(self, task_id: str, payload: dict[str, Any]) -> None:
        tool_call_id = str(payload.get("tool_call_id") or "")
        output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
        with LOCK:
            task = TASKS.get(task_id)
            runner = RUNNERS.get(task_id)
            if task is None:
                _json_response(self, 404, {"error": "unknown_task"})
                return
            task["tool_outputs"].append(payload)
        submitted = runner.submit_tool_output(tool_call_id, output) if runner is not None else False
        if not submitted:
            _json_response(self, 404, {"error": "unknown_tool_call"})
            return
        _json_response(
            self,
            200,
            {
                "app_server_id": APP_SERVER_ID,
                "task_id": task_id,
                "status": "clarification_received",
            },
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--health-check", action="store_true")
    args = parser.parse_args()
    if args.health_check:
        host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
        raise SystemExit(0 if _health_url_is_ready(host, args.port) else 1)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
