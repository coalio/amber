from __future__ import annotations

from pathlib import Path

from src.action.telegram.transport import RecordingTransport
from src.tools.registry import ToolRuntime, default_tool_registry


def test_send_file_maps_podman_work_path_and_sends_file(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    report_dir = workspace / "reports"
    report_dir.mkdir(parents=True)
    report = report_dir / "summary.txt"
    report.write_text("done\n", encoding="utf-8")
    transport = RecordingTransport()
    session = default_tool_registry().new_session(
        runtime=ToolRuntime(telegram_transport=transport, codex_workspace=workspace)
    )
    session.enable("SendFile")

    result = session.execute(
        "SendFile",
        {
            "chat_id": 1001001001,
            "file_path": "/work/reports/summary.txt",
            "caption": "summary",
            "reply_to_message_id": 412,
        },
    )

    assert result["sent"] is True
    assert result["workspace_relative_path"] == "reports/summary.txt"
    assert result["sent_message_id"] == transport.file_records[0].sent_message_id
    assert transport.file_records[0].chat_id == 1001001001
    assert transport.file_records[0].file_path == report.resolve()
    assert transport.file_records[0].caption == "summary"
    assert transport.file_records[0].reply_to_message_id == 412


def test_send_file_sends_relative_workspace_file(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir()
    artifact = workspace / "artifact.json"
    artifact.write_text("{}", encoding="utf-8")
    transport = RecordingTransport()
    session = default_tool_registry().new_session(
        runtime=ToolRuntime(telegram_transport=transport, codex_workspace=workspace)
    )
    session.enable("SendFile")

    result = session.execute(
        "SendFile",
        {
            "chat_id": "1001001001",
            "file_path": "artifact.json",
            "caption": None,
            "reply_to_message_id": None,
        },
    )

    assert result["sent"] is True
    assert result["workspace_relative_path"] == "artifact.json"
    assert transport.file_records[0].file_path == artifact.resolve()
    assert transport.file_records[0].caption is None


def test_send_file_rejects_path_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir()
    outside_file = tmp_path / "secret.txt"
    outside_file.write_text("secret\n", encoding="utf-8")
    transport = RecordingTransport()
    session = default_tool_registry().new_session(
        runtime=ToolRuntime(telegram_transport=transport, codex_workspace=workspace)
    )
    session.enable("SendFile")

    result = session.execute(
        "SendFile",
        {
            "chat_id": 1001001001,
            "file_path": str(outside_file),
            "caption": None,
            "reply_to_message_id": None,
        },
    )

    assert result == {"sent": False, "error": "File path is outside the configured Codex workspace."}
    assert transport.file_records == []


def test_send_file_rejects_symlink_that_escapes_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir()
    outside_file = tmp_path / "secret.txt"
    outside_file.write_text("secret\n", encoding="utf-8")
    (workspace / "linked-secret.txt").symlink_to(outside_file)
    transport = RecordingTransport()
    session = default_tool_registry().new_session(
        runtime=ToolRuntime(telegram_transport=transport, codex_workspace=workspace)
    )
    session.enable("SendFile")

    result = session.execute(
        "SendFile",
        {
            "chat_id": 1001001001,
            "file_path": "linked-secret.txt",
            "caption": None,
            "reply_to_message_id": None,
        },
    )

    assert result == {"sent": False, "error": "File path is outside the configured Codex workspace."}
    assert transport.file_records == []
