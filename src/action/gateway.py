from __future__ import annotations

import time

from src.events.delivery import TaskOrigin
from src.state.gateway import GatewayStore, SESSION_RE


class GatewayTransport:
    def __init__(self, delegate, store: GatewayStore, allowlisted_sender_ids=()) -> None:
        self._delegate = delegate
        self._store = store
        self._allowlisted = {str(item).removeprefix("user") for item in allowlisted_sender_ids}

    def origin_for_chat(self, chat_id) -> TaskOrigin | None:
        if self._is_gateway(chat_id):
            return TaskOrigin(delivery_route=str(chat_id))
        return None

    def candidates(self, origin: TaskOrigin) -> list[dict]:
        # resolve only a known, authorized capture; never fall back to telegram
        session = origin.delivery_route
        if not SESSION_RE.fullmatch(session):
            return []
        try:
            profile = self._store.read(session)[0]
        except RuntimeError:
            return []
        if profile["sender_id"] not in self._allowlisted:
            return []
        return [{"sender_id": profile["sender_id"], "display_name": profile["display_name"], "chat_id": session}]

    def _is_gateway(self, chat_id) -> bool:
        if not str(chat_id).startswith("gateway:"):
            return False
        self._store.sender(str(chat_id))
        return True

    def send_message(self, chat_id, message, reply_to_message_id):
        if self._is_gateway(chat_id):
            return self._store.record(str(chat_id), "reply", message=message, reply_to=reply_to_message_id)
        return self._delegate.send_message(chat_id, message, reply_to_message_id)

    def send_file(self, chat_id, file_path, caption, reply_to_message_id):
        if self._is_gateway(chat_id):
            return self._store.record(str(chat_id), "file", filename=file_path.name, caption=caption)
        return self._delegate.send_file(chat_id, file_path, caption, reply_to_message_id)

    def mark_read(self, chat_id, read_through_message_id):
        if self._is_gateway(chat_id):
            return
        return self._delegate.mark_read(chat_id, read_through_message_id)

    def send_typing(self, chat_id, duration_seconds):
        if self._is_gateway(chat_id):
            if duration_seconds > 0:
                time.sleep(duration_seconds)
            return
        return self._delegate.send_typing(chat_id, duration_seconds)

    def set_presence(self, online):
        return self._delegate.set_presence(online)
