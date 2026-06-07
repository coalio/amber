from __future__ import annotations

import asyncio
from datetime import timedelta

from telethon import TelegramClient, events
from telethon.tl.custom.message import Message

from src.events.bus import EventBus, emitter_context
from src.events.receiver import TelegramMessageReceivedEvent, TelegramTypingPayload, TelegramTypingUpdatedEvent, TelegramSenderPayload
from src.state.store import GlobalStateStore
from src.utils.message_archive import MessageArchive
from src.receiver.telegram.utils import normalize_telegram_message
from src.utils.time import utc_now


class TelegramReceiver:
    def __init__(
        self,
        client: TelegramClient,
        message_archive: MessageArchive,
        state_store: GlobalStateStore | None = None,
        transport=None,
    ) -> None:
        self._client = client
        self._message_archive = message_archive
        self._state_store = state_store
        self._transport = transport

    def register(self) -> None:
        self._client.add_event_handler(self._on_new_message, events.NewMessage())
        self._client.add_event_handler(self._on_user_update, events.UserUpdate())

    async def replay_open_question_backlog(self, *, limit: int = 20) -> None:
        if self._state_store is None:
            return
        state = self._state_store.snapshot()

        # backfill missed replies to active codex questions
        for question in state.open_questions.values():
            min_id = 0
            if state.delivery_state.get("last_outbound_chat_id") == question.chat_id:
                min_id = int(state.delivery_state.get("last_outbound_message_id") or 0)
            messages = await self._client.get_messages(question.chat_id, limit=limit, min_id=min_id)
            for message in reversed(list(messages)):
                normalized = await self.normalize_message(message)
                if normalized.payload.sender.is_self:
                    continue
                self._message_archive.put(normalized.payload)
                self._record_open_question_reply(normalized)
                should_mark_seen = await self._mark_active_chat_read_if_needed(normalized)
                await asyncio.to_thread(self._emit_normalized, normalized)
                if should_mark_seen:
                    self._state_store.mark_seen(normalized.payload.chat_id, normalized.payload.message_id)

    async def _on_new_message(self, event: events.NewMessage.Event) -> None:
        normalized = await self.normalize_message(event.message)

        # archive before downstream context reads
        self._message_archive.put(normalized.payload)
        self._record_open_question_reply(normalized)
        should_mark_seen = await self._mark_active_chat_read_if_needed(normalized)
        await asyncio.to_thread(self._emit_normalized, normalized)
        if should_mark_seen and self._state_store is not None:
            self._state_store.mark_seen(normalized.payload.chat_id, normalized.payload.message_id)

    async def normalize_message(self, message: Message) -> TelegramMessageReceivedEvent:
        return await normalize_telegram_message(message)

    def _emit_normalized(self, normalized: TelegramMessageReceivedEvent) -> None:
        with emitter_context("receiver.telegram"):
            EventBus.emit(normalized)

    def _record_open_question_reply(self, normalized: TelegramMessageReceivedEvent) -> None:
        if self._state_store is None:
            return
        message = normalized.payload

        # record only replies to waiting codex questions
        if message.sender.is_self:
            return
        if not self._state_store.open_questions_for_chat(message.chat_id, sender_id=str(message.sender.id)):
            return
        self._state_store.append_open_question_replies(
            chat_id=message.chat_id,
            sender_id=str(message.sender.id),
            content=message.content,
            message_id=message.message_id,
        )

    async def _mark_active_chat_read_if_needed(self, normalized: TelegramMessageReceivedEvent) -> bool:
        if self._state_store is None or self._transport is None:
            return False
        message = normalized.payload
        if message.sender.is_self:
            return False
        state = self._state_store.snapshot()

        # keep engaged chats visibly read
        is_active_chat = state.active_chat_id is not None and str(state.active_chat_id) == str(message.chat_id)
        is_open_question_chat = any(str(question.chat_id) == str(message.chat_id) for question in state.open_questions.values())
        if not is_active_chat and not is_open_question_chat:
            return False
        await asyncio.to_thread(self._transport.mark_read, message.chat_id, message.message_id)
        return is_open_question_chat

    async def _on_user_update(self, event: events.UserUpdate.Event) -> None:
        if getattr(event, "action", None) is None:
            return
        sender = await event.get_sender()
        chat_id = getattr(event, "chat_id", None) or getattr(event, "user_id", None)
        if chat_id is None:
            return
        active = not bool(getattr(event, "cancel", False))
        activity = self._typing_activity(event)
        now = utc_now()

        # normalize telethon typing state for runtime layers
        normalized = TelegramTypingUpdatedEvent(
            chat_id=int(chat_id),
            payload=TelegramTypingPayload(
                chat_id=int(chat_id),
                sender=TelegramSenderPayload(
                    id=str(getattr(sender, "id", getattr(event, "user_id", "unknown"))),
                    name=getattr(sender, "first_name", None) or getattr(sender, "title", None) or "unknown",
                    username=getattr(sender, "username", None),
                    is_self=False,
                ),
                timestamp=now,
                active=active,
                activity=activity,
                expires_at=now + timedelta(seconds=6) if active else None,
            ),
        )
        await asyncio.to_thread(self._emit_typing_update, normalized)

    def _emit_typing_update(self, normalized: TelegramTypingUpdatedEvent) -> None:
        with emitter_context("receiver.telegram"):
            EventBus.emit(normalized)

    def _typing_activity(self, event: events.UserUpdate.Event) -> str:
        if getattr(event, "typing", False):
            return "typing"
        if getattr(event, "uploading", False):
            return "uploading"
        if getattr(event, "recording", False):
            return "recording"
        if getattr(event, "playing", False):
            return "playing"
        return "activity"
