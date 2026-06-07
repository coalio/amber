from __future__ import annotations

from collections import defaultdict, deque
from threading import Lock

from src.events.receiver import TelegramMessagePayload


class MessageArchive:
    _instance: "MessageArchive | None" = None
    _instance_lock = Lock()

    def __init__(self, max_messages_per_chat: int = 500) -> None:
        self._max_messages_per_chat = max_messages_per_chat
        self._messages_by_chat: dict[str, deque[TelegramMessagePayload]] = defaultdict(deque)
        self._index: dict[tuple[str, int], TelegramMessagePayload] = {}
        self._lock = Lock()

    @classmethod
    def instance(cls) -> "MessageArchive":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def put(self, message: TelegramMessagePayload) -> None:
        chat_key = str(message.chat_id)
        with self._lock:
            key = (chat_key, message.message_id)
            if key in self._index:
                return
            bucket = self._messages_by_chat[chat_key]
            bucket.append(message)
            self._index[key] = message
            while len(bucket) > self._max_messages_per_chat:
                evicted = bucket.popleft()
                self._index.pop((chat_key, evicted.message_id), None)

    def get(self, chat_id: int | str, message_id: int) -> TelegramMessagePayload | None:
        with self._lock:
            return self._index.get((str(chat_id), message_id))

    def latest_message_id(self, chat_id: int | str) -> int | None:
        chat_key = str(chat_id)
        with self._lock:
            bucket = self._messages_by_chat.get(chat_key)
            if not bucket:
                return None
            return bucket[-1].message_id

    def recent_segment_for_sender(
        self,
        chat_id: int | str,
        sender_id: str,
        before_message_id: int,
        limit: int = 6,
    ) -> list[TelegramMessagePayload]:
        chat_key = str(chat_id)
        with self._lock:
            bucket = list(self._messages_by_chat.get(chat_key, []))
        results: list[TelegramMessagePayload] = []
        for message in reversed(bucket):
            if message.message_id >= before_message_id:
                continue
            if message.sender.id != sender_id:
                if results:
                    break
                continue
            results.append(message)
            if len(results) >= limit:
                break
        return list(reversed(results))

    def window_around_message(
        self,
        chat_id: int | str,
        message_id: int,
        *,
        before: int = 15,
        after: int = 15,
    ) -> list[TelegramMessagePayload]:
        chat_key = str(chat_id)
        with self._lock:
            bucket = list(self._messages_by_chat.get(chat_key, []))
        for index, message in enumerate(bucket):
            if message.message_id != message_id:
                continue
            start = max(index - before, 0)
            end = min(index + after + 1, len(bucket))
            return bucket[start:end]
        return []

    def messages_after(self, chat_id: int | str, after_message_id: int) -> list[TelegramMessagePayload]:
        chat_key = str(chat_id)
        with self._lock:
            bucket = list(self._messages_by_chat.get(chat_key, []))
        if after_message_id <= 0:
            return bucket
        for index in range(len(bucket) - 1, -1, -1):
            if bucket[index].message_id == after_message_id:
                return bucket[index + 1 :]
        return [message for message in bucket if message.message_id > after_message_id]

    def reset(self) -> None:
        with self._lock:
            self._messages_by_chat.clear()
            self._index.clear()
