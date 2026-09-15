from __future__ import annotations

import time

from src.gateway.store import GatewayStore


class GatewayTransport:
    def __init__(self, delegate, store: GatewayStore) -> None:
        self._delegate = delegate
        self._store = store

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
