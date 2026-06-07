from __future__ import annotations

import random
import re
import time
from datetime import datetime, timedelta

from src.action.config import ActionConfig
from src.events.action import (
    MessageReadEvent,
    MessageReadPayload,
    OutboundChunkPayload,
    OutboundChunkSentEvent,
    OutboundDeliveryPayload,
    OutboundMessageSentEvent,
    PresenceStateChangedEvent,
    SleepStateChangedEvent,
    SleepStateChangedPayload,
)
from src.events.ai import SemanticDecisionMadeEvent
from src.events.bus import EventBus, emitter_context
from src.events.outbound import OutboundMessagePreparedEvent
from src.events.receiver import (
    TelegramAttachmentPayload,
    TelegramMessagePayload,
    TelegramReplySenderPayload,
    TelegramSenderPayload,
    TelegramTransportPayload,
)
from src.state.store import GlobalStateStore
from src.utils.logging import get_logger
from src.utils.message_archive import MessageArchive
from src.utils.scheduler import RuntimeScheduler
from src.utils.sleep import compute_sleep_window
from src.utils.time import local_now, utc_now


_FILLER_MESSAGE_RE = re.compile(r"\s*h+m+\s*[,.!?]*\s*", re.IGNORECASE)


class ActionLayer:
    _logger = get_logger("amber.action")

    def __init__(
        self,
        config: ActionConfig,
        transport,
        state_store: GlobalStateStore,
        scheduler: RuntimeScheduler,
        message_archive: MessageArchive,
        timezone_name: str,
    ) -> None:
        self._config = config
        self._transport = transport
        self._state_store = state_store
        self._scheduler = scheduler
        self._message_archive = message_archive
        self._timezone_name = timezone_name
        self._completed_event_ids: set[str] = set()
        self._send_attempts: dict[str, int] = {}
        self._pending_visible_reads: dict[str, MessageReadPayload] = {}
        self._seen_visible_read_keys: set[str] = set()
        self._presence_online: bool | None = None
        EventBus.subscribe("MessageReadEvent", self.handle_message_read)
        EventBus.subscribe("OutboundMessagePreparedEvent", self.handle_prepared_message)
        EventBus.subscribe("SemanticDecisionMadeEvent", self.handle_semantic_decision)
        EventBus.subscribe("PresenceStateChangedEvent", self.handle_presence_state_changed)
        self.refresh_sleep_window()

    def handle_message_read(self, event: MessageReadEvent) -> None:
        with emitter_context("action"):
            key = self._visible_read_key(event.payload.chat_id, event.payload.session_id)
            if event.payload.mark_seen:
                self._state_store.mark_seen(event.payload.chat_id, event.payload.read_through_message_id)
                self._pending_visible_reads.pop(key, None)
            elif not event.payload.mark_seen:
                self._seen_visible_read_keys.add(key)
            self._transport.mark_read(event.payload.chat_id, event.payload.read_through_message_id)

    def refresh_sleep_window(self) -> None:
        state = self._state_store.snapshot()

        # force awake state when sleep is disabled
        if self._config.disable_sleep_state:
            if (
                state.sleep_state != "awake"
                or state.scheduled_wake_at is not None
                or state.fatigue_alert_active
                or state.pending_sleep_window
            ):
                self._state_store.update_action_state(
                    sleep_state="awake",
                    woke_at=utc_now(),
                    slept_at=None,
                    scheduled_wake_at=None,
                    energy_level=16.0,
                    fatigue_alert_active=False,
                    pending_sleep_window={},
            )
            return

        # compute sleep window, fatigue, and wake schedule
        window = compute_sleep_window(state, self._timezone_name)
        now_local = local_now(self._timezone_name)
        tired_window_start = datetime.fromisoformat(str(window["tired_window_start"]))
        fatigue_alert = now_local >= tired_window_start and state.sleep_state == "awake"
        self._state_store.update_action_state(pending_sleep_window=window, fatigue_alert_active=fatigue_alert)
        if state.sleep_state == "asleep" and state.scheduled_wake_at is not None and utc_now() >= state.scheduled_wake_at:
            self._wake_up()
        elif state.sleep_state == "asleep" and state.scheduled_wake_at is not None:
            self._scheduler.schedule_at("action:wake", state.scheduled_wake_at, self._wake_up)

    def handle_prepared_message(self, event: OutboundMessagePreparedEvent) -> None:
        with emitter_context("action"):
            if event.event_id in self._completed_event_ids:
                return
            payload = event.payload

            # prepare visible-read state before typing
            read_key = self._visible_read_key(payload.chat_id, payload.session_id)
            pending_visible_read = self._pending_visible_reads.get(read_key)
            active_visible_read = pending_visible_read
            fallback_read_through_message_id = payload.visible_read_through_message_id
            if fallback_read_through_message_id is None:
                fallback_read_through_message_id = self._message_archive.latest_message_id(payload.chat_id)
            if active_visible_read is None and read_key not in self._seen_visible_read_keys and fallback_read_through_message_id is not None:
                active_visible_read = MessageReadPayload(
                    chat_id=payload.chat_id,
                    session_id=payload.session_id,
                    trigger_message_id=payload.trigger_message_id,
                    surfaced_message_ids=list(payload.visible_surfaced_message_ids),
                    surfaced_until_message_id=payload.visible_surfaced_until_message_id,
                    read_through_message_id=fallback_read_through_message_id,
                    mark_seen=False,
                    visible_not_before=payload.visible_read_not_before,
                )

            # resolve who can interrupt this batch
            reply_target_sender_id, reply_target_sender_name = self._resolve_reply_target(payload.chat_id, payload.reply_to_message_id)
            if reply_target_sender_id is None:
                reply_target_sender_id, reply_target_sender_name = self._open_question_reply_target(payload.chat_id)
            initial_observed_message_id = (
                payload.visible_surfaced_until_message_id
                or self._message_archive.latest_message_id(payload.chat_id)
                or 0
            )
            remaining_visible_delay = self._remaining_visible_read_delay_seconds(active_visible_read)
            if active_visible_read is not None or payload.frame_created_at is not None:
                self._logger.info(
                    "action.visible_read_delay",
                    extra={
                        "event": "action.visible_read_delay",
                        "context": {
                            "chat_id": payload.chat_id,
                            "session_id": payload.session_id,
                            "trigger_message_id": payload.trigger_message_id,
                            "frame_created_at": (
                                payload.frame_created_at.isoformat() if payload.frame_created_at is not None else None
                            ),
                            "visible_read_not_before": (
                                active_visible_read.visible_not_before.isoformat()
                                if active_visible_read is not None and active_visible_read.visible_not_before is not None
                                else None
                            ),
                            "elapsed_since_frame_seconds": round(
                                max((utc_now() - payload.frame_created_at).total_seconds(), 0.0),
                                3,
                            )
                            if payload.frame_created_at is not None
                            else None,
                            "remaining_delay_seconds": round(remaining_visible_delay, 3),
                        },
                    },
                )
            if remaining_visible_delay > 0:
                self._apply_real_delay(remaining_visible_delay if self._config.enable_real_delays else 0.0)

            # stop before first chunk if target already replied
            interruption_message_id = self._maybe_interrupt_before_send(
                chat_id=payload.chat_id,
                correlation_id=event.correlation_id,
                session_id=payload.session_id,
                trigger_message_id=payload.trigger_message_id,
                reply_to_message_id=payload.reply_to_message_id,
                reply_target_sender_id=reply_target_sender_id,
                reply_target_sender_name=reply_target_sender_name,
                ordered_messages=payload.ordered_messages,
                after_message_id=initial_observed_message_id,
            )
            if interruption_message_id is not None:
                self._completed_event_ids.add(event.event_id)
                EventBus.emit(
                    OutboundMessageSentEvent(
                        correlation_id=event.correlation_id,
                        chat_id=event.chat_id,
                        payload=OutboundDeliveryPayload(
                            chat_id=payload.chat_id,
                            reply_to_message_id=payload.reply_to_message_id,
                            ordered_messages=[],
                            sent_message_ids=[],
                            planned_message_count=len(payload.ordered_messages),
                            interrupted=True,
                            interruption_message_id=interruption_message_id,
                            no_send=payload.no_send,
                            delivered_at=utc_now(),
                            session_id=payload.session_id,
                            trigger_message_id=payload.trigger_message_id,
                        ),
                    )
                )
                return

            # mark visible messages read before delivery
            if active_visible_read is not None:
                self._emit_visible_read(
                    correlation_id=event.correlation_id,
                    chat_id=active_visible_read.chat_id,
                    session_id=active_visible_read.session_id,
                    trigger_message_id=active_visible_read.trigger_message_id,
                    surfaced_message_ids=active_visible_read.surfaced_message_ids,
                    surfaced_until_message_id=active_visible_read.surfaced_until_message_id,
                    read_through_message_id=active_visible_read.read_through_message_id,
                )
                self._pending_visible_reads.pop(read_key, None)
                self._seen_visible_read_keys.add(read_key)

            # finish no-send delivery without telegram io
            if payload.no_send or not payload.ordered_messages:
                self._completed_event_ids.add(event.event_id)
                delivery = OutboundDeliveryPayload(
                    chat_id=payload.chat_id,
                    reply_to_message_id=payload.reply_to_message_id,
                    ordered_messages=[],
                    sent_message_ids=[],
                    planned_message_count=0,
                    interrupted=False,
                    interruption_message_id=None,
                    no_send=True,
                    delivered_at=utc_now(),
                    session_id=payload.session_id,
                    trigger_message_id=payload.trigger_message_id,
                )
                EventBus.emit(OutboundMessageSentEvent(correlation_id=event.correlation_id, chat_id=event.chat_id, payload=delivery))
                return

            # send chunks in order; retry one transport failure
            try:
                sent_ids, sent_messages, interruption_message_id = self._send_outbound_messages(
                    event.correlation_id,
                    payload.chat_id,
                    payload.ordered_messages,
                    payload.reply_to_message_id,
                    payload.session_id,
                    payload.trigger_message_id,
                )
            except Exception as exc:
                attempts = self._send_attempts.get(event.event_id, 0) + 1
                self._send_attempts[event.event_id] = attempts
                self._logger.exception(
                    "action.send_failed",
                    extra={
                        "event": "action.send_failed",
                        "context": {
                            "chat_id": payload.chat_id,
                            "session_id": payload.session_id,
                            "trigger_message_id": payload.trigger_message_id,
                            "event_id": event.event_id,
                            "attempt": attempts,
                            "error": str(exc),
                        },
                    },
                )
                if attempts <= 1:
                    self._scheduler.schedule_after(f"action_retry:{event.event_id}", 2.0, self.handle_prepared_message, event)
                return

            # record delivered chunks for later context
            self._completed_event_ids.add(event.event_id)
            self._archive_outbound_messages(payload.chat_id, sent_messages, payload.reply_to_message_id, sent_ids)
            if sent_ids:
                self._state_store.touch_delivery_state({"last_outbound_message_id": sent_ids[-1], "last_outbound_chat_id": payload.chat_id})
            delivery = OutboundDeliveryPayload(
                chat_id=payload.chat_id,
                reply_to_message_id=payload.reply_to_message_id,
                ordered_messages=sent_messages,
                sent_message_ids=sent_ids,
                no_send=False,
                delivered_at=utc_now(),
                session_id=payload.session_id,
                trigger_message_id=payload.trigger_message_id,
                planned_message_count=len(payload.ordered_messages),
                interrupted=interruption_message_id is not None,
                interruption_message_id=interruption_message_id,
            )
            EventBus.emit(OutboundMessageSentEvent(correlation_id=event.correlation_id, chat_id=event.chat_id, payload=delivery))

    def _visible_read_key(self, chat_id: int | str, session_id: str | None) -> str:
        if session_id:
            return f"session:{session_id}"
        return f"chat:{chat_id}"

    def _remaining_visible_read_delay_seconds(self, payload: MessageReadPayload | None) -> float:
        if payload is None or payload.visible_not_before is None:
            return 0.0
        return max((payload.visible_not_before - utc_now()).total_seconds(), 0.0)

    def _emit_visible_read(
        self,
        correlation_id: str,
        chat_id: int | str,
        session_id: str | None,
        trigger_message_id: int | None,
        surfaced_message_ids: list[int],
        surfaced_until_message_id: int | None,
        read_through_message_id: int | None,
    ) -> None:
        if read_through_message_id is None:
            read_through_message_id = self._message_archive.latest_message_id(chat_id)
        if read_through_message_id is None:
            return
        EventBus.emit(
            MessageReadEvent(
                correlation_id=correlation_id,
                chat_id=chat_id,
                payload=MessageReadPayload(
                    chat_id=chat_id,
                    session_id=session_id,
                    trigger_message_id=trigger_message_id,
                    surfaced_message_ids=list(surfaced_message_ids),
                    surfaced_until_message_id=surfaced_until_message_id,
                    read_through_message_id=read_through_message_id,
                    mark_seen=False,
                ),
            )
        )

    def handle_semantic_decision(self, event: SemanticDecisionMadeEvent) -> None:
        with emitter_context("action"):
            if event.payload.action != "sleep":
                return
            if self._config.disable_sleep_state:
                return
            self._transition_to_sleep()

    def handle_presence_state_changed(self, event: PresenceStateChangedEvent) -> None:
        with emitter_context("action"):
            self._set_presence(
                online=event.payload.online,
                reason=event.payload.reason,
                session_id=event.payload.session_id,
                trigger_message_id=event.payload.trigger_message_id,
            )

    def sync_presence_from_state(self) -> None:
        state = self._state_store.snapshot()
        self._set_presence(
            online=bool(state.active_chat_id is not None and state.active_session_id is not None),
            reason="startup_sync",
            session_id=state.active_session_id,
            trigger_message_id=None,
        )

    def _typing_duration_seconds(self, message: str) -> float:
        chars = max(len(message.replace(" ", "")), 1)
        word_estimate = max(chars / 5.0, 1.0)
        length_ratio = min(word_estimate / 40.0, 1.0)
        baseline_wpm = self._config.typing_baseline_wpm - (20.0 * length_ratio)
        non_alphanumeric_penalty = sum(1 for char in message if not char.isalnum() and not char.isspace()) * 5.0
        negative_variance_wpm = random.uniform(0.0, 40.0)
        effective_wpm = max(baseline_wpm - non_alphanumeric_penalty - negative_variance_wpm, 30.0)
        return max(min((word_estimate / effective_wpm) * 60.0, 12.0), 0.4)

    def _set_presence(
        self,
        *,
        online: bool,
        reason: str,
        session_id: str | None,
        trigger_message_id: int | None,
    ) -> None:
        if self._presence_online is online:
            return
        self._transport.set_presence(online)
        self._presence_online = online
        self._logger.info(
            "action.presence_changed",
            extra={
                "event": "action.presence_changed",
                "context": {
                    "online": online,
                    "reason": reason,
                    "session_id": session_id,
                    "trigger_message_id": trigger_message_id,
                },
            },
        )

    def _inter_chunk_delay_seconds(self, next_message: str) -> float:
        base_delay = random.uniform(
            self._config.inter_chunk_delay_min_seconds,
            self._config.inter_chunk_delay_max_seconds,
        )
        extra_chars = max(len(next_message) - self._config.inter_chunk_delay_length_threshold_chars, 0)
        extra_steps = extra_chars // self._config.inter_chunk_delay_chars_per_step
        total_delay = base_delay + (extra_steps * self._config.inter_chunk_delay_step_seconds)
        return min(total_delay, self._config.inter_chunk_delay_total_max_seconds)

    def _filler_pause_seconds(self, message: str) -> float:
        if self._config.filler_pause_seconds <= 0:
            return 0.0
        if not _FILLER_MESSAGE_RE.fullmatch(message):
            return 0.0
        return self._config.filler_pause_seconds

    def _apply_real_delay(self, duration_seconds: float) -> None:
        if duration_seconds <= 0 or not self._config.enable_real_delays:
            return
        time.sleep(duration_seconds)

    def _send_outbound_messages(
        self,
        correlation_id: str,
        chat_id: int | str,
        ordered_messages: list[str],
        reply_to_message_id: int | None,
        session_id: str | None,
        trigger_message_id: int | None,
    ) -> tuple[list[int], list[str], int | None]:
        sent_ids: list[int] = []
        sent_messages: list[str] = []
        current_reply_to = reply_to_message_id
        chunk_count = len(ordered_messages)

        # only the reply target can interrupt this batch
        reply_target_sender_id, reply_target_sender_name = self._resolve_reply_target(chat_id, reply_to_message_id)
        if reply_target_sender_id is None:
            reply_target_sender_id, reply_target_sender_name = self._open_question_reply_target(chat_id)

        # reply threading applies only to the first chunk
        for chunk_index, message in enumerate(ordered_messages, start=1):
            typing_started_after_message_id = self._message_archive.latest_message_id(chat_id) or 0
            duration = self._typing_duration_seconds(message)
            applied_duration = duration if self._config.enable_real_delays else 0.0
            self._transport.send_typing(chat_id, applied_duration)
            interrupt_messages = self._interrupt_messages_during_typing(
                chat_id,
                typing_started_after_message_id,
                reply_target_sender_id,
            )
            sent_message_id = self._transport.send_message(chat_id, message, current_reply_to)
            sent_ids.append(sent_message_id)
            sent_messages.append(message)
            EventBus.emit(
                OutboundChunkSentEvent(
                    correlation_id=correlation_id,
                    chat_id=chat_id,
                    payload=OutboundChunkPayload(
                        chat_id=chat_id,
                        session_id=session_id,
                        trigger_message_id=trigger_message_id,
                        reply_to_message_id=current_reply_to,
                        chunk_index=chunk_index,
                        chunk_count=chunk_count,
                        message_text=message,
                        sent_message_id=sent_message_id,
                        typing_duration_seconds=applied_duration,
                    ),
                )
            )
            current_reply_to = None
            if chunk_index < chunk_count:
                # pause if the target interrupted after this chunk
                interruption_message_id = self._maybe_interrupt_after_chunk(
                    chat_id=chat_id,
                    correlation_id=correlation_id,
                    session_id=session_id,
                    trigger_message_id=trigger_message_id,
                    reply_to_message_id=reply_to_message_id,
                    reply_target_sender_id=reply_target_sender_id,
                    reply_target_sender_name=reply_target_sender_name,
                    chunk_index=chunk_index,
                    sent_reply_chunks=sent_messages,
                    remaining_reply_chunks=ordered_messages[chunk_index:],
                    interrupt_messages=interrupt_messages,
                )
                if interruption_message_id is not None:
                    return sent_ids, sent_messages, interruption_message_id
                next_message = ordered_messages[chunk_index]
                inter_chunk_delay = self._inter_chunk_delay_seconds(next_message)
                filler_pause = self._filler_pause_seconds(message)
                pause_seconds = max(inter_chunk_delay, filler_pause)
                applied_pause_seconds = pause_seconds if self._config.enable_real_delays else 0.0
                self._logger.info(
                    "action.inter_chunk_delay",
                    extra={
                        "event": "action.inter_chunk_delay",
                        "context": {
                            "chat_id": chat_id,
                            "session_id": session_id,
                            "trigger_message_id": trigger_message_id,
                            "from_chunk_index": chunk_index,
                            "to_chunk_index": chunk_index + 1,
                            "next_message_length": len(next_message),
                            "base_delay_seconds": round(inter_chunk_delay, 3),
                            "filler_pause_seconds": round(filler_pause, 3),
                            "delay_seconds": round(applied_pause_seconds, 3),
                        },
                    },
                )
                self._apply_real_delay(applied_pause_seconds)
        return sent_ids, sent_messages, None

    def _resolve_reply_target(self, chat_id: int | str, reply_to_message_id: int | None) -> tuple[str | None, str | None]:
        if reply_to_message_id is None:
            return None, None
        replied_message = self._message_archive.get(chat_id, reply_to_message_id)
        if replied_message is None or replied_message.sender.is_self:
            return None, None
        return replied_message.sender.id, replied_message.sender.name

    def _open_question_reply_target(self, chat_id: int | str) -> tuple[str | None, str | None]:
        questions = [
            question
            for question in self._state_store.snapshot().open_questions.values()
            if str(question.chat_id) == str(chat_id)
        ]
        if len(questions) != 1:
            return None, None
        question = questions[0]
        return question.sender_id, question.sender_name

    def _interrupt_messages_during_typing(
        self,
        chat_id: int | str,
        after_message_id: int,
        reply_target_sender_id: str | None,
    ) -> list[TelegramMessagePayload]:
        if reply_target_sender_id is None:
            return []
        return [
            message
            for message in self._message_archive.messages_after(chat_id, after_message_id)
            if not message.sender.is_self and message.sender.id == reply_target_sender_id
        ]

    def _maybe_interrupt_before_send(
        self,
        *,
        chat_id: int | str,
        correlation_id: str,
        session_id: str | None,
        trigger_message_id: int | None,
        reply_to_message_id: int | None,
        reply_target_sender_id: str | None,
        reply_target_sender_name: str | None,
        ordered_messages: list[str],
        after_message_id: int,
    ) -> int | None:
        interrupt_messages = self._interrupt_messages_during_typing(chat_id, after_message_id, reply_target_sender_id)
        return self._remember_pending_interruption(
            chat_id=chat_id,
            correlation_id=correlation_id,
            session_id=session_id,
            trigger_message_id=trigger_message_id,
            reply_to_message_id=reply_to_message_id,
            reply_target_sender_id=reply_target_sender_id,
            reply_target_sender_name=reply_target_sender_name,
            sent_reply_chunks=[],
            remaining_reply_chunks=ordered_messages,
            interrupt_messages=interrupt_messages,
            stopped_after_chunk_index=0,
        )

    def _maybe_interrupt_after_chunk(
        self,
        *,
        chat_id: int | str,
        correlation_id: str,
        session_id: str | None,
        trigger_message_id: int | None,
        reply_to_message_id: int | None,
        reply_target_sender_id: str | None,
        reply_target_sender_name: str | None,
        chunk_index: int,
        sent_reply_chunks: list[str],
        remaining_reply_chunks: list[str],
        interrupt_messages: list[TelegramMessagePayload],
    ) -> int | None:
        return self._remember_pending_interruption(
            chat_id=chat_id,
            correlation_id=correlation_id,
            session_id=session_id,
            trigger_message_id=trigger_message_id,
            reply_to_message_id=reply_to_message_id,
            reply_target_sender_id=reply_target_sender_id,
            reply_target_sender_name=reply_target_sender_name,
            sent_reply_chunks=sent_reply_chunks,
            remaining_reply_chunks=remaining_reply_chunks,
            interrupt_messages=interrupt_messages,
            stopped_after_chunk_index=chunk_index,
        )

    def _remember_pending_interruption(
        self,
        *,
        chat_id: int | str,
        correlation_id: str,
        session_id: str | None,
        trigger_message_id: int | None,
        reply_to_message_id: int | None,
        reply_target_sender_id: str | None,
        reply_target_sender_name: str | None,
        sent_reply_chunks: list[str],
        remaining_reply_chunks: list[str],
        interrupt_messages: list[TelegramMessagePayload],
        stopped_after_chunk_index: int,
    ) -> int | None:
        if not remaining_reply_chunks or reply_target_sender_id is None or not interrupt_messages:
            return None
        interrupting_message = interrupt_messages[-1]
        self._logger.info(
            "action.interruption_requested",
            extra={
                "event": "action.interruption_requested",
                "context": {
                    "chat_id": chat_id,
                    "session_id": session_id,
                    "trigger_message_id": trigger_message_id,
                    "reply_to_message_id": reply_to_message_id,
                    "reply_target_sender_id": reply_target_sender_id,
                    "interrupting_message_id": interrupting_message.message_id,
                    "chunk_index": stopped_after_chunk_index,
                    "remaining_chunk_count": len(remaining_reply_chunks),
                },
            },
        )
        self._state_store.remember_pending_interruption(
            chat_id=chat_id,
            session_id=session_id,
            original_trigger_message_id=trigger_message_id,
            original_reply_to_message_id=reply_to_message_id,
            interrupting_message_id=interrupting_message.message_id,
            reply_target_sender_id=reply_target_sender_id,
            reply_target_sender_name=reply_target_sender_name,
            sent_reply_chunks=list(sent_reply_chunks),
            remaining_reply_chunks=list(remaining_reply_chunks),
            created_at=utc_now(),
        )
        self._logger.info(
            "action.outbound_batch_paused_for_interruption",
            extra={
                "event": "action.outbound_batch_paused_for_interruption",
                "context": {
                    "correlation_id": correlation_id,
                    "chat_id": chat_id,
                    "session_id": session_id,
                    "trigger_message_id": trigger_message_id,
                    "interrupting_message_id": interrupting_message.message_id,
                    "stopped_after_chunk_index": stopped_after_chunk_index,
                    "remaining_chunk_count": len(remaining_reply_chunks),
                },
            },
        )
        return interrupting_message.message_id

    def _archive_outbound_messages(
        self,
        chat_id: int | str,
        ordered_messages: list[str],
        reply_to_message_id: int | None,
        sent_message_ids: list[int],
    ) -> None:
        current_reply_to = reply_to_message_id
        for message_id, text in zip(sent_message_ids, ordered_messages, strict=False):
            self._state_store.remember_self_message(message_id)
            self._message_archive.put(
                TelegramMessagePayload(
                    message_id=message_id,
                    chat_id=chat_id,
                    sender=TelegramSenderPayload(id="amber-self", name="amber", is_self=True),
                    timestamp=utc_now(),
                    content=text,
                    raw_text=text,
                    reply_to_message_id=current_reply_to,
                    reply_to_sender=TelegramReplySenderPayload(),
                    mentions=[],
                    attachment=TelegramAttachmentPayload(),
                    transport=TelegramTransportPayload(peer_id=chat_id, raw_chat_id=chat_id, raw_message_id=message_id),
                )
            )
            current_reply_to = None

    def _transition_to_sleep(self) -> None:
        now = utc_now()
        state = self._state_store.snapshot()
        late_bonus = 0.0
        if state.pending_sleep_window:
            max_awake_at = state.pending_sleep_window.get("max_awake_at")
            if isinstance(max_awake_at, str):
                late_delta = now - datetime.fromisoformat(max_awake_at)
                late_bonus = max(late_delta.total_seconds() / 3600.0, 0.0)
        sleep_hours = min(10.0, max(8.0, 8.0 + late_bonus + ((16.0 - state.energy_level) / 8.0)))
        scheduled_wake_at = now + timedelta(hours=sleep_hours)
        self._state_store.update_action_state(sleep_state="winding_down", fatigue_alert_active=False)
        EventBus.emit(
            SleepStateChangedEvent(
                payload=SleepStateChangedPayload(
                    sleep_state="winding_down",
                    changed_at=now,
                    scheduled_wake_at=scheduled_wake_at,
                )
            )
        )
        self._state_store.update_action_state(
            sleep_state="asleep",
            slept_at=now,
            scheduled_wake_at=scheduled_wake_at,
            fatigue_alert_active=False,
        )
        EventBus.emit(
            SleepStateChangedEvent(
                payload=SleepStateChangedPayload(
                    sleep_state="asleep",
                    changed_at=now,
                    scheduled_wake_at=scheduled_wake_at,
                )
            )
        )
        self._scheduler.schedule_at("action:wake", scheduled_wake_at, self._wake_up)

    def _wake_up(self) -> None:
        now = utc_now()
        self._state_store.update_action_state(
            sleep_state="awake",
            woke_at=now,
            scheduled_wake_at=None,
            energy_level=16.0,
            fatigue_alert_active=False,
        )
        self.refresh_sleep_window()
        EventBus.emit(
            SleepStateChangedEvent(
                payload=SleepStateChangedPayload(
                    sleep_state="awake",
                    changed_at=now,
                    scheduled_wake_at=None,
                )
            )
        )
