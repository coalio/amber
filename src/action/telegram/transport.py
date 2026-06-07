from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

from telethon import TelegramClient
from telethon.tl.functions.account import UpdateStatusRequest


@dataclass
class TransportRecord:
    chat_id: int | str
    ordered_messages: list[str]
    reply_to_message_id: int | None
    sent_message_ids: list[int]
    typing_durations: list[float] = field(default_factory=list)
    no_send: bool = False


@dataclass
class ReadRecord:
    chat_id: int | str
    read_through_message_id: int


@dataclass
class FileTransportRecord:
    chat_id: int | str
    file_path: Path
    caption: str | None
    reply_to_message_id: int | None
    sent_message_id: int


class RecordingTransport:
    def __init__(self) -> None:
        self.records: list[TransportRecord] = []
        self.file_records: list[FileTransportRecord] = []
        self.read_records: list[ReadRecord] = []
        self.presence_records: list[bool] = []
        self._pending_typing_durations: dict[str, list[float]] = {}
        self._lock = Lock()
        self._next_message_id = 900000

    def send_typing(self, chat_id: int | str, duration_seconds: float) -> None:
        with self._lock:
            self._pending_typing_durations.setdefault(str(chat_id), []).append(duration_seconds)

    def mark_read(self, chat_id: int | str, read_through_message_id: int) -> None:
        with self._lock:
            self.read_records.append(ReadRecord(chat_id=chat_id, read_through_message_id=read_through_message_id))

    def set_presence(self, online: bool) -> None:
        with self._lock:
            self.presence_records.append(online)

    def send_message(self, chat_id: int | str, message: str, reply_to_message_id: int | None) -> int:
        with self._lock:
            self._next_message_id += 1
            sent_message_id = self._next_message_id
            typing_durations = self._pending_typing_durations.pop(str(chat_id), [])
            self.records.append(
                TransportRecord(
                    chat_id=chat_id,
                    ordered_messages=[message],
                    reply_to_message_id=reply_to_message_id,
                    sent_message_ids=[sent_message_id],
                    typing_durations=typing_durations,
                )
            )
            return sent_message_id

    def send_file(
        self,
        chat_id: int | str,
        file_path: Path,
        caption: str | None,
        reply_to_message_id: int | None,
    ) -> int:
        with self._lock:
            self._next_message_id += 1
            sent_message_id = self._next_message_id
            self.file_records.append(
                FileTransportRecord(
                    chat_id=chat_id,
                    file_path=file_path,
                    caption=caption,
                    reply_to_message_id=reply_to_message_id,
                    sent_message_id=sent_message_id,
                )
            )
            return sent_message_id

    def send_messages(self, chat_id: int | str, ordered_messages: Sequence[str], reply_to_message_id: int | None) -> list[int]:
        sent_ids: list[int] = []
        current_reply_to = reply_to_message_id
        for message in ordered_messages:
            sent_ids.append(self.send_message(chat_id, message, current_reply_to))
            current_reply_to = None
        return sent_ids


class TelegramTransport:
    def __init__(self, client: TelegramClient, loop: asyncio.AbstractEventLoop) -> None:
        self._client = client
        self._loop = loop

    async def _mark_read(self, chat_id: int | str, read_through_message_id: int) -> None:
        await self._client.send_read_acknowledge(entity=chat_id, max_id=read_through_message_id)

    async def _set_presence(self, online: bool) -> None:
        await self._client(UpdateStatusRequest(offline=not online))

    async def _typing(self, chat_id: int | str, duration_seconds: float) -> None:
        if duration_seconds <= 0:
            return
        async with self._client.action(chat_id, "typing"):
            await asyncio.sleep(duration_seconds)

    async def _send_messages(self, chat_id: int | str, ordered_messages: Sequence[str], reply_to_message_id: int | None) -> list[int]:
        sent_ids: list[int] = []
        current_reply_to = reply_to_message_id
        for message in ordered_messages:
            sent_ids.append(await self._send_message(chat_id, message, current_reply_to))
            current_reply_to = None
        return sent_ids

    async def _send_message(self, chat_id: int | str, message: str, reply_to_message_id: int | None) -> int:
        sent = await self._client.send_message(entity=chat_id, message=message, reply_to=reply_to_message_id)
        return int(sent.id)

    async def _send_file(
        self,
        chat_id: int | str,
        file_path: Path,
        caption: str | None,
        reply_to_message_id: int | None,
    ) -> int:
        sent = await self._client.send_file(
            entity=chat_id,
            file=str(file_path),
            caption=caption,
            reply_to=reply_to_message_id,
        )
        if isinstance(sent, list):
            if not sent:
                raise RuntimeError("Telegram did not return a sent message for the file.")
            return int(sent[-1].id)
        return int(sent.id)

    def send_typing(self, chat_id: int | str, duration_seconds: float) -> None:
        future = asyncio.run_coroutine_threadsafe(self._typing(chat_id, duration_seconds), self._loop)
        future.result()

    def mark_read(self, chat_id: int | str, read_through_message_id: int) -> None:
        future = asyncio.run_coroutine_threadsafe(self._mark_read(chat_id, read_through_message_id), self._loop)
        future.result()

    def set_presence(self, online: bool) -> None:
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is self._loop:
            self._loop.create_task(self._set_presence(online))
            return
        future = asyncio.run_coroutine_threadsafe(self._set_presence(online), self._loop)
        future.result()

    def send_message(self, chat_id: int | str, message: str, reply_to_message_id: int | None) -> int:
        future = asyncio.run_coroutine_threadsafe(
            self._send_message(chat_id, message, reply_to_message_id),
            self._loop,
        )
        return future.result()

    def send_file(
        self,
        chat_id: int | str,
        file_path: Path,
        caption: str | None,
        reply_to_message_id: int | None,
    ) -> int:
        future = asyncio.run_coroutine_threadsafe(
            self._send_file(chat_id, file_path, caption, reply_to_message_id),
            self._loop,
        )
        return future.result()

    def send_messages(self, chat_id: int | str, ordered_messages: Sequence[str], reply_to_message_id: int | None) -> list[int]:
        future = asyncio.run_coroutine_threadsafe(
            self._send_messages(chat_id, ordered_messages, reply_to_message_id),
            self._loop,
        )
        return future.result()
