from __future__ import annotations

import json
import re
import time
from pathlib import Path
from threading import RLock
from uuid import uuid4

from src.events.bus import EventBus
from src.utils.time import utc_now


SESSION_RE = re.compile(r"gateway:[a-f0-9]{32}")


class GatewayStore:
    def __init__(self, directory: Path, allowlisted_sender_ids, sender_names: dict[str, str] | None = None) -> None:
        self.directory = directory
        self.allowlisted_sender_ids = {str(item).removeprefix("user") for item in allowlisted_sender_ids}
        self.sender_names = sender_names or {}
        self._lock = RLock()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)

    def _path(self, session: str) -> Path:
        if not SESSION_RE.fullmatch(session):
            raise RuntimeError("Invalid gateway session.")
        return self.directory / f"{session.removeprefix('gateway:')}.jsonl"

    def create(self, sender_id: str) -> str:
        if sender_id not in self.allowlisted_sender_ids:
            raise RuntimeError("--as must identify an allowlisted Telegram administrator.")
        session = f"gateway:{uuid4().hex}"
        self.record(session, "created", sender_id=sender_id, display_name=self.sender_names.get(sender_id, "admin"))
        return session

    def read(self, session: str) -> list[dict]:
        with self._lock:
            try:
                return [json.loads(line) for line in self._path(session).read_text().splitlines()]
            except FileNotFoundError as exc:
                raise RuntimeError("Gateway session does not exist.") from exc

    def sender(self, session: str) -> str:
        return self.read(session)[0]["sender_id"]

    def record(self, session: str, event: str, **fields) -> int:
        # keep captures private and order inbound and outbound ids in one namespace
        with self._lock:
            path = self._path(session)
            message_id = time.time_ns()
            row = {"event": event, "timestamp": utc_now().isoformat(), "id": message_id, **fields}
            with path.open("a", encoding="utf-8") as handle:
                path.chmod(0o600)
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            return message_id

    def candidates(self, context: dict) -> list[dict] | None:
        session = context.get("amber_gateway_chat_id")
        if session is None:
            return None
        # synthetic tasks can only address their durable operator session
        sender = self.sender(session)
        if sender not in self.allowlisted_sender_ids:
            return []
        return [{"sender_id": sender, "display_name": self.read(session)[0]["display_name"], "chat_id": session}]

    def register(self, adapter) -> None:
        for name in ("ContextFrameReadyEvent", "SemanticDecisionMadeEvent", "OutboundMessageSentEvent"):
            EventBus.subscribe(name, self._observe)
        adapter.subscribe_task_completed(self._task_completed)

    def _observe(self, event) -> None:
        payload = event.payload
        session = str(payload.chat_id)
        if not SESSION_RE.fullmatch(session):
            return
        if event.name == "ContextFrameReadyEvent":
            self.record(session, "frame", message_ids=payload.visible_surfaced_message_ids,
                        trigger_message_id=payload.trigger_message_id)
        elif event.name == "SemanticDecisionMadeEvent":
            self.record(session, "decision", action=payload.action, task_id=payload.codex_task_id,
                        acknowledged=payload.work_acknowledged,
                        error_code=payload.codex_work_error_code, notes=payload.notes)
        else:
            self.record(session, "turn_complete", task_id=payload.codex_task_id, no_send=payload.no_send,
                        trigger_message_id=payload.trigger_message_id)

    def _task_completed(self, task) -> None:
        session = task.context.get("amber_gateway_chat_id")
        if session is not None:
            self.record(session, "task_complete", task_id=task.task_id, status=task.status)
