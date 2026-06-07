from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.tools.base import BaseTool

if TYPE_CHECKING:
    from src.tools.registry import ToolSession


class SendFile(BaseTool):
    name = "SendFile"
    description = "Send an existing file from the Codex Podman workspace to a Telegram chat."
    brief = "Send a file from the mounted Codex workspace through Telegram."
    arguments = {
        "chat_id": {
            "type": ["integer", "string"],
            "description": "Telegram chat id from the current context frame.",
        },
        "file_path": {
            "type": "string",
            "description": (
                "Existing file path inside the Codex workspace. Relative paths are resolved from the workspace; "
                "/work/... paths are mapped to the host workspace mount."
            ),
        },
        "caption": {
            "type": ["string", "null"],
            "description": "Optional Telegram caption. Use null when no caption is needed.",
        },
        "reply_to_message_id": {
            "type": ["integer", "null"],
            "description": "Optional Telegram message id to reply to. Use null for a normal chat message.",
        },
    }
    required_arguments = ("chat_id", "file_path", "caption", "reply_to_message_id")

    def run(self, arguments: dict[str, Any], session: ToolSession) -> dict[str, Any]:
        if session.runtime.telegram_transport is None:
            return {"sent": False, "error": "Telegram transport is not available."}
        if session.runtime.codex_workspace is None:
            return {"sent": False, "error": "Codex workspace is not configured."}

        chat_id = self._chat_id(arguments.get("chat_id"))
        if chat_id is None:
            return {"sent": False, "error": "chat_id is required."}

        raw_file_path = str(arguments.get("file_path") or "").strip()
        if not raw_file_path:
            return {"sent": False, "error": "file_path is required."}

        try:
            workspace_root, file_path = _workspace_file_path(raw_file_path, Path(session.runtime.codex_workspace))
        except ValueError as exc:
            return {"sent": False, "error": str(exc)}

        caption = arguments.get("caption")
        if caption is not None:
            caption = str(caption).strip() or None

        raw_reply_to = arguments.get("reply_to_message_id")
        try:
            reply_to_message_id = int(raw_reply_to) if raw_reply_to is not None else None
        except (TypeError, ValueError):
            return {"sent": False, "error": "reply_to_message_id must be an integer or null."}

        try:
            sent_message_id = session.runtime.telegram_transport.send_file(
                chat_id,
                file_path,
                caption,
                reply_to_message_id,
            )
        except Exception as exc:
            return {"sent": False, "error": f"Telegram file send failed: {exc}"}

        return {
            "sent": True,
            "chat_id": chat_id,
            "sent_message_id": sent_message_id,
            "workspace_relative_path": file_path.relative_to(workspace_root).as_posix(),
            "caption_sent": caption is not None,
            "reply_to_message_id": reply_to_message_id,
        }

    def _chat_id(self, value: Any) -> int | str | None:
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return None


def _workspace_file_path(raw_file_path: str, workspace: Path) -> tuple[Path, Path]:
    workspace_root = workspace.expanduser().resolve(strict=False)
    requested_path = Path(raw_file_path).expanduser()
    if requested_path.is_absolute() and _is_podman_work_path(requested_path):
        requested_path = workspace_root / requested_path.relative_to("/work")
    elif not requested_path.is_absolute():
        requested_path = workspace_root / requested_path

    try:
        resolved_path = requested_path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError("File does not exist in the Codex workspace.") from exc

    try:
        resolved_path.relative_to(workspace_root)
    except ValueError as exc:
        raise ValueError("File path is outside the configured Codex workspace.") from exc

    if not resolved_path.is_file():
        raise ValueError("File path must point to a regular file.")
    return workspace_root, resolved_path


def _is_podman_work_path(path: Path) -> bool:
    try:
        path.relative_to("/work")
    except ValueError:
        return False
    return True
