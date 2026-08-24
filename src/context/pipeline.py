from __future__ import annotations

import random
from datetime import timedelta

from src.attention.memory.store import MemoryStore
from src.adapters.registry import AdapterRegistry
from src.attention.utils import derive_topic_summary
from src.context.config import ContextConfig
from src.context.session.store import ConversationSession
from src.events.action import (
    MessageReadEvent,
    MessageReadPayload,
    OutboundMessageSentEvent,
    PresenceStateChangedEvent,
    PresenceStateChangedPayload,
)
from src.events.ai import SemanticDecisionMadeEvent
from src.events.attention import AttentionDecisionMadeEvent
from src.events.bus import EventBus, emitter_context
from src.events.codex import (
    CodexCandidatePersonPayload,
    CodexNotificationReceivedEvent,
    CodexQuestionPayload,
    CodexQuestionReceivedEvent,
)
from src.events.context import (
    CodexCandidateConversationPayload,
    CodexFollowupFramePayload,
    CodexNotificationFramePayload,
    ContextFrameMessagePayload,
    ContextFramePayload,
    ContextFrameReadyEvent,
    LinearTaskListFramePayload,
    OpenQuestionPayload,
    PendingInterruptionPayload,
)
from src.events.linear import LinearTaskListReceivedEvent
from src.events.receiver import TelegramTypingUpdatedEvent
from src.state.models import GlobalState, OpenQuestion, OpenQuestionCandidate
from src.state.store import GlobalStateStore
from src.adapters.linear.status import set_linear_status
from src.utils.ids import new_session_id
from src.utils.logging import get_logger
from src.utils.message_archive import MessageArchive
from src.utils.scheduler import RuntimeScheduler
from src.utils.sleep import fatigue_notice
from src.utils.time import utc_now


class ContextLayer:
    _logger = get_logger("amber.context")

    def __init__(
        self,
        config: ContextConfig,
        state_store: GlobalStateStore,
        scheduler: RuntimeScheduler,
        message_archive: MessageArchive,
        memory_store: MemoryStore,
        timezone_name: str,
        adapter_registry: AdapterRegistry | None = None,
    ) -> None:
        self._config = config
        self._state_store = state_store
        self._scheduler = scheduler
        self._message_archive = message_archive
        self._memory_store = memory_store
        self._timezone_name = timezone_name
        self._adapter_registry = adapter_registry
        self._active_session: ConversationSession | None = None
        self._pending_codex_questions: dict[str, CodexQuestionPayload] = {}
        EventBus.subscribe("AttentionDecisionMadeEvent", self.handle_attention_decision)
        EventBus.subscribe("CodexQuestionReceivedEvent", self.handle_codex_question)
        EventBus.subscribe("CodexNotificationReceivedEvent", self.handle_codex_notification)
        EventBus.subscribe("LinearTaskListReceivedEvent", self.handle_linear_task_list)
        EventBus.subscribe("MessageReadEvent", self.handle_message_read)
        EventBus.subscribe("SemanticDecisionMadeEvent", self.handle_semantic_decision)
        EventBus.subscribe("OutboundMessageSentEvent", self.handle_outbound_delivery)
        EventBus.subscribe("TelegramTypingUpdatedEvent", self.handle_typing_update)

    def handle_attention_decision(self, event: AttentionDecisionMadeEvent) -> None:
        with emitter_context("context"):
            if event.payload.decision not in {"surface", "surface_urgent"}:
                return
            message = event.payload.message
            processed_at = utc_now()
            session = self._resolve_session(message.chat_id)
            converted = self._convert_message(message)
            self._upsert_message(session, converted)
            self._track_pending_surfaced_message(session, converted, processed_at)
            session.last_updated_at = processed_at
            session.latest_trigger_message_id = message.message_id
            session.engaged_user_ids.add(message.sender.id)
            session.participant_names[message.sender.id] = message.sender.name
            self._engage_conversation_window_participants(session, message.chat_id, message.message_id)
            session.recommended_reply_candidate = event.payload.reply_target_candidate or converted.message_id
            for memory in event.payload.memory_cards:
                session.memory_cards[memory.memory_id] = memory
            session.attention_classification = event.payload.classification
            if "always_surface_sender" in event.payload.reasons:
                session.response_required = True
                session.response_required_reason = "always_surface_sender"
            self._inject_replied_message_if_needed(session, converted)
            self._refresh_session_metadata(session)
            self._ensure_initial_engagement_delay(session, processed_at)
            self._sync_context_state(session, engaged_at=processed_at if session.engagement_committed else None)
            self._schedule_finalize(session)
            self._refresh_idle_expiry(
                session,
                trigger_message_id=converted.message_id,
                reason="attention_surface",
                now=processed_at,
            )

    def handle_codex_question(self, event: CodexQuestionReceivedEvent) -> None:
        with emitter_context("context"):
            question = event.payload
            linear_issue_id = question.context.get("linear_issue_id")
            if linear_issue_id:
                self._state_store.mark_linear_task_waiting_for_user(issue_id=str(linear_issue_id))
                self._set_linear_status(
                    str(linear_issue_id),
                    "in_progress",
                    note=f"Waiting for user input on Codex task {question.task_id}.",
                )
            self._pending_codex_questions[self._codex_key(question.app_server_id, question.task_id, question.tool_call_id)] = question
            current_message = ContextFrameMessagePayload(
                message_id=0,
                sender_id="codex",
                sender_name="Codex",
                is_self=False,
                content="\n".join([question.task_description, *question.questions]),
                timestamp=event.timestamp or utc_now(),
                source="codex_question",
            )
            EventBus.emit(
                ContextFrameReadyEvent(
                    correlation_id=event.correlation_id,
                    chat_id=event.chat_id,
                    payload=ContextFramePayload(
                        session_id=f"codex:{question.task_id}",
                        chat_id=event.chat_id or f"codex:{question.task_id}",
                        trigger_message_id=0,
                        current_message=current_message,
                        recent_messages=[current_message],
                        conversation_window_messages=[current_message],
                        topic_summary="codex question",
                        participants=[candidate.display_name for candidate in question.candidate_people],
                        relevant_memories=[],
                        mood=self._state_store.snapshot().mood,
                        recommended_reply_candidate=None,
                        engaged_user_ids=[],
                        open_question=OpenQuestionPayload(
                            app_server_id=question.app_server_id,
                            task_id=question.task_id,
                            tool_call_id=question.tool_call_id,
                            questions=list(question.questions),
                            task_description=question.task_description,
                            context={
                                key: value
                                for key, value in question.context.items()
                                if isinstance(value, (str, int, float, bool)) or value is None
                            },
                            candidate_people=list(question.candidate_people),
                        ),
                        open_questions=[
                            OpenQuestionPayload(
                                app_server_id=question.app_server_id,
                                task_id=question.task_id,
                                tool_call_id=question.tool_call_id,
                                questions=list(question.questions),
                                task_description=question.task_description,
                                context={
                                    key: value
                                    for key, value in question.context.items()
                                    if isinstance(value, (str, int, float, bool)) or value is None
                                },
                                candidate_people=list(question.candidate_people),
                            )
                        ],
                    ),
                )
            )

    def handle_linear_task_list(self, event: LinearTaskListReceivedEvent) -> None:
        with emitter_context("context"):
            task_list = event.payload
            current_message = ContextFrameMessagePayload(
                message_id=0,
                sender_id="linear",
                sender_name="Linear",
                is_self=False,
                content=self._linear_task_list_content(task_list),
                timestamp=event.timestamp or utc_now(),
                source="linear_task_list",
            )
            EventBus.emit(
                ContextFrameReadyEvent(
                    correlation_id=event.correlation_id,
                    chat_id=event.chat_id,
                    payload=ContextFramePayload(
                        session_id=f"linear:{task_list.queue_hash}",
                        chat_id=event.chat_id or "linear:queue",
                        trigger_message_id=0,
                        current_message=current_message,
                        recent_messages=[current_message],
                        conversation_window_messages=[current_message],
                        topic_summary="linear due task queue",
                        participants=["Linear"],
                        relevant_memories=[],
                        mood=self._state_store.snapshot().mood,
                        recommended_reply_candidate=None,
                        engaged_user_ids=[],
                        linear_task_list=LinearTaskListFramePayload(
                            tasks=list(task_list.tasks),
                            generated_at=task_list.generated_at,
                            window_start_date=task_list.window_start_date,
                            window_end_date=task_list.window_end_date,
                            queue_hash=task_list.queue_hash,
                        ),
                    ),
                )
            )

    def handle_codex_notification(self, event: CodexNotificationReceivedEvent) -> None:
        with emitter_context("context"):
            notification = event.payload
            self._state_store.remember_codex_task(
                app_server_id=notification.app_server_id,
                task_id=notification.task_id,
                updated_at=event.timestamp or utc_now(),
            )
            current_message = ContextFrameMessagePayload(
                message_id=0,
                sender_id="codex",
                sender_name="Codex",
                is_self=False,
                content="\n".join([notification.task_description, notification.message]),
                timestamp=event.timestamp or utc_now(),
                source="codex_notification",
            )
            EventBus.emit(
                ContextFrameReadyEvent(
                    correlation_id=event.correlation_id,
                    chat_id=event.chat_id,
                    payload=ContextFramePayload(
                        session_id=f"codex-notify:{notification.app_server_id}:{notification.task_id}",
                        chat_id=event.chat_id or f"codex:{notification.task_id}",
                        trigger_message_id=0,
                        current_message=current_message,
                        recent_messages=[current_message],
                        conversation_window_messages=[current_message],
                        topic_summary="codex notification",
                        participants=[candidate.display_name for candidate in notification.candidate_people],
                        relevant_memories=[],
                        mood=self._state_store.snapshot().mood,
                        recommended_reply_candidate=None,
                        engaged_user_ids=[],
                        codex_notification=CodexNotificationFramePayload(
                            app_server_id=notification.app_server_id,
                            task_id=notification.task_id,
                            notification_id=notification.notification_id,
                            notification_kind=notification.notification_kind,
                            message=notification.message,
                            task_description=notification.task_description,
                            context={
                                key: value
                                for key, value in notification.context.items()
                                if isinstance(value, (str, int, float, bool)) or value is None
                            },
                            candidate_people=list(notification.candidate_people),
                            candidate_conversations=self._codex_candidate_conversations(
                                notification.candidate_people
                            ),
                        ),
                    ),
                )
            )

    def _codex_candidate_conversations(
        self,
        candidates: list[CodexCandidatePersonPayload],
    ) -> list[CodexCandidateConversationPayload]:
        conversations: list[CodexCandidateConversationPayload] = []
        for candidate in candidates:
            archived = self._message_archive.recent_messages(
                candidate.chat_id,
                limit=self._config.recent_message_budget,
            )
            conversations.append(
                CodexCandidateConversationPayload(
                    sender_id=candidate.sender_id,
                    chat_id=candidate.chat_id,
                    recent_messages=[
                        self._convert_archived_message(message, source="codex_candidate_history")
                        for message in archived
                    ],
                )
            )
        return conversations

    def _linear_task_list_content(self, task_list) -> str:
        lines = [
            f"Linear tasks due {task_list.window_start_date} through {task_list.window_end_date}:",
        ]
        for task in task_list.tasks:
            due = f" due {task.due_date}" if task.due_date else ""
            project = f" [{task.project}]" if task.project else ""
            lines.append(f"- {task.identifier}{project}{due}: {task.title}")
        return "\n".join(lines)

    def _set_linear_status(self, issue_id: str, status: str, *, note: str | None = None) -> None:
        if self._adapter_registry is None:
            return
        try:
            set_linear_status(self._adapter_registry, issue_id=issue_id, status=status, note=note)
        except RuntimeError as exc:
            self._state_store.mark_linear_task_last_error(issue_id=issue_id, error=str(exc))

    def handle_semantic_decision(self, event: SemanticDecisionMadeEvent) -> None:
        with emitter_context("context"):
            self._maybe_open_codex_question(event)
            self._apply_memory_mutation(event)
            if event.payload.action == "disengage":
                self._handle_disengage(event)
                return
            if event.payload.action != "expand_memory":
                return
            session = self._active_session
            if session is None or not event.payload.session_id or session.session_id != event.payload.session_id:
                return
            missing_ids = [memory_id for memory_id in event.payload.referenced_memory_ids if memory_id not in session.expanded_memory_ids]
            if not missing_ids:
                return
            expanded_by_owner: dict[tuple[str, str], list[str]] = {}
            for memory_id in missing_ids:
                existing = session.memory_cards.get(memory_id)
                if existing is None or not existing.owner_sender_id:
                    continue
                owner_name = existing.owner_sender_name or self._participant_name(session, existing.owner_sender_id)
                expanded_by_owner.setdefault((existing.owner_sender_id, owner_name), []).append(memory_id)
            for (owner_sender_id, owner_sender_name), owner_memory_ids in expanded_by_owner.items():
                expanded = self._memory_store.expand(owner_sender_id, owner_sender_name, owner_memory_ids)
                for memory in expanded:
                    session.memory_cards[memory.memory_id] = memory
                    session.expanded_memory_ids.add(memory.memory_id)
            self._emit_frame(session)

    def _maybe_open_codex_question(self, event: SemanticDecisionMadeEvent) -> None:
        payload = event.payload
        if payload.action != "reply" or not payload.codex_app_server_id or not payload.codex_task_id or not payload.codex_tool_call_id:
            return
        question = self._pending_codex_questions.pop(
            self._codex_key(payload.codex_app_server_id, payload.codex_task_id, payload.codex_tool_call_id),
            None,
        )
        if question is None or not payload.codex_target_sender_id:
            return
        target = next((candidate for candidate in question.candidate_people if candidate.sender_id == payload.codex_target_sender_id), None)
        if target is None:
            return
        created_at = utc_now()
        self._state_store.remember_open_question(
            chat_id=target.chat_id,
            sender_id=target.sender_id,
            sender_name=target.display_name,
            app_server_id=question.app_server_id,
            task_id=question.task_id,
            tool_call_id=question.tool_call_id,
            questions=list(question.questions),
            task_description=question.task_description,
            context={
                key: value
                for key, value in question.context.items()
                if isinstance(value, (str, int, float, bool)) or value is None
            },
            candidate_people=[
                OpenQuestionCandidate.model_validate(candidate.model_dump(mode="json"))
                for candidate in question.candidate_people
            ],
            created_at=created_at,
        )

    def handle_message_read(self, event: MessageReadEvent) -> None:
        with emitter_context("context"):
            session = self._active_session
            if session is None:
                return
            if event.payload.mark_seen:
                return
            if event.payload.session_id is None or session.session_id != event.payload.session_id:
                return
            if str(session.chat_id) != str(event.payload.chat_id):
                return
            if session.engagement_committed:
                return
            engaged_at = event.timestamp or utc_now()
            engagement_delay_seconds = 0.0
            if session.engagement_pending_since is not None:
                engagement_delay_seconds = max((engaged_at - session.engagement_pending_since).total_seconds(), 0.0)
            session.engagement_committed = True
            session.engagement_delay_until = None
            session.engagement_pending_since = None
            self._sync_context_state(session, engaged_at=engaged_at)
            self._logger.info(
                "context.engaged",
                extra={
                    "event": "context.engaged",
                    "context": {
                        "session_id": session.session_id,
                        "chat_id": session.chat_id,
                        "trigger_message_id": event.payload.trigger_message_id or session.latest_trigger_message_id,
                        "engagement_delay_seconds": round(engagement_delay_seconds, 3),
                        "engaged_user_ids": sorted(session.engaged_user_ids),
                        "read_through_message_id": event.payload.read_through_message_id,
                    },
                },
            )

    def handle_typing_update(self, event: TelegramTypingUpdatedEvent) -> None:
        with emitter_context("context"):
            session = self._active_session
            if session is None:
                return
            if str(session.chat_id) != str(event.payload.chat_id):
                return
            sender_id = event.payload.sender.id
            if sender_id not in session.engaged_user_ids:
                return
            if event.payload.active and event.payload.expires_at is not None:
                session.typing_until_by_sender[sender_id] = event.payload.expires_at
            else:
                session.typing_until_by_sender.pop(sender_id, None)

    def _apply_memory_mutation(self, event: SemanticDecisionMadeEvent) -> None:
        session = self._active_session
        if session is None or not event.payload.session_id or session.session_id != event.payload.session_id:
            return
        if event.payload.memory_mutation == "none" or event.payload.action == "expand_memory":
            return
        if not event.payload.target_memory_id:
            return
        target_memory = session.memory_cards.get(event.payload.target_memory_id)
        if target_memory is None:
            return
        target_sender_id = event.payload.target_memory_sender_id or target_memory.owner_sender_id
        if not target_sender_id or self._is_self_sender(target_sender_id):
            return
        target_sender_name = target_memory.owner_sender_name or self._participant_name(session, target_sender_id)
        mutated_at = utc_now()
        if event.payload.memory_mutation == "rewrite":
            rewritten_text = event.payload.rewritten_memory_text or target_memory.text
            rewritten_tags = list(event.payload.rewritten_memory_tags or target_memory.tags)
            updated_memory = self._memory_store.rewrite_memory(
                target_sender_id,
                target_sender_name,
                target_memory.memory_id,
                rewritten_text,
                rewritten_tags,
                timestamp=mutated_at,
            )
            if updated_memory is not None:
                session.memory_cards[target_memory.memory_id] = updated_memory
            return
        if event.payload.memory_mutation == "forget":
            forgot = self._memory_store.forget_memory(
                target_sender_id,
                target_sender_name,
                target_memory.memory_id,
                timestamp=mutated_at,
            )
            if forgot:
                session.memory_cards.pop(target_memory.memory_id, None)
                session.expanded_memory_ids.discard(target_memory.memory_id)

    def _handle_disengage(self, event: SemanticDecisionMadeEvent) -> None:
        session = self._active_session
        if session is None or not event.payload.session_id or session.session_id != event.payload.session_id:
            return
        target_sender_id = event.payload.disengage_sender_id or self._current_message_for_frame(session).sender_id
        target_sender_name = self._participant_name(session, target_sender_id)
        disengaged_at = utc_now()
        if event.payload.ignore_for_seconds:
            self._state_store.remember_conversation_ignore(
                chat_id=session.chat_id,
                sender_id=target_sender_id,
                sender_name=target_sender_name,
                created_at=disengaged_at,
                ignore_until=disengaged_at + timedelta(seconds=event.payload.ignore_for_seconds),
                reason=event.payload.disengage_reason,
            )
        bad_memory_sender_id = event.payload.bad_memory_sender_id or target_sender_id
        if event.payload.create_bad_memory and bad_memory_sender_id and not self._is_self_sender(bad_memory_sender_id):
            bad_memory_sender_name = self._participant_name(session, bad_memory_sender_id)
            memory_text = event.payload.bad_memory_text or event.payload.disengage_reason or self._current_message_for_frame(session).content
            self._memory_store.write_bad_memory(
                bad_memory_sender_id,
                bad_memory_sender_name,
                memory_text,
                source_message_id=event.payload.trigger_message_id,
                timestamp=disengaged_at,
            )
        self._disengage_session(
            session,
            correlation_id=event.correlation_id,
            trigger_message_id=event.payload.trigger_message_id,
            reason="semantic_disengage",
        )

    def handle_outbound_delivery(self, event: OutboundMessageSentEvent) -> None:
        with emitter_context("context"):
            session = self._active_session
            if session is None or event.payload.session_id is None or session.session_id != event.payload.session_id:
                return
            if str(session.chat_id) != str(event.payload.chat_id):
                return
            session.frame_in_flight = False
            if not event.payload.no_send:
                session.last_updated_at = utc_now()
            if not event.payload.no_send and event.payload.sent_message_ids:
                delivered_through = max(event.payload.sent_message_ids)
                session.pending_read_through_message_id = max(session.pending_read_through_message_id or 0, delivered_through)
            self._refresh_idle_expiry(
                session,
                trigger_message_id=event.payload.trigger_message_id,
                reason="outbound_delivery",
            )
            self._schedule_finalize(session)

    def _resolve_session(self, chat_id: int | str) -> ConversationSession:
        now = utc_now()
        if self._active_session is None:
            self._active_session = ConversationSession(session_id=new_session_id(), chat_id=chat_id, last_updated_at=now)
            return self._active_session
        if str(self._active_session.chat_id) != str(chat_id):
            self._active_session = ConversationSession(session_id=new_session_id(), chat_id=chat_id, last_updated_at=now)
            return self._active_session
        if self._session_idle_expired(self._active_session, now):
            self._active_session = ConversationSession(session_id=new_session_id(), chat_id=chat_id, last_updated_at=now)
        return self._active_session

    def _convert_message(self, message) -> ContextFrameMessagePayload:
        return ContextFrameMessagePayload(
            message_id=message.message_id,
            sender_id=message.sender.id,
            sender_name=message.sender.name,
            is_self=getattr(message.sender, "is_self", False),
            content=message.content,
            timestamp=message.timestamp,
            reply_to_message_id=message.reply_to_message_id,
            reply_to_sender_id=message.reply_to_sender.id,
            reply_to_sender_name=message.reply_to_sender.name,
            reply_to_content=message.reply_to_content,
            reply_to_raw_text=message.reply_to_raw_text,
            source="surface",
        )

    def _convert_archived_message(
        self,
        message,
        *,
        source: str,
    ) -> ContextFrameMessagePayload:
        return ContextFrameMessagePayload(
            message_id=message.message_id,
            sender_id=message.sender.id,
            sender_name=message.sender.name,
            is_self=message.sender.is_self,
            content=message.content,
            timestamp=message.timestamp,
            reply_to_message_id=message.reply_to_message_id,
            reply_to_sender_id=message.reply_to_sender.id,
            reply_to_sender_name=message.reply_to_sender.name,
            reply_to_content=message.reply_to_content,
            reply_to_raw_text=message.reply_to_raw_text,
            source=source,
        )

    def _upsert_message(self, session: ConversationSession, message: ContextFrameMessagePayload) -> None:
        existing = {item.message_id: item for item in session.recent_messages}
        existing[message.message_id] = message
        ordered = sorted(existing.values(), key=lambda item: item.timestamp)
        session.recent_messages = ordered

    def _track_pending_surfaced_message(
        self,
        session: ConversationSession,
        message: ContextFrameMessagePayload,
        surfaced_at,
    ) -> None:
        session.pending_surfaced_messages[message.message_id] = message
        if session.pending_first_surfaced_at is None:
            session.pending_first_surfaced_at = surfaced_at
        session.pending_latest_surfaced_at = surfaced_at
        if not session.engagement_committed and session.engagement_pending_since is None:
            session.engagement_pending_since = session.pending_first_surfaced_at

    def _engage_conversation_window_participants(
        self,
        session: ConversationSession,
        chat_id: int | str,
        trigger_message_id: int,
    ) -> None:
        for message in self._message_archive.window_around_message(
            chat_id,
            trigger_message_id,
            before=self._config.conversation_window_before,
            after=self._config.conversation_window_after,
        ):
            session.engaged_user_ids.add(message.sender.id)
            session.participant_names[message.sender.id] = message.sender.name

    def _inject_replied_message_if_needed(self, session: ConversationSession, message: ContextFrameMessagePayload) -> None:
        if message.reply_to_message_id is None:
            return
        if any(item.message_id == message.reply_to_message_id for item in session.recent_messages):
            return
        if message.sender_id not in session.engaged_user_ids:
            return
        injected = self._message_archive.get(session.chat_id, message.reply_to_message_id)
        if injected is None:
            return
        injected_message = ContextFrameMessagePayload(
            message_id=injected.message_id,
            sender_id=injected.sender.id,
            sender_name=injected.sender.name,
            is_self=injected.sender.is_self,
            content=injected.content,
            timestamp=injected.timestamp,
            reply_to_message_id=injected.reply_to_message_id,
            reply_to_sender_id=injected.reply_to_sender.id,
            reply_to_sender_name=injected.reply_to_sender.name,
            reply_to_content=injected.reply_to_content,
            reply_to_raw_text=injected.reply_to_raw_text,
            source="injected_reply_context",
        )
        self._upsert_message(session, injected_message)
        session.engaged_user_ids.add(injected.sender.id)
        session.participant_names[injected.sender.id] = injected.sender.name

    def _refresh_session_metadata(self, session: ConversationSession) -> None:
        budget = self._config.recent_message_budget
        if len(session.recent_messages) > budget:
            overflow = session.recent_messages[:-budget]
            session.recent_messages = session.recent_messages[-budget:]
            for item in overflow:
                fact = f"{item.sender_name}: {item.content[:120]}"
                if fact not in session.compacted_facts:
                    session.compacted_facts.append(fact)
            session.compacted_facts = session.compacted_facts[-self._config.max_compacted_facts :]
        session.topic_summary = derive_topic_summary([item.content for item in session.recent_messages] + session.compacted_facts)
        open_loops = [f"{item.sender_name}: {item.content}" for item in session.recent_messages if "?" in item.content]
        session.open_loops = open_loops[-4:]

    def _ensure_initial_engagement_delay(self, session: ConversationSession, now) -> None:
        if session.engagement_committed or session.engagement_delay_until is not None:
            return
        delay_seconds = random.uniform(
            self._config.initial_engagement_delay_min_seconds,
            self._config.initial_engagement_delay_max_seconds,
        )
        session.engagement_delay_until = now + timedelta(seconds=delay_seconds)
        self._logger.info(
            "context.engagement_delay_scheduled",
            extra={
                "event": "context.engagement_delay_scheduled",
                "context": {
                    "session_id": session.session_id,
                    "chat_id": session.chat_id,
                    "trigger_message_id": session.latest_trigger_message_id,
                    "delay_seconds": round(delay_seconds, 3),
                    "ready_at": session.engagement_delay_until.isoformat(),
                    "pending_message_count": len(session.pending_surfaced_messages),
                },
            },
        )

    def _idle_timeout_bounds(self) -> tuple[float, float]:
        return self._config.idle_timeout_bounds()

    def _random_idle_timeout_seconds(self) -> float:
        minimum, maximum = self._idle_timeout_bounds()
        if maximum <= minimum:
            return minimum
        return random.uniform(minimum, maximum)

    def _refresh_idle_expiry(
        self,
        session: ConversationSession,
        *,
        trigger_message_id: int | None,
        reason: str,
        now=None,
    ) -> None:
        now = now or utc_now()
        delay_seconds = self._random_idle_timeout_seconds()
        idle_expire_at = now + timedelta(seconds=delay_seconds)
        session.idle_expire_at = idle_expire_at
        self._scheduler.schedule_after(
            f"context_expire:{session.session_id}",
            delay_seconds,
            self._expire_session,
            session.session_id,
        )
        self._logger.info(
            "context.idle_timeout_scheduled",
            extra={
                "event": "context.idle_timeout_scheduled",
                "context": {
                    "session_id": session.session_id,
                    "chat_id": session.chat_id,
                    "trigger_message_id": trigger_message_id or session.latest_trigger_message_id,
                    "reason": reason,
                    "delay_seconds": round(delay_seconds, 3),
                    "idle_expire_at": idle_expire_at.isoformat(),
                },
            },
        )

    def _active_typing_until(self, session: ConversationSession, now):
        session.typing_until_by_sender = {
            sender_id: expires_at
            for sender_id, expires_at in session.typing_until_by_sender.items()
            if expires_at > now
        }
        return max(
            (
                expires_at
                for sender_id, expires_at in session.typing_until_by_sender.items()
                if sender_id in session.engaged_user_ids
            ),
            default=None,
        )

    def _session_idle_expired(self, session: ConversationSession, now) -> bool:
        if session.idle_expire_at is None or session.idle_expire_at > now:
            return False
        if session.frame_in_flight:
            return False
        return self._active_typing_until(session, now) is None

    def _sync_context_state(self, session: ConversationSession, *, engaged_at=None) -> None:
        prior_state = self._state_store.snapshot()
        was_engaged = prior_state.active_chat_id is not None and prior_state.active_session_id is not None
        if session.engagement_committed:
            payload = {
                "active_chat_id": session.chat_id,
                "active_session_id": session.session_id,
                "pending_chat_id": None,
                "pending_session_id": None,
                "conversation_engaged_user_ids": sorted(session.engaged_user_ids),
                "pending_engaged_user_ids": [],
            }
            if engaged_at is not None:
                payload["last_engagement_at"] = engaged_at
            self._state_store.update_context_state(
                **payload,
            )
            if (not was_engaged) or prior_state.active_session_id != session.session_id or str(prior_state.active_chat_id) != str(session.chat_id):
                EventBus.emit(
                    PresenceStateChangedEvent(
                        chat_id=session.chat_id,
                        payload=PresenceStateChangedPayload(
                            online=True,
                            changed_at=utc_now(),
                            reason="conversation_engaged",
                            session_id=session.session_id,
                            trigger_message_id=session.latest_trigger_message_id,
                        ),
                    )
                )
            return
        self._state_store.update_context_state(
            active_chat_id=None,
            active_session_id=None,
            pending_chat_id=session.chat_id,
            pending_session_id=session.session_id,
            conversation_engaged_user_ids=[],
            pending_engaged_user_ids=sorted(session.engaged_user_ids),
        )
        if was_engaged:
            EventBus.emit(
                PresenceStateChangedEvent(
                    chat_id=session.chat_id,
                    payload=PresenceStateChangedPayload(
                        online=False,
                        changed_at=utc_now(),
                        reason="no_engaged_conversation",
                        session_id=session.session_id,
                        trigger_message_id=session.latest_trigger_message_id,
                    ),
                )
            )

    def _schedule_finalize(self, session: ConversationSession) -> None:
        key = f"context_finalize:{session.session_id}"
        if not session.pending_surfaced_messages or session.frame_in_flight:
            self._scheduler.cancel(key)
            return
        finalize_at = self._next_finalize_at(session)
        if finalize_at is None:
            self._scheduler.cancel(key)
            return
        delay_seconds = max((finalize_at - utc_now()).total_seconds(), 0.0)
        self._scheduler.schedule_after(key, delay_seconds, self._finalize_session, session.session_id)

    def _next_finalize_at(self, session: ConversationSession):
        if not session.pending_surfaced_messages or session.pending_first_surfaced_at is None:
            return None
        surfaced_at = session.pending_latest_surfaced_at or session.pending_first_surfaced_at
        finalize_at = surfaced_at + timedelta(seconds=self._config.debounce_seconds)
        return finalize_at

    def _visible_read_not_before(self, session: ConversationSession):
        not_before = session.read_cooldown_until
        if not session.engagement_committed and session.engagement_delay_until is not None:
            if not_before is None or session.engagement_delay_until > not_before:
                not_before = session.engagement_delay_until
        return not_before

    def _read_delay_seconds(self, messages: list[ContextFrameMessagePayload]) -> float:
        char_count = max(sum(len(message.content.replace(" ", "")) for message in messages), 1)
        word_estimate = max(char_count / 5.0, 1.0)
        length_ratio = min(word_estimate / 100.0, 1.0)
        wpm = 500.0 - (200.0 * length_ratio)
        return max((word_estimate / wpm) * 60.0, 0.15)

    def _finalize_session(self, session_id: str) -> None:
        with emitter_context("context"):
            if self._active_session is None or self._active_session.session_id != session_id:
                return
            session = self._active_session
            if session.frame_in_flight or not session.pending_surfaced_messages:
                return
            finalize_at = self._next_finalize_at(session)
            if finalize_at is not None and finalize_at > utc_now():
                self._schedule_finalize(session)
                return
            active_typing_until = self._active_typing_until(session, utc_now())
            if active_typing_until is not None:
                delay_seconds = max((active_typing_until - utc_now()).total_seconds(), 0.25)
                self._logger.info(
                    "context.finalize_deferred",
                    extra={
                        "event": "context.finalize_deferred",
                        "context": {
                            "session_id": session.session_id,
                            "chat_id": session.chat_id,
                            "reason": "engaged_participant_typing",
                            "delay_seconds": round(delay_seconds, 3),
                            "deferred_until": active_typing_until.isoformat(),
                        },
                    },
                )
                self._scheduler.schedule_after(
                    f"context_finalize:{session.session_id}",
                    delay_seconds,
                    self._finalize_session,
                    session.session_id,
                )
                return
            self._emit_pending_frame(session)

    def _emit_pending_frame(self, session: ConversationSession) -> None:
        pending_messages = sorted(session.pending_surfaced_messages.values(), key=lambda item: (item.timestamp, item.message_id))
        if not pending_messages:
            return
        self._scheduler.cancel(f"context_finalize:{session.session_id}")
        surfaced_message_ids = [item.message_id for item in pending_messages]
        surfaced_until_message_id = max(surfaced_message_ids)
        read_through_message_id = max(surfaced_until_message_id, session.pending_read_through_message_id or surfaced_until_message_id)
        now = utc_now()
        visible_read_not_before = self._visible_read_not_before(session)
        remaining_visible_delay_seconds = 0.0
        if visible_read_not_before is not None:
            remaining_visible_delay_seconds = max((visible_read_not_before - now).total_seconds(), 0.0)
        if remaining_visible_delay_seconds > 0:
            self._logger.info(
                "context.visible_read_deferred",
                extra={
                    "event": "context.visible_read_deferred",
                    "context": {
                        "session_id": session.session_id,
                        "chat_id": session.chat_id,
                        "trigger_message_id": session.latest_trigger_message_id or surfaced_until_message_id,
                        "visible_read_not_before": visible_read_not_before.isoformat(),
                        "remaining_delay_seconds": round(remaining_visible_delay_seconds, 3),
                        "surfaced_message_ids": surfaced_message_ids,
                    },
                },
            )
        read_delay_seconds = self._read_delay_seconds(pending_messages)
        session.read_cooldown_until = now + timedelta(seconds=read_delay_seconds)
        self._logger.info(
            "context.read_cooldown_scheduled",
            extra={
                "event": "context.read_cooldown_scheduled",
                "context": {
                    "session_id": session.session_id,
                    "chat_id": session.chat_id,
                    "trigger_message_id": session.latest_trigger_message_id or surfaced_until_message_id,
                    "surfaced_message_ids": surfaced_message_ids,
                    "read_delay_seconds": round(read_delay_seconds, 3),
                    "read_cooldown_until": session.read_cooldown_until.isoformat(),
                },
            },
        )
        session.pending_surfaced_messages.clear()
        session.pending_first_surfaced_at = None
        session.pending_latest_surfaced_at = None
        session.pending_read_through_message_id = None
        session.frame_in_flight = True
        frame = self._build_frame_event(
            session,
            frame_created_at=now,
            visible_read_not_before=visible_read_not_before,
            visible_surfaced_message_ids=surfaced_message_ids,
            visible_surfaced_until_message_id=surfaced_until_message_id,
            visible_read_through_message_id=read_through_message_id,
        )
        EventBus.emit(
            MessageReadEvent(
                correlation_id=frame.correlation_id,
                chat_id=session.chat_id,
                payload=MessageReadPayload(
                    chat_id=session.chat_id,
                    session_id=session.session_id,
                    trigger_message_id=session.latest_trigger_message_id or surfaced_until_message_id,
                    surfaced_message_ids=surfaced_message_ids,
                    surfaced_until_message_id=surfaced_until_message_id,
                    read_through_message_id=read_through_message_id,
                    mark_seen=False,
                    visible_not_before=visible_read_not_before,
                ),
            )
        )
        EventBus.emit(frame)

    def _build_frame_event(
        self,
        session: ConversationSession,
        *,
        frame_created_at=None,
        visible_read_not_before=None,
        visible_surfaced_message_ids: list[int] | None = None,
        visible_surfaced_until_message_id: int | None = None,
        visible_read_through_message_id: int | None = None,
    ) -> ContextFrameReadyEvent:
        if not session.recent_messages:
            raise RuntimeError("Cannot build a frame without recent messages.")
        state = self._state_store.snapshot()
        current_message = self._current_message_for_frame(session)
        open_questions = self._open_questions_for_frame(state, session.chat_id, current_message.sender_id)
        trigger_message_id = session.latest_trigger_message_id or current_message.message_id
        pending_interruption = self._pending_interruption_payload(session.chat_id, session.session_id)
        conversation_window_messages = self._conversation_window_messages(
            session.chat_id,
            trigger_message_id,
        )
        codex_followup = self._codex_followup_for_frame(
            state,
            session.chat_id,
            current_message,
            conversation_window_messages,
        )
        fatigue_notice_text = None if self._config.disable_sleep_state else fatigue_notice(state, self._timezone_name)
        return ContextFrameReadyEvent(
            chat_id=session.chat_id,
            payload=ContextFramePayload(
                session_id=session.session_id,
                chat_id=session.chat_id,
                trigger_message_id=trigger_message_id,
                current_message=current_message,
                recent_messages=list(session.recent_messages),
                conversation_window_messages=conversation_window_messages,
                topic_summary=session.topic_summary,
                open_loops=list(session.open_loops),
                participants=[session.participant_names[key] for key in sorted(session.participant_names)],
                relevant_memories=list(session.memory_cards.values())[:6],
                attention_classification=session.attention_classification,
                mood=state.mood,
                fatigue_notice=fatigue_notice_text,
                recommended_reply_candidate=session.recommended_reply_candidate,
                response_required=session.response_required,
                response_required_reason=session.response_required_reason,
                engaged_user_ids=sorted(session.engaged_user_ids),
                compacted_facts=list(session.compacted_facts),
                expanded_memory_ids=sorted(session.expanded_memory_ids),
                pending_interruption=pending_interruption,
                open_question=open_questions[0] if len(open_questions) == 1 else None,
                open_questions=open_questions,
                codex_followup=codex_followup,
                frame_created_at=frame_created_at,
                visible_read_not_before=visible_read_not_before,
                visible_surfaced_message_ids=list(visible_surfaced_message_ids or []),
                visible_surfaced_until_message_id=visible_surfaced_until_message_id,
                visible_read_through_message_id=visible_read_through_message_id,
            ),
        )

    def _current_message_for_frame(self, session: ConversationSession) -> ContextFrameMessagePayload:
        trigger_message_id = session.latest_trigger_message_id
        if trigger_message_id is None:
            return session.recent_messages[-1]
        for message in session.recent_messages:
            if message.message_id == trigger_message_id:
                return message
        archived = self._message_archive.get(session.chat_id, trigger_message_id)
        if archived is not None:
            return self._convert_archived_message(archived, source="conversation_window")
        return session.recent_messages[-1]

    def _conversation_window_messages(
        self,
        chat_id: int | str,
        trigger_message_id: int,
    ) -> list[ContextFrameMessagePayload]:
        window = self._message_archive.window_around_message(
            chat_id,
            trigger_message_id,
            before=self._config.conversation_window_before,
            after=self._config.conversation_window_after,
        )
        return [self._convert_archived_message(message, source="conversation_window") for message in window]

    def _codex_followup_for_frame(
        self,
        state: GlobalState,
        chat_id: int | str,
        current_message: ContextFrameMessagePayload,
        conversation_window: list[ContextFrameMessagePayload],
    ) -> CodexFollowupFramePayload | None:
        # Preserve task continuity even when the newest parameter is not itself a reply.
        candidates = [current_message, *reversed(conversation_window)]
        seen_message_ids: set[int] = set()
        for message in candidates:
            if message.message_id in seen_message_ids or message.is_self or message.reply_to_message_id is None:
                continue
            seen_message_ids.add(message.message_id)
            matching_tasks = [
                item
                for item in state.codex_tasks.values()
                if any(
                    str(link.chat_id) == str(chat_id) and link.message_id == message.reply_to_message_id
                    for link in item.outbound_messages
                )
            ]
            if not matching_tasks:
                continue
            task = max(matching_tasks, key=lambda item: item.updated_at)
            return CodexFollowupFramePayload(
                app_server_id=task.app_server_id,
                task_id=task.task_id,
                codex_thread_id=task.thread_id,
                codex_turn_id=task.turn_id,
                status=task.status,
                linked_message_id=message.reply_to_message_id,
            )
        return None

    def _pending_interruption_payload(
        self,
        chat_id: int | str,
        session_id: str | None,
    ) -> PendingInterruptionPayload | None:
        interruption = self._state_store.take_pending_interruption(
            chat_id=chat_id,
            session_id=session_id,
        )
        if interruption is None:
            return None
        return PendingInterruptionPayload(
            original_trigger_message_id=interruption.original_trigger_message_id,
            original_reply_to_message_id=interruption.original_reply_to_message_id,
            interrupting_message_id=interruption.interrupting_message_id,
            reply_target_sender_id=interruption.reply_target_sender_id,
            reply_target_sender_name=interruption.reply_target_sender_name,
            sent_reply_chunks=list(interruption.sent_reply_chunks),
            remaining_reply_chunks=list(interruption.remaining_reply_chunks),
        )

    def _open_question_payload(self, question: OpenQuestion | None) -> OpenQuestionPayload | None:
        if question is None:
            return None
        return OpenQuestionPayload(
            app_server_id=question.app_server_id,
            task_id=question.task_id,
            tool_call_id=question.tool_call_id,
            questions=list(question.questions),
            task_description=question.task_description,
            context=dict(question.context),
            candidate_people=[
                {
                    "sender_id": candidate.sender_id,
                    "chat_id": candidate.chat_id,
                    "display_name": candidate.display_name,
                    "known_aliases": list(candidate.known_aliases),
                    "expertise_tags": list(candidate.expertise_tags),
                    "project_owner_tags": list(candidate.project_owner_tags),
                }
                for candidate in question.candidate_people
            ],
            selected_sender_id=question.sender_id,
            selected_sender_name=question.sender_name,
            user_replies=list(question.user_replies),
        )

    def _open_questions_for_frame(
        self,
        state,
        chat_id: int | str,
        sender_id: str,
    ) -> list[OpenQuestionPayload]:
        questions = [
            question
            for question in state.open_questions.values()
            if str(question.chat_id) == str(chat_id) and str(question.sender_id) == str(sender_id)
        ]
        return [
            payload
            for question in questions
            if (payload := self._open_question_payload(question)) is not None
        ]

    def _codex_key(self, app_server_id: str, task_id: str, tool_call_id: str) -> str:
        return f"{app_server_id}:{task_id}:{tool_call_id}"

    def _emit_frame(self, session: ConversationSession) -> None:
        if not session.recent_messages:
            return
        EventBus.emit(self._build_frame_event(session))

    def _disengage_session(
        self,
        session: ConversationSession,
        *,
        correlation_id: str | None,
        trigger_message_id: int | None,
        reason: str,
    ) -> None:
        latest_message_id = self._message_archive.latest_message_id(session.chat_id)
        pending_messages = sorted(session.pending_surfaced_messages.values(), key=lambda item: (item.timestamp, item.message_id))
        read_through_message_id = None

        # avoid acknowledging a surfaced session that ended because amber went to sleep
        state = self._state_store.snapshot()
        should_mark_seen = self._config.disable_sleep_state or state.sleep_state == "awake"
        if should_mark_seen and (pending_messages or latest_message_id is not None):
            surfaced_message_ids = [item.message_id for item in pending_messages]
            surfaced_until_message_id = max(surfaced_message_ids) if surfaced_message_ids else None
            read_through_message_id = max(
                latest_message_id or 0,
                surfaced_until_message_id or 0,
                session.pending_read_through_message_id or 0,
            )
            EventBus.emit(
                MessageReadEvent(
                    chat_id=session.chat_id,
                    payload=MessageReadPayload(
                        chat_id=session.chat_id,
                        session_id=session.session_id,
                        trigger_message_id=trigger_message_id or session.latest_trigger_message_id or surfaced_until_message_id,
                        surfaced_message_ids=surfaced_message_ids,
                        surfaced_until_message_id=surfaced_until_message_id,
                        read_through_message_id=read_through_message_id,
                        mark_seen=True,
                    ),
                    **({"correlation_id": correlation_id} if correlation_id is not None else {}),
                )
            )
        self._logger.info(
            "context.disengaged",
            extra={
                "event": "context.disengaged",
                "context": {
                    "session_id": session.session_id,
                    "chat_id": session.chat_id,
                    "trigger_message_id": trigger_message_id or session.latest_trigger_message_id,
                    "reason": reason,
                    "engagement_committed": session.engagement_committed,
                    "pending_message_count": len(pending_messages),
                    "read_through_message_id": read_through_message_id,
                },
            },
        )
        self._scheduler.cancel(f"context_finalize:{session.session_id}")
        self._scheduler.cancel(f"context_expire:{session.session_id}")
        if session.engagement_committed:
            EventBus.emit(
                PresenceStateChangedEvent(
                    chat_id=session.chat_id,
                    payload=PresenceStateChangedPayload(
                        online=False,
                        changed_at=utc_now(),
                        reason=reason,
                        session_id=session.session_id,
                        trigger_message_id=trigger_message_id or session.latest_trigger_message_id,
                    ),
                )
            )
        session.pending_surfaced_messages.clear()
        session.pending_first_surfaced_at = None
        session.pending_latest_surfaced_at = None
        session.engagement_pending_since = None
        session.engagement_delay_until = None
        session.engagement_committed = False
        session.pending_read_through_message_id = None
        session.idle_expire_at = None
        session.frame_in_flight = False
        self._active_session = None
        self._state_store.clear_session()

    def _expire_session(self, session_id: str) -> None:
        with emitter_context("context"):
            if self._active_session is None or self._active_session.session_id != session_id:
                return
            if self._active_session.frame_in_flight:
                self._logger.info(
                    "context.idle_timeout_deferred",
                    extra={
                        "event": "context.idle_timeout_deferred",
                        "context": {
                            "session_id": session_id,
                            "chat_id": self._active_session.chat_id,
                            "reason": "frame_in_flight",
                            "delay_seconds": 1.0,
                        },
                    },
                )
                self._scheduler.schedule_after(
                    f"context_expire:{session_id}",
                    1.0,
                    self._expire_session,
                    session_id,
                )
                return
            now = utc_now()
            active_typing_until = self._active_typing_until(self._active_session, now)
            if active_typing_until is not None:
                delay_seconds = max((active_typing_until - now).total_seconds(), 1.0)
                self._logger.info(
                    "context.idle_timeout_deferred",
                    extra={
                        "event": "context.idle_timeout_deferred",
                        "context": {
                            "session_id": session_id,
                            "chat_id": self._active_session.chat_id,
                            "reason": "engaged_participant_typing",
                            "delay_seconds": round(delay_seconds, 3),
                            "deferred_until": active_typing_until.isoformat(),
                        },
                    },
                )
                self._scheduler.schedule_after(
                    f"context_expire:{session_id}",
                    delay_seconds,
                    self._expire_session,
                    session_id,
                )
                return
            idle_expire_at = self._active_session.idle_expire_at
            if idle_expire_at is None:
                self._refresh_idle_expiry(
                    self._active_session,
                    trigger_message_id=self._active_session.latest_trigger_message_id,
                    reason="missing_deadline_recovery",
                    now=now,
                )
                return
            if idle_expire_at > now:
                delay_seconds = max((idle_expire_at - now).total_seconds(), 0.0)
                self._scheduler.schedule_after(
                    f"context_expire:{session_id}",
                    delay_seconds,
                    self._expire_session,
                    session_id,
                )
                return
            session = self._active_session
            self._disengage_session(
                session,
                correlation_id=None,
                trigger_message_id=session.latest_trigger_message_id,
                reason="idle_timeout",
            )

    def _participant_name(self, session: ConversationSession, sender_id: str) -> str:
        if sender_id in session.participant_names:
            return session.participant_names[sender_id]
        for message in reversed(session.recent_messages):
            if message.sender_id == sender_id:
                return message.sender_name
        archived = self._message_archive.get(session.chat_id, session.latest_trigger_message_id or 0)
        if archived is not None and archived.sender.id == sender_id:
            return archived.sender.name
        return sender_id

    def _is_self_sender(self, sender_id: str) -> bool:
        return sender_id == "amber-self"
