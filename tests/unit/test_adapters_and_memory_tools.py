from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

import src.adapters.codex.adapter as codex_adapter_module
from src.adapters.codex import app_server as codex_app_server
from src.adapters.base import BaseAdapter
from src.adapters.codex import CodexAdapter, CodexNotification, CodexPullRequestEvent, CodexQuestion, CodexTaskCompleted
from src.adapters.linear import LinearAdapter
from src.adapters.registry import AdapterRegistry
from src.attention.memory.store import MemoryStore
from src.events.bus import EventBus
from src.events.codex import CodexNotificationReceivedEvent, CodexQuestionReceivedEvent
from src.receiver.codex.receiver import CodexReceiver
from src.state.models import OpenQuestionCandidate
from src.state.store import GlobalStateStore
from src.adapters.codex import CodexTaskLifecycleHandler
from src.tools.registry import ToolRuntime, default_tool_registry
from src.utils.time import utc_now


LINEAR_STATUS_TARGETS = {
    "in_progress": "In Progress",
    "under_review": "In Review",
    "completed": "Done",
}


def test_memory_tools_manage_expertise_and_read_profile(tmp_path: Path) -> None:
    memory_store = MemoryStore(tmp_path / "memories")
    session = default_tool_registry().new_session(runtime=ToolRuntime(memory_store=memory_store))
    session.enable("ManageMemory")
    session.enable("GetMemory")

    manage_result = session.execute(
        "ManageMemory",
        {
            "operation": "create_expertise",
            "sender_id": "1001001001",
            "display_name": "Fixture Owner",
            "text": "Fixture Owner manages the Amber Telegram integration.",
            "tags": ["amber", "telegram"],
            "memory_id": None,
            "expertise_tags": ["telegram-backend"],
            "project_owner_tags": ["amber"],
        },
    )
    read_result = session.execute("GetMemory", {"sender_id": "1001001001", "query": "telegram", "limit": 5})

    assert manage_result["profile"]["expertise_tags"] == ["telegram-backend"]
    assert manage_result["profile"]["project_owner_tags"] == ["amber"]
    assert read_result["profile"]["display_name"] == "Fixture Owner"
    assert read_result["profile"]["expertise_tags"] == ["telegram-backend"]
    assert read_result["memories"][0]["text"] == "Fixture Owner manages the Amber Telegram integration."


def test_codex_receiver_surfaces_allowlisted_candidates_with_expertise(tmp_path: Path) -> None:
    EventBus.reset_for_tests()
    memory_store = MemoryStore(tmp_path / "memories")
    memory_store.update_profile_tags(
        "1001001001",
        "Fixture Owner",
        expertise_tags=["python", "telegram-backend"],
        project_owner_tags=["amber"],
    )
    adapter = CodexAdapter()
    receiver = CodexReceiver(adapter, memory_store, ["1001001001"])
    seen: list[CodexQuestionReceivedEvent] = []
    seen_notifications: list[CodexNotificationReceivedEvent] = []
    EventBus.subscribe("CodexQuestionReceivedEvent", seen.append)
    EventBus.subscribe("CodexNotificationReceivedEvent", seen_notifications.append)

    receiver.register()
    adapter.emit_question_for_tests(
        CodexQuestion(
            app_server_id="codex-sandbox",
            task_id="task_1",
            tool_call_id="tool_1",
            questions=["Which constraints matter?"],
            task_description="Create a small Python script.",
            context={"repo": "amber"},
        )
    )
    adapter.emit_notification_for_tests(
        CodexNotification(
            app_server_id="codex-sandbox",
            task_id="task_1",
            notification_id="notify_1",
            notification_kind="completion",
            message="Implemented the script and all tests pass.",
            task_description="Create a small Python script.",
            context={"repo": "amber"},
        )
    )

    assert len(seen) == 1
    candidate = seen[0].payload.candidate_people[0]
    assert candidate.sender_id == "1001001001"
    assert candidate.chat_id == 1001001001
    assert candidate.display_name == "Fixture Owner"
    assert candidate.expertise_tags == ["python", "telegram-backend"]
    assert candidate.project_owner_tags == ["amber"]
    assert seen_notifications[0].payload.notification_kind == "completion"
    assert seen_notifications[0].payload.candidate_people[0].sender_id == "1001001001"


def test_codex_adapter_skips_question_completed_in_same_event_batch() -> None:
    adapter = CodexAdapter()
    seen: list[CodexQuestion] = []
    adapter.subscribe_questions(seen.append)

    def fake_get_json(path: str, *, timeout: float = 30) -> dict[str, Any]:
        adapter._poll_stop.set()
        return {
            "events": [
                {
                    "seq": 1,
                    "type": "AmberAskUserQuestion",
                    "app_server_id": "codex-sandbox",
                    "task_id": "task_1",
                    "tool_call_id": "tool_1",
                    "questions": ["What should Codex do?"],
                    "task_description": "Create a script.",
                    "context": {},
                },
                {
                    "seq": 2,
                    "type": "CodexToolOutputReceived",
                    "app_server_id": "codex-sandbox",
                    "task_id": "task_1",
                    "tool_call_id": "tool_1",
                },
            ]
        }

    adapter._get_json = fake_get_json

    adapter._poll_events()

    assert seen == []
    assert adapter._last_event_seq == 2


def test_codex_adapter_emits_user_notifications() -> None:
    adapter = CodexAdapter()
    seen: list[CodexNotification] = []
    adapter.subscribe_notifications(seen.append)

    def fake_get_json(path: str, *, timeout: float = 30) -> dict[str, Any]:
        adapter._poll_stop.set()
        return {
            "events": [
                {
                    "seq": 1,
                    "type": "AmberNotifyUser",
                    "app_server_id": "codex-sandbox",
                    "task_id": "task_1",
                    "notification_id": "notify_1",
                    "notification_kind": "milestone",
                    "message": "The pull request is open.",
                    "task_description": "Implement the task.",
                    "context": {"pr": 12},
                }
            ]
        }

    adapter._get_json = fake_get_json

    adapter._poll_events()

    assert seen == [
        CodexNotification(
            app_server_id="codex-sandbox",
            task_id="task_1",
            notification_id="notify_1",
            notification_kind="milestone",
            message="The pull request is open.",
            task_description="Implement the task.",
            context={"pr": 12},
        )
    ]


def test_codex_adapter_emits_task_completion_events() -> None:
    adapter = CodexAdapter()
    seen: list[CodexTaskCompleted] = []
    adapter.subscribe_task_completed(seen.append)

    def fake_get_json(path: str, *, timeout: float = 30) -> dict[str, Any]:
        adapter._poll_stop.set()
        return {
            "events": [
                {
                    "seq": 1,
                    "type": "CodexTaskCompleted",
                    "app_server_id": "codex-sandbox",
                    "task_id": "task_1",
                    "status": "completed",
                    "task_description": "Implement LIN-1.",
                    "context": {"linear_identifier": "LIN-1"},
                }
            ]
        }

    adapter._get_json = fake_get_json

    adapter._poll_events()

    assert seen == [
        CodexTaskCompleted(
            app_server_id="codex-sandbox",
            task_id="task_1",
            status="completed",
            task_description="Implement LIN-1.",
            context={"linear_identifier": "LIN-1"},
        )
    ]


def test_codex_adapter_health_uses_runtime_signals() -> None:
    adapter = CodexAdapter()
    payload = {
        "ok": True,
        "runner": "codex-cli",
        "yolo_mode": True,
    }

    adapter._get_json = lambda path, timeout=1: payload

    assert adapter._app_server_is_healthy() is True


def test_codex_adapter_decodes_pull_request_events() -> None:
    adapter = CodexAdapter()
    seen: list[CodexPullRequestEvent] = []
    adapter.subscribe_pull_request_events(seen.append)

    def fake_get_json(path: str, timeout: int = 10) -> dict[str, Any]:
        adapter._poll_stop.set()
        return {
            "events": [
                {
                    "seq": 1,
                    "type": "AmberReportPullRequest",
                    "app_server_id": "codex-sandbox",
                    "task_id": "task_1",
                    "event_type": "opened",
                    "pr_url": "https://github.com/acme/widgets/pull/12",
                    "repository": "acme/widgets",
                    "pr_number": 12,
                    "branch": "feature/LIN-1-small-task",
                    "title": "LIN-1 small task",
                    "summary": "Opened the PR.",
                    "task_description": "Implement LIN-1.",
                    "context": {"linear_identifier": "LIN-1"},
                }
            ]
        }

    adapter._get_json = fake_get_json

    adapter._poll_events()

    assert seen == [
        CodexPullRequestEvent(
            app_server_id="codex-sandbox",
            task_id="task_1",
            event_type="opened",
            pr_url="https://github.com/acme/widgets/pull/12",
            repository="acme/widgets",
            pr_number=12,
            branch="feature/LIN-1-small-task",
            title="LIN-1 small task",
            summary="Opened the PR.",
            task_description="Implement LIN-1.",
            context={"linear_identifier": "LIN-1"},
        )
    ]


def test_codex_start_task_includes_user_interaction_tools() -> None:
    adapter = CodexAdapter()
    captured: dict[str, Any] = {}
    adapter.ensure_app_server = lambda: None
    adapter._ensure_event_polling = lambda: None

    def fake_post_json(path: str, payload: dict[str, Any]) -> dict[str, Any]:
        captured["path"] = path
        captured["payload"] = payload
        return {"app_server_id": "codex-sandbox", "task_id": "task_1", "status": "started"}

    adapter._post_json = fake_post_json

    task = adapter.start_task(task_description="Do the thing.", context={"project": "demo"})

    assert task.task_id == "task_1"
    assert captured["path"] == "/tasks"
    assert [tool["name"] for tool in captured["payload"]["tools"]] == [
        "AmberAskUserQuestion",
        "AmberNotifyUser",
        "AmberReportPullRequest",
    ]
    assert captured["payload"]["release_version"] == "development"


def test_codex_start_task_does_not_log_task_contents(caplog: pytest.LogCaptureFixture) -> None:
    adapter = CodexAdapter()
    adapter.ensure_app_server = lambda: None
    adapter._ensure_event_polling = lambda: None
    adapter._post_json = lambda path, payload: {
        "app_server_id": "codex-sandbox",
        "task_id": "task_1",
        "status": "started",
    }
    caplog.set_level(logging.INFO)

    adapter.start_task(
        task_description="Authenticate with secret-value",
        context={"access_key": "another-secret", "project": "demo"},
    )

    record = next(record for record in caplog.records if record.getMessage() == "codex.task_start_requested")
    assert record.context == {
        "task_description_chars": 30,
        "task_context_keys": ["access_key", "project"],
        "thread_id": None,
    }
    assert "secret-value" not in caplog.text
    assert "another-secret" not in caplog.text


def test_codex_notify_tool_requires_typed_milestone_kind() -> None:
    notify_tool = next(tool for tool in codex_app_server._dynamic_tools() if tool["name"] == "AmberNotifyUser")

    assert notify_tool["inputSchema"]["required"] == ["notification_kind", "message"]
    assert set(notify_tool["inputSchema"]["properties"]["notification_kind"]["enum"]) == {
        "milestone",
        "completion",
        "blocked",
        "failed",
    }


def test_codex_question_tool_allows_required_operational_input() -> None:
    question_tool = next(tool for tool in codex_app_server._dynamic_tools() if tool["name"] == "AmberAskUserQuestion")

    assert "authorization code" in question_tool["description"]
    assert "exact external URL" in question_tool["description"]
    assert any("required external value" in reason for reason in codex_app_server.CLARIFICATION_POLICY["ask_when"])


def test_codex_task_lifecycle_handler_uses_pr_events_for_linear_status(tmp_path: Path) -> None:
    EventBus.reset_for_tests()
    state_store = GlobalStateStore(tmp_path / "state.json", "UTC")
    state_store.sync_linear_queue(
        [
            {
                "issue_id": "issue-a",
                "identifier": "LIN-1",
                "title": "Small task",
                "due_date": "2026-06-01",
                "status": "Todo",
                "project": "Amber",
            }
        ],
        seen_at=utc_now(),
    )
    codex_adapter = CodexAdapter()
    linear_client = FakeLinearMutationClient()
    linear_adapter = LinearAdapter(api_key=None, client=linear_client, status_names=LINEAR_STATUS_TARGETS)
    handler = CodexTaskLifecycleHandler(
        codex_adapter,
        adapter_registry=AdapterRegistry([linear_adapter]),
        state_store=state_store,
    )
    state_store.mark_linear_task_started(
        issue_id="issue-a",
        codex_app_server_id="codex-sandbox",
        codex_task_id="task_1",
        codex_thread_id="thread_1",
        codex_turn_id="turn_1",
        started_at=utc_now(),
    )

    handler.register()
    codex_adapter.emit_task_completed_for_tests(
        CodexTaskCompleted(
            app_server_id="codex-sandbox",
            task_id="task_1",
            status="completed",
            task_description="Implement LIN-1.",
            context={},
            thread_id="thread_1",
            turn_id="turn_2",
        )
    )
    task = state_store.snapshot().linear_tasks["issue-a"]
    assert task.queue_status == "codex_running"
    assert task.codex_turn_id == "turn_2"
    assert linear_client.status_updates == []

    codex_adapter.emit_pull_request_event_for_tests(
        CodexPullRequestEvent(
            app_server_id="codex-sandbox",
            task_id="task_1",
            event_type="opened",
            pr_url="https://github.com/acme/widgets/pull/12",
            repository="acme/widgets",
            pr_number=12,
            branch="feature/LIN-1-small-task",
            title="LIN-1 small task",
            summary="Opened the implementation PR.",
        )
    )
    task = state_store.snapshot().linear_tasks["issue-a"]
    assert task.queue_status == "under_review"
    assert task.pr_url == "https://github.com/acme/widgets/pull/12"
    assert task.pr_number == 12

    wake_events = []
    EventBus.subscribe("LinearQueueWakeRequestedEvent", wake_events.append)
    codex_adapter.emit_pull_request_event_for_tests(
        CodexPullRequestEvent(
            app_server_id="codex-sandbox",
            task_id="task_1",
            event_type="merged",
            pr_url="https://github.com/acme/widgets/pull/12",
            repository="acme/widgets",
            pr_number=12,
            branch="feature/LIN-1-small-task",
            title="LIN-1 small task",
            summary="Merged the implementation PR.",
        )
    )

    assert linear_client.status_updates == [("issue-a", "In Review"), ("issue-a", "Done")]
    assert state_store.snapshot().linear_tasks["issue-a"].queue_status == "completed"
    assert wake_events[-1].payload.reason == "linear_pr_merged"


def test_codex_adapter_updates_codex_cli_once() -> None:
    calls: list[list[str]] = []

    def command_runner(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    adapter = CodexAdapter(command_runner=command_runner, auto_update=True)

    adapter._ensure_codex_updated()
    adapter._ensure_codex_updated()

    update_calls = [call for call in calls if call[-2:] == ["-lc", 'mkdir -p "$CODEX_HOME" "$npm_config_cache" && codex update']]
    assert len(update_calls) == 1
    assert update_calls[0][:4] == ["podman", "exec", "--user", "root"]


def test_codex_container_omits_resource_flags_when_limits_are_disabled(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def command_runner(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[-3:] == ["container", "exists", "codex-sandbox"]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    adapter = CodexAdapter(
        workdir=tmp_path / "work",
        github_auth_dir=tmp_path / "github-auth",
        codex_home_dir=tmp_path / "codex-home",
        enforce_resource_limits=False,
        command_runner=command_runner,
    )

    adapter._ensure_container()

    run_call = next(call for call in calls if call[:3] == ["podman", "run", "-d"])
    assert run_call[run_call.index("--user") + 1] == str(os.getuid())
    assert run_call[-3:] == ["bash", "-c", "while true; do sleep 3600; done"]
    assert "--cgroups=disabled" not in run_call
    assert "--memory=4g" not in run_call
    assert "--cpus=2" not in run_call
    assert "--pids-limit=512" not in run_call


def test_codex_adapter_recreates_container_when_port_forward_is_stale() -> None:
    calls: list[list[str]] = []
    adapter = CodexAdapter(container_name="codex-sandbox")
    adapter._app_server_is_healthy = lambda: False
    adapter._container_app_server_is_healthy = lambda: True
    adapter._run = lambda args: calls.append(args) or subprocess.CompletedProcess(args, 0, stdout="", stderr="")
    adapter._ensure_container = lambda: calls.append(["ensure-container"])

    adapter._recreate_container_if_port_forward_is_stale()

    assert calls == [
        ["rm", "-f", "codex-sandbox"],
        ["ensure-container"],
    ]


def test_codex_adapter_checks_container_health_with_app_server_script() -> None:
    calls: list[list[str]] = []

    def command_runner(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    adapter = CodexAdapter(app_server_port=9876, container_name="codex-sandbox", command_runner=command_runner)

    assert adapter._container_app_server_is_healthy() is True
    assert calls == [
        [
            "podman",
            "exec",
            "codex-sandbox",
            "python3",
            "/work/.amber_codex_app_server.py",
            "--health-check",
            "--host",
            "127.0.0.1",
            "--port",
            "9876",
        ]
    ]


def test_codex_dependency_bootstrap_omits_cgroup_mode_when_limits_are_disabled(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def command_runner(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[-3:] == ["image", "exists", "amber-codex-sandbox:ubuntu-24.04-codex-cli"]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    adapter = CodexAdapter(workdir=tmp_path / "work", enforce_resource_limits=False, command_runner=command_runner)

    adapter._ensure_dependency_image()

    run_call = next(call for call in calls if call[:3] == ["podman", "run", "-d"])
    assert "--cgroups=disabled" not in run_call
    assert "--memory=4g" not in run_call
    assert "--cpus=2" not in run_call
    assert "--pids-limit=512" not in run_call


def test_codex_adapter_finds_packaged_app_server_resource(monkeypatch, tmp_path: Path) -> None:
    module_dir = tmp_path / "_internal" / "src" / "adapters" / "codex"
    module_dir.mkdir(parents=True)
    release_dir = tmp_path / "release"
    packaged_script = release_dir / "resources" / "codex" / "app_server.py"
    packaged_script.parent.mkdir(parents=True)
    packaged_script.write_text("print('app server')\n", encoding="utf-8")

    monkeypatch.setattr(codex_adapter_module, "__file__", str(module_dir / "adapter.py"))
    monkeypatch.setattr(codex_adapter_module.sys, "executable", str(release_dir / "amber"))

    adapter = CodexAdapter()

    assert adapter._app_server_script_source() == packaged_script


def test_codex_adapter_installs_each_configured_skill(tmp_path: Path) -> None:
    source_root = tmp_path / "source-skills"
    skill_paths: list[Path] = []
    for name in ("codex-development", "codex-pr-reviews", "python-style-rules"):
        skill_path = source_root / name / "SKILL.md"
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text(f"---\nname: {name}\ndescription: test\n---\n", encoding="utf-8")
        skill_paths.append(skill_path)

    adapter = CodexAdapter(
        workdir=tmp_path / "work",
        codex_home_dir=tmp_path / "codex-home",
        skill_paths=tuple(skill_paths),
    )
    adapter._install_codex_skills()

    for name in ("codex-development", "codex-pr-reviews", "python-style-rules"):
        assert (tmp_path / "work" / ".amber_codex_skills" / name / "SKILL.md").exists()
        assert (tmp_path / "codex-home" / ".codex" / "skills" / name / "SKILL.md").exists()


def test_codex_skills_activate_for_their_task_scope() -> None:
    adapter = CodexAdapter()

    assert adapter._required_codex_skill_names("Explain this repository without making changes.", {}) == set()
    assert adapter._required_codex_skill_names("Implement a small Python parser.", {}) == {
        "codex-development",
        "python-style-rules",
    }
    assert adapter._required_codex_skill_names("Fix the PR review comments.", {}) == {
        "codex-development",
        "codex-pr-reviews",
    }
    assert adapter._required_codex_skill_names("Build the Python thing.", {"requires_code_editing": False}) == {
        "python-style-rules",
    }


class FakeCodexAdapter(BaseAdapter):
    name = "codex"

    def __init__(self) -> None:
        self.outputs: list[dict[str, Any]] = []

    def submit_tool_output(
        self,
        *,
        app_server_id: str,
        task_id: str,
        tool_call_id: str,
        output: dict[str, Any],
    ) -> dict[str, Any]:
        self.outputs.append(
            {
                "app_server_id": app_server_id,
                "task_id": task_id,
                "tool_call_id": tool_call_id,
                "output": output,
            }
        )
        return {"status": "accepted"}


class FakeLinearMutationClient:
    def __init__(self) -> None:
        self.status_updates: list[tuple[str, str]] = []

    def update_issue_status(self, *, issue_id: str, status_name: str) -> dict[str, Any]:
        self.status_updates.append((issue_id, status_name))
        return {
            "success": True,
            "issue": {
                "id": issue_id,
                "identifier": "LIN-1",
                "url": "https://linear.app/test/issue/LIN-1",
                "state": {"name": status_name},
            },
        }


def test_codex_send_reply_submits_output_and_clears_open_question(tmp_path: Path) -> None:
    state_store = GlobalStateStore(tmp_path / "state.json", "UTC")
    now = utc_now()
    state_store.remember_open_question(
        chat_id="1001001001",
        sender_id="1001001001",
        sender_name="Fixture Owner",
        app_server_id="codex-sandbox",
        task_id="task_1",
        tool_call_id="tool_1",
        questions=["Which constraints matter?"],
        task_description="Create a small Python script.",
        context={},
        candidate_people=[
            OpenQuestionCandidate(
                sender_id="1001001001",
                chat_id="1001001001",
                display_name="Fixture Owner",
                expertise_tags=["python"],
                project_owner_tags=[],
            )
        ],
        created_at=now,
    )
    adapter = FakeCodexAdapter()
    session = default_tool_registry().new_session(
        runtime=ToolRuntime(adapter_registry=AdapterRegistry([adapter]), state_store=state_store)
    )
    session.enable("CodexSendReply")

    result = session.execute(
        "CodexSendReply",
        {
            "app_server_id": "codex-sandbox",
            "task_id": "task_1",
            "tool_call_id": "tool_1",
            "answers": ["Use argparse and print the supplied message."],
            "summary": "Build a tiny argparse echo script.",
            "confidence": 0.9,
        },
    )

    assert result["submitted"] is True
    assert result["cleared_open_question"] is True
    assert state_store.snapshot().open_questions == {}
    assert adapter.outputs[0]["output"]["summary"] == "Build a tiny argparse echo script."


def test_codex_app_server_does_not_replay_completed_question_events() -> None:
    codex_app_server.TASKS.clear()
    codex_app_server.EVENTS.clear()
    codex_app_server.TASKS["task_1"] = {"status": "waiting_for_clarification"}
    codex_app_server._append_event(
        {
            "type": "AmberAskUserQuestion",
            "task_id": "task_1",
            "tool_call_id": "tool_1",
        }
    )

    assert [event["type"] for event in codex_app_server._events_after(0)] == ["AmberAskUserQuestion"]

    codex_app_server.TASKS["task_1"]["status"] = "clarification_received"
    codex_app_server._append_event(
        {
            "type": "CodexToolOutputReceived",
            "task_id": "task_1",
            "tool_call_id": "tool_1",
        }
    )

    assert [event["type"] for event in codex_app_server._events_after(0)] == ["CodexToolOutputReceived"]


def test_codex_app_server_clarification_policy_discourages_trivial_questions() -> None:
    policy = codex_app_server.CLARIFICATION_POLICY

    assert "filenames" in policy["do_not_ask_for"]
    assert "minor output formatting" in policy["do_not_ask_for"]
    assert "Ask one meaningful question at a time" in policy["style"]


def test_codex_app_server_notify_user_event() -> None:
    codex_app_server.TASKS.clear()
    codex_app_server.EVENTS.clear()
    codex_app_server.RUNNERS.clear()
    codex_app_server.TASKS["task_1"] = {
        "status": "waiting_for_clarification",
        "task_description": "Open a pull request.",
        "notifications": [],
    }
    original_next_id = codex_app_server._next_id
    notification_id = "amber_notify_test"
    codex_app_server._next_id = lambda prefix: notification_id
    runner = codex_app_server.CodexTaskRunner(
        "task_1",
        {"task_description": "Open a pull request.", "context": {}},
    )
    codex_app_server.RUNNERS["task_1"] = runner

    class FakeHandler:
        status: int | None = None
        body = b""
        headers: list[tuple[str, str]] = []

        def send_response(self, status: int) -> None:
            self.status = status

        def send_header(self, key: str, value: str) -> None:
            self.headers.append((key, value))

        def end_headers(self) -> None:
            return

        @property
        def wfile(self):
            class Writer:
                def __init__(self, outer):
                    self._outer = outer

                def write(self, body: bytes) -> None:
                    self._outer.body = body

            return Writer(self)

    handler = FakeHandler()

    try:
        codex_app_server.Handler._receive_notification(
            handler,
            "task_1",
            {
                "notification_kind": "milestone",
                "message": "The pull request is open.",
                "context": {"pr": 12},
            },
        )
    finally:
        codex_app_server._next_id = original_next_id
        codex_app_server.RUNNERS.clear()

    assert handler.status == 200
    assert codex_app_server.EVENTS[-1]["type"] == "AmberNotifyUser"
    assert codex_app_server.EVENTS[-1]["notification_kind"] == "milestone"
    assert codex_app_server.EVENTS[-1]["message"] == "The pull request is open."


def test_codex_app_server_completion_forwards_captured_assistant_text() -> None:
    codex_app_server.TASKS.clear()
    codex_app_server.EVENTS.clear()
    task_id = "task_1"
    codex_app_server.TASKS[task_id] = {
        "status": "running",
        "task_description": "Create a script.",
        "context": {},
        "notifications": [],
    }
    runner = codex_app_server.CodexTaskRunner(
        task_id,
        {"task_description": "Create a script.", "context": {}},
    )

    runner._on_notification(
        {
            "method": "item/completed",
            "params": {
                "item": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Created /work/echo.py and tests pass."}],
                }
            },
        }
    )
    runner._on_notification({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})

    assert [event["type"] for event in codex_app_server.EVENTS] == ["AmberNotifyUser", "CodexTaskCompleted"]
    notification = codex_app_server.EVENTS[0]
    assert notification["notification_kind"] == "completion"
    assert notification["message"] == "Created /work/echo.py and tests pass."
    assert notification["context"]["guardrail"] == "terminal_user_facing_event"
    assert notification["context"]["source"] == "captured_assistant_message"


def test_codex_app_server_completion_without_terminal_tool_emits_guard_notification() -> None:
    codex_app_server.TASKS.clear()
    codex_app_server.EVENTS.clear()
    task_id = "task_1"
    codex_app_server.TASKS[task_id] = {
        "status": "running",
        "task_description": "Implement a feature.",
        "context": {},
        "notifications": [],
    }
    runner = codex_app_server.CodexTaskRunner(
        task_id,
        {"task_description": "Implement a feature.", "context": {}},
    )

    runner._on_notification({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})

    assert [event["type"] for event in codex_app_server.EVENTS] == ["AmberNotifyUser", "CodexTaskCompleted"]
    notification = codex_app_server.EVENTS[0]
    assert notification["notification_kind"] == "completion"
    assert notification["message"] == "The task completed without a detailed result to report."
    assert notification["context"]["source"] == "missing_terminal_tool"


def test_codex_app_server_completion_after_explicit_notify_does_not_duplicate_notification() -> None:
    codex_app_server.TASKS.clear()
    codex_app_server.EVENTS.clear()
    task_id = "task_1"
    codex_app_server.TASKS[task_id] = {
        "status": "running",
        "task_description": "Open a pull request.",
        "context": {},
        "notifications": [],
    }
    runner = codex_app_server.CodexTaskRunner(
        task_id,
        {"task_description": "Open a pull request.", "context": {}},
    )

    runner._notify_user(
        7,
        {
            "notification_kind": "completion",
            "message": "Implemented the change and all tests pass.",
        },
    )
    runner._on_notification({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})

    assert [event["type"] for event in codex_app_server.EVENTS] == ["AmberNotifyUser", "CodexTaskCompleted"]
    assert codex_app_server.EVENTS[0]["message"] == "Implemented the change and all tests pass."
    assert "guardrail" not in codex_app_server.EVENTS[0]["context"]


def test_codex_app_server_milestone_still_requires_one_completion_notification() -> None:
    codex_app_server.TASKS.clear()
    codex_app_server.EVENTS.clear()
    task_id = "task_1"
    codex_app_server.TASKS[task_id] = {
        "status": "running",
        "task_description": "Implement a parser.",
        "context": {},
        "notifications": [],
    }
    runner = codex_app_server.CodexTaskRunner(
        task_id,
        {"task_description": "Implement a parser.", "context": {}},
    )

    runner._notify_user(
        7,
        {
            "notification_kind": "milestone",
            "message": "The parser implementation is ready for validation.",
        },
    )
    runner._on_notification(
        {
            "method": "item/completed",
            "params": {
                "item": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Implemented the parser and 12 tests pass."}],
                }
            },
        }
    )
    runner._on_notification({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})

    notifications = [event for event in codex_app_server.EVENTS if event["type"] == "AmberNotifyUser"]
    assert [event["notification_kind"] for event in notifications] == ["milestone", "completion"]
    assert notifications[-1]["message"] == "Implemented the parser and 12 tests pass."


def test_codex_app_server_completion_suppresses_assistant_text_after_terminal_notify() -> None:
    codex_app_server.TASKS.clear()
    codex_app_server.EVENTS.clear()
    task_id = "task_1"
    codex_app_server.TASKS[task_id] = {
        "status": "running",
        "task_description": "Open a pull request.",
        "context": {},
        "notifications": [],
    }
    runner = codex_app_server.CodexTaskRunner(
        task_id,
        {"task_description": "Open a pull request.", "context": {}},
    )

    runner._notify_user(
        7,
        {
            "notification_kind": "completion",
            "message": "Implemented the change and all tests pass.",
        },
    )
    runner._report_pull_request(
        8,
        {
            "event_type": "opened",
            "pr_url": "https://github.com/acme/widgets/pull/12",
            "repository": "acme/widgets",
        },
    )
    runner._on_notification(
        {
            "method": "item/completed",
            "params": {
                "item": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Additional final detail."}],
                }
            },
        }
    )
    runner._on_notification({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})

    assert [event["type"] for event in codex_app_server.EVENTS] == [
        "AmberNotifyUser",
        "AmberReportPullRequest",
        "CodexTaskCompleted",
    ]
    assert codex_app_server.EVENTS[0]["message"] == "Implemented the change and all tests pass."


def test_codex_app_server_dynamic_tool_emits_question_and_receives_output() -> None:
    codex_app_server.TASKS.clear()
    codex_app_server.EVENTS.clear()
    task_id = "task_1"
    codex_app_server.TASKS[task_id] = {
        "status": "running",
        "task_description": "Implement a parser.",
        "tool_outputs": [],
        "notifications": [],
    }

    class FakeClient:
        responses: list[dict[str, Any]]

        def __init__(self) -> None:
            self.responses = []

        def respond(self, request_id: int, result: dict[str, Any] | None = None, error: dict[str, Any] | None = None) -> None:
            self.responses.append({"request_id": request_id, "result": result, "error": error})

    runner = codex_app_server.CodexTaskRunner(
        task_id,
        {"task_description": "Implement a parser.", "context": {"project": "demo"}},
    )
    fake_client = FakeClient()
    runner.client = fake_client

    runner._handle_dynamic_tool_call(
        7,
        {
            "tool": "AmberAskUserQuestion",
            "itemId": "tool_1",
            "arguments": {"questions": ["Which grammar should this parser support?"]},
        },
    )

    assert codex_app_server.TASKS[task_id]["status"] == "waiting_for_clarification"
    assert codex_app_server.EVENTS[-1]["type"] == "AmberAskUserQuestion"
    assert codex_app_server.EVENTS[-1]["tool_call_id"] == "tool_1"
    assert codex_app_server.EVENTS[-1]["questions"] == ["Which grammar should this parser support?"]

    submitted = runner.submit_tool_output(
        "tool_1",
        {
            "answers": ["Only arithmetic expressions."],
            "summary": "Use arithmetic expressions.",
            "confidence": 1,
        },
    )

    assert submitted is True
    assert fake_client.responses[0]["request_id"] == 7
    assert fake_client.responses[0]["result"]["success"] is True
    assert "Only arithmetic expressions" in fake_client.responses[0]["result"]["contentItems"][0]["text"]
    assert codex_app_server.EVENTS[-1]["type"] == "CodexToolOutputReceived"

    runner._on_notification(
        {
            "method": "item/completed",
            "params": {
                "item": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Implemented arithmetic parsing and tests pass."}],
                }
            },
        }
    )
    runner._on_notification({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})

    assert [event["type"] for event in codex_app_server.EVENTS] == [
        "AmberAskUserQuestion",
        "CodexToolOutputReceived",
        "AmberNotifyUser",
        "CodexTaskCompleted",
    ]
    assert codex_app_server.EVENTS[-2]["notification_kind"] == "completion"


def test_codex_app_server_rejects_untyped_notification() -> None:
    codex_app_server.TASKS.clear()
    codex_app_server.EVENTS.clear()
    task_id = "task_1"
    codex_app_server.TASKS[task_id] = {
        "status": "running",
        "task_description": "Implement a parser.",
        "notifications": [],
    }

    class FakeClient:
        def __init__(self) -> None:
            self.responses: list[dict[str, Any]] = []

        def respond(self, request_id: int, result: dict[str, Any] | None = None, error: dict[str, Any] | None = None) -> None:
            self.responses.append({"request_id": request_id, "result": result, "error": error})

    runner = codex_app_server.CodexTaskRunner(task_id, {"task_description": "Implement a parser."})
    fake_client = FakeClient()
    runner.client = fake_client

    runner._notify_user(7, {"message": "Parser work is done."})

    assert codex_app_server.EVENTS == []
    assert fake_client.responses[0]["result"]["success"] is False
    assert "notification_kind" in fake_client.responses[0]["result"]["contentItems"][0]["text"]


def test_codex_app_server_dynamic_tool_reports_pull_request_event() -> None:
    codex_app_server.TASKS.clear()
    codex_app_server.EVENTS.clear()
    task_id = "task_1"
    codex_app_server.TASKS[task_id] = {
        "status": "running",
        "task_description": "Implement LIN-1.",
        "context": {"linear_identifier": "LIN-1"},
        "tool_outputs": [],
        "notifications": [],
    }

    class FakeClient:
        def __init__(self) -> None:
            self.responses: list[dict[str, Any]] = []

        def respond(self, request_id: int, result: dict[str, Any] | None = None, error: dict[str, Any] | None = None) -> None:
            self.responses.append({"request_id": request_id, "result": result, "error": error})

    runner = codex_app_server.CodexTaskRunner(
        task_id,
        {"task_description": "Implement LIN-1.", "context": {"linear_identifier": "LIN-1"}},
    )
    fake_client = FakeClient()
    runner.client = fake_client

    runner._handle_dynamic_tool_call(
        9,
        {
            "tool": "AmberReportPullRequest",
            "itemId": "tool_pr",
            "arguments": {
                "event_type": "opened",
                "pr_url": "https://github.com/acme/widgets/pull/12",
                "repository": "acme/widgets",
                "pr_number": 12,
                "branch": "feature/LIN-1-small-task",
                "title": "LIN-1 small task",
                "summary": "Opened the implementation PR.",
            },
        },
    )

    assert fake_client.responses[0]["request_id"] == 9
    assert fake_client.responses[0]["result"]["success"] is True
    assert codex_app_server.EVENTS[-1]["type"] == "AmberReportPullRequest"
    assert codex_app_server.EVENTS[-1]["event_type"] == "opened"
    assert codex_app_server.EVENTS[-1]["pr_url"] == "https://github.com/acme/widgets/pull/12"
    assert codex_app_server.TASKS[task_id]["pr_status"] == "opened"


def test_codex_app_server_turn_input_injects_each_requested_skill() -> None:
    editing_runner = codex_app_server.CodexTaskRunner(
        "task_edit",
        {
            "task_description": "Implement a feature.",
            "context": {},
            "codex_skills": [
                {
                    "name": "codex-development",
                    "path": "/codex-home/.codex/skills/codex-development/SKILL.md",
                    "use_for_task": True,
                },
                {
                    "name": "python-style-rules",
                    "path": "/codex-home/.codex/skills/python-style-rules/SKILL.md",
                    "use_for_task": True,
                },
            ],
        },
    )
    readonly_runner = codex_app_server.CodexTaskRunner(
        "task_read",
        {
            "task_description": "Explain the repo.",
            "context": {},
            "codex_skills": [
                {
                    "name": "codex-development",
                    "path": "/codex-home/.codex/skills/codex-development/SKILL.md",
                    "use_for_task": False,
                }
            ],
        },
    )

    editing_input = editing_runner._turn_input()
    readonly_input = readonly_runner._turn_input()

    assert editing_input[0]["text"].startswith("$codex-development $python-style-rules ")
    assert editing_input[1] == {
        "type": "skill",
        "name": "codex-development",
        "path": "/codex-home/.codex/skills/codex-development/SKILL.md",
    }
    assert editing_input[2] == {
        "type": "skill",
        "name": "python-style-rules",
        "path": "/codex-home/.codex/skills/python-style-rules/SKILL.md",
    }
    assert len(readonly_input) == 1
    assert not readonly_input[0]["text"].startswith("$codex-development ")


def test_codex_app_server_starts_real_codex_in_yolo_mode() -> None:
    codex_app_server.TASKS.clear()
    codex_app_server.TASKS["task_1"] = {"status": "starting"}
    runner = codex_app_server.CodexTaskRunner(
        "task_1",
        {"task_description": "Implement the feature.", "context": {}},
    )
    captured_requests: list[tuple[str, dict[str, Any]]] = []

    class FakeClient:
        def request(self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 120) -> dict[str, Any]:
            captured_requests.append((method, params or {}))
            if method == "thread/start":
                return {"thread": {"id": "thread_1"}}
            if method == "turn/start":
                return {"turn": {"id": "turn_1"}}
            return {}

    runner.client = FakeClient()

    assert runner._codex_command() == [
        "codex",
        "--dangerously-bypass-approvals-and-sandbox",
        "app-server",
    ]

    runner._start_thread()
    runner._start_turn()

    thread_params = captured_requests[0][1]
    turn_params = captured_requests[1][1]
    assert thread_params["approvalPolicy"] == "never"
    assert thread_params["sandbox"] == "danger-full-access"
    assert turn_params["approvalPolicy"] == "never"
    assert turn_params["sandboxPolicy"] == {"type": "dangerFullAccess"}


def test_codex_app_server_health_check_cli(monkeypatch) -> None:
    seen: list[tuple[str, int]] = []

    def fake_health_url_is_ready(host: str, port: int) -> bool:
        seen.append((host, port))
        return True

    monkeypatch.setattr(codex_app_server, "_health_url_is_ready", fake_health_url_is_ready)
    monkeypatch.setattr(
        codex_app_server.sys,
        "argv",
        ["app_server.py", "--health-check", "--host", "0.0.0.0", "--port", "9876"],
    )

    with pytest.raises(SystemExit) as exc_info:
        codex_app_server.main()

    assert exc_info.value.code == 0
    assert seen == [("127.0.0.1", 9876)]
