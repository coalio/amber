from __future__ import annotations

import asyncio
import fcntl
import json
from datetime import timedelta
from pathlib import Path

from src.events.receiver import (
    TelegramMessagePayload, TelegramMessageReceivedEvent, TelegramSenderPayload,
    TelegramTransportPayload, TelegramTypingPayload, TelegramTypingUpdatedEvent,
)
from src.gateway.store import GatewayStore
from src.receiver.telegram.utils import extract_mentions, normalize_content
from src.utils.time import utc_now


class GatewayServer:
    def __init__(self, socket_path: Path, receiver, store: GatewayStore) -> None:
        self.socket_path = socket_path
        self.receiver = receiver
        self.store = store
        self._server = None
        self._lock_file = None
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        # an exclusive lease makes stale socket cleanup safe across process restarts
        self._lock_file = self.socket_path.with_suffix(".lock").open("a")
        try:
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._lock_file.close()
            raise RuntimeError("Another runtime owns this workspace gateway.") from exc
        self.socket_path.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(self._handle, path=self.socket_path, limit=65536)
        self.socket_path.chmod(0o600)

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self.socket_path.unlink(missing_ok=True)
        if self._lock_file is not None:
            self._lock_file.close()

    async def _handle(self, reader, writer) -> None:
        try:
            request = json.loads(await asyncio.wait_for(reader.readline(), timeout=5))
            response = await self._request(request)
        except (ValueError, TypeError, KeyError, RuntimeError, asyncio.TimeoutError) as exc:
            response = {"error": str(exc)}
        writer.write((json.dumps(response) + "\n").encode())
        try:
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def _request(self, request: dict) -> dict:
        if not isinstance(request, dict):
            raise RuntimeError("Gateway request must be an object.")
        if request.get("action") == "events":
            return {"events": self.store.read(str(request["session"]))}
        if request.get("action") != "send":
            raise RuntimeError("Unknown gateway operation.")
        sender = str(request["sender"])
        if sender not in self.store.allowlisted_sender_ids:
            raise RuntimeError("--as must identify an allowlisted Telegram administrator.")
        messages = request["messages"]
        if not isinstance(messages, list) or not 1 <= len(messages) <= 20 or any(
            not isinstance(item, str) or not item.strip() or len(item) > 16000 for item in messages
        ):
            raise RuntimeError("Provide 1 to 20 non-empty messages of at most 16000 characters each.")
        typing_seconds = float(request.get("typing_seconds", 0))
        interval = float(request.get("interval", 0))
        if not 0 <= typing_seconds <= 60 or not 0 <= interval <= 60:
            raise RuntimeError("Typing duration and message interval must be between 0 and 60 seconds.")
        session = request.get("session") or self.store.create(sender)
        if self.store.sender(session) != sender:
            raise RuntimeError("The gateway session belongs to a different sender.")
        reply_to = request.get("reply_to")
        if reply_to is not None and (not isinstance(reply_to, int) or reply_to <= 0):
            raise RuntimeError("Reply target must be a positive message id.")
        # return acceptance immediately; the normal receiver owns asynchronous processing
        task = asyncio.create_task(self._deliver(session, sender, messages, interval, typing_seconds, reply_to))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return {"session": session, "accepted": len(messages)}

    async def _deliver(self, session, sender_id, messages, interval, typing_seconds, reply_to) -> None:
        deliveries = []
        try:
            sender = TelegramSenderPayload(id=sender_id, name="admin")
            for index, text in enumerate(messages):
                if index:
                    # real typing updates and message gaps exercise the same debounce gate
                    if typing_seconds:
                        await self._typing(session, sender, True, max(interval, typing_seconds) + 0.1)
                    await asyncio.sleep(max(interval, typing_seconds))
                content, raw_text = normalize_content(text, None)
                message_id = self.store.record(session, "received", message=text)
                reply = self.receiver._message_archive.get(session, reply_to) if reply_to else None
                payload = TelegramMessagePayload(
                    message_id=message_id, chat_id=session, sender=sender, timestamp=utc_now(),
                    content=content, raw_text=raw_text, mentions=extract_mentions(content),
                    reply_to_message_id=reply_to,
                    reply_to_content=reply.content if reply else None,
                    reply_to_sender={"id": reply.sender.id, "name": reply.sender.name} if reply else {},
                    transport=TelegramTransportPayload(peer_id=session, raw_chat_id=session, raw_message_id=message_id),
                )
                deliveries.append(asyncio.create_task(self.receiver.receive(
                    TelegramMessageReceivedEvent(chat_id=session, payload=payload),
                )))
                # telegram callbacks overlap while a prior message is still being processed
                await asyncio.sleep(0)
                if index and typing_seconds:
                    await self._typing(session, sender, False, 0)
            await asyncio.gather(*deliveries)
        except Exception as exc:
            self.store.record(session, "error", error_type=type(exc).__name__)
        finally:
            for delivery in deliveries:
                if not delivery.done():
                    delivery.cancel()
            await asyncio.gather(*deliveries, return_exceptions=True)

    async def _typing(self, session, sender, active, duration) -> None:
        event = TelegramTypingUpdatedEvent(chat_id=session, payload=TelegramTypingPayload(
            chat_id=session, sender=sender, timestamp=utc_now(), active=active, activity="typing",
            expires_at=utc_now() + timedelta(seconds=duration) if active else None,
        ))
        await asyncio.to_thread(self.receiver._emit_typing_update, event)
