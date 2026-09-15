from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta

from src.events.receiver import (
    TelegramMessagePayload, TelegramMessageReceivedEvent, TelegramSenderPayload,
    TelegramTransportPayload, TelegramTypingPayload, TelegramTypingUpdatedEvent,
)
from src.receiver.chat import NormalizedChatIngress
from src.receiver.telegram.utils import extract_mentions, normalize_content
from src.state.gateway import GatewayStore
from src.utils.time import utc_now


class GatewayReceiver:
    def __init__(
        self, ingress: NormalizedChatIngress, store: GatewayStore, allowlisted_sender_ids,
        *, lookup_reply: Callable[[int | str, int], TelegramMessagePayload | None],
        sender_names: dict[str, str] | None = None,
    ) -> None:
        self._ingress = ingress
        self._store = store
        self._allowlisted = {str(item).removeprefix("user") for item in allowlisted_sender_ids}
        self._sender_names = sender_names or {}
        self._lookup_reply = lookup_reply
        self._tasks: set[asyncio.Task] = set()

    async def close(self) -> None:
        # cancel accepted bursts before the workspace event loop closes
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def submit(self, request: dict) -> dict:
        # authorize the operator and validate the entire burst before scheduling input
        sender = str(request["sender"])
        if sender not in self._allowlisted:
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
        session = request.get("session") or self._store.create(sender, self._sender_names.get(sender, "admin"))
        if self._store.sender(session) != sender:
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
            sender = TelegramSenderPayload(id=sender_id, name=self._store.read(session)[0]["display_name"])
            for index, text in enumerate(messages):
                if index:
                    # real typing updates and message gaps exercise the same debounce gate
                    if typing_seconds:
                        await self._typing(session, sender, True, max(interval, typing_seconds) + 0.1)
                    await asyncio.sleep(max(interval, typing_seconds))
                content, raw_text = normalize_content(text, None)
                message_id = self._store.record(session, "received", message=text)
                reply = self._lookup_reply(session, reply_to) if reply_to else None
                payload = TelegramMessagePayload(
                    message_id=message_id, chat_id=session, sender=sender, timestamp=utc_now(),
                    content=content, raw_text=raw_text, mentions=extract_mentions(content),
                    reply_to_message_id=reply_to,
                    reply_to_content=reply.content if reply else None,
                    reply_to_sender={"id": reply.sender.id, "name": reply.sender.name} if reply else {},
                    transport=TelegramTransportPayload(peer_id=session, raw_chat_id=session, raw_message_id=message_id),
                )
                deliveries.append(asyncio.create_task(self._ingress.receive(
                    TelegramMessageReceivedEvent(chat_id=session, payload=payload),
                )))
                # telegram callbacks overlap while a prior message is still being processed
                await asyncio.sleep(0)
                if index and typing_seconds:
                    await self._typing(session, sender, False, 0)
            await asyncio.gather(*deliveries)
        except Exception as exc:
            self._store.record(session, "error", error_type=type(exc).__name__)
        finally:
            for delivery in deliveries:
                if not delivery.done():
                    delivery.cancel()
            await asyncio.gather(*deliveries, return_exceptions=True)

    async def _typing(self, session, sender, active, duration) -> None:
        # publish activity through the same public ingress used by telegram
        event = TelegramTypingUpdatedEvent(chat_id=session, payload=TelegramTypingPayload(
            chat_id=session, sender=sender, timestamp=utc_now(), active=active, activity="typing",
            expires_at=utc_now() + timedelta(seconds=duration) if active else None,
        ))
        await self._ingress.receive_typing(event)
