from __future__ import annotations

import json
import re
import time
from pathlib import Path
from threading import RLock
from uuid import uuid4

from src.utils.time import utc_now


SESSION_RE = re.compile(r"gateway:[a-f0-9]{32}")


class GatewayStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._lock = RLock()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)

    def _path(self, session: str) -> Path:
        if not SESSION_RE.fullmatch(session):
            raise RuntimeError("Invalid gateway session.")
        return self.directory / f"{session.removeprefix('gateway:')}.jsonl"

    def create(self, sender_id: str, display_name: str = "admin") -> str:
        session = f"gateway:{uuid4().hex}"
        self.record(session, "created", sender_id=sender_id, display_name=display_name)
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
