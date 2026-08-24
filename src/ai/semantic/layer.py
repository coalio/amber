from __future__ import annotations

import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from src.ai.config import AIConfig
from src.ai.semantic.client import SemanticClient
from src.ai.semantic.schema import InterruptionDecisionSchema, SemanticDecisionSchema
from src.events.ai import SemanticDecisionMadeEvent, SemanticDecisionPayload
from src.events.bus import EventBus, emitter_context
from src.events.context import ContextFramePayload, ContextFrameReadyEvent
from src.utils.logging import get_logger, redact_sensitive_text


@dataclass(frozen=True)
class HarnessFailure:
    code: str
    reason: str
    context: dict[str, Any]
    user_reply: str | None = None


class ConsciousHarness:
    def __init__(self, config: AIConfig) -> None:
        self._config = config

    def evaluate(self, frame: ContextFramePayload, decision: SemanticDecisionSchema) -> HarnessFailure | None:
        if decision.work_intent == "delegate" and not decision.codex_work_dispatched:
            blocker = (decision.codex_work_error or "").strip()
            blocker_code = (decision.codex_work_error_code or "").strip()
            if blocker:
                reason = (
                    "The requested Codex transition failed with a verified runtime blocker"
                    f"{f' ({blocker_code})' if blocker_code else ''}: {blocker}"
                )
                if blocker_code == "tool_output_conflict":
                    user_reply = f"i couldn't apply that clarification because {blocker.rstrip('.')}"
                else:
                    user_reply = f"i couldn't complete that task because {blocker.rstrip('.')}"
            else:
                reason = (
                    "The visible conversation asks Amber to do work beyond answering, but no Codex transition "
                    "completed successfully in this turn. Resume a waiting clarification with CodexSendReply, "
                    "or start new work with CodexRunTask, before acknowledging the work."
                )
                user_reply = "i couldn't complete that task because no work process could be started or resumed"
            return HarnessFailure(
                code="delegated_work_requires_codex_dispatch",
                reason=reason,
                context={
                    "current_message": self._message_subject(frame.current_message),
                    "recent_window": self._window_context(frame, decision),
                    "codex_work_error_code": blocker_code or None,
                    "codex_work_error": blocker or None,
                },
                user_reply=user_reply,
            )
        if frame.response_required and decision.action in {"ignore", "sleep"}:
            return HarnessFailure(
                code="required_response_cannot_be_silent",
                reason=(
                    "This frame is marked response_required, so Amber must reply instead of choosing "
                    f"`{decision.action}`."
                ),
                context={
                    "response_required_reason": frame.response_required_reason,
                    "current_message": self._message_subject(frame.current_message),
                    "recent_window": self._window_context(frame, decision),
                },
            )
        if decision.action != "reply":
            return None
        reply_text = (decision.reply_text or "").strip()

        # reply decisions must carry final send text
        if not reply_text:
            return HarnessFailure(
                code="reply_requires_non_empty_text",
                reason="Reply text is empty. If action is `reply`, `reply_text` must be non-empty.",
                context={
                    "reply_preview": None,
                    "reply_length": 0,
                    "recent_window": self._window_context(frame, decision),
                },
            )
        if len(reply_text) > self._config.max_reply_chars:
            return HarnessFailure(
                code="reply_too_long_for_context",
                reason=(
                    f"Reply text is too long for this context: {len(reply_text)} chars exceeds "
                    f"the max of {self._config.max_reply_chars}."
                ),
                context={
                    "reply_preview": self._preview(reply_text),
                    "reply_length": len(reply_text),
                    "max_reply_chars": self._config.max_reply_chars,
                    "recent_window": self._window_context(frame, decision),
                },
            )

        # reject repeats of the trigger
        trigger_text = frame.current_message.content.lower()
        trigger_similarity = SequenceMatcher(a=trigger_text, b=reply_text.lower()).ratio() if trigger_text else 0.0
        if trigger_text and trigger_similarity > 0.92:
            trigger_subject = self._message_subject(frame.current_message, similarity=trigger_similarity)
            return HarnessFailure(
                code="reply_mirrors_trigger_message",
                reason=(
                    f"Reply text is too similar to the triggering message {frame.current_message.message_id} "
                    f"from {frame.current_message.sender_name} (similarity {trigger_similarity:.3f}): "
                    f"\"{self._preview(frame.current_message.content)}\""
                ),
                context={
                    "reply_preview": self._preview(reply_text),
                    "reply_length": len(reply_text),
                    "offending_messages": [trigger_subject],
                    "recent_window": self._window_context(frame, decision),
                    "similarity_threshold": 0.92,
                },
            )

        # reject near-copies of visible messages
        visible_messages = self._visible_window_messages(frame, decision)
        offending_messages = [
            self._message_subject(message, similarity=similarity)
            for message in visible_messages
            if (similarity := SequenceMatcher(a=message.content.lower(), b=reply_text.lower()).ratio()) > 0.9
        ]
        if offending_messages:
            primary = max(offending_messages, key=lambda item: float(item["similarity"]))
            return HarnessFailure(
                code="reply_too_similar_to_recent_message",
                reason=(
                    f"Reply text is too similar to recent message {primary['message_id']} from "
                    f"{primary['sender_name']} (similarity {primary['similarity']:.3f}): "
                    f"\"{primary['content_preview']}\""
                ),
                context={
                    "reply_preview": self._preview(reply_text),
                    "reply_length": len(reply_text),
                    "offending_messages": offending_messages,
                    "recent_window": self._window_context(frame, decision),
                    "similarity_threshold": 0.9,
                },
            )
        return None

    def evaluate_interruption(
        self,
        frame: ContextFramePayload,
        interruption_decision: InterruptionDecisionSchema,
        semantic_decision: SemanticDecisionSchema,
    ) -> HarnessFailure | None:
        failure = self.evaluate(frame, semantic_decision)
        if failure is not None:
            return failure
        if interruption_decision.interrupt_decision != "accept" or semantic_decision.action != "reply":
            return None
        pending = frame.pending_interruption
        if pending is None:
            return None
        normalized_reply = " ".join((semantic_decision.reply_text or "").lower().split())
        if not normalized_reply:
            return None

        # accepted interruptions need fresh text
        offending_chunks: list[dict[str, object]] = []
        for chunk in pending.remaining_reply_chunks:
            normalized_chunk = " ".join(chunk.lower().split())
            if not normalized_chunk:
                continue
            similarity = SequenceMatcher(a=normalized_chunk, b=normalized_reply).ratio()
            if similarity > 0.88 or normalized_chunk in normalized_reply:
                offending_chunks.append(
                    {
                        "chunk_preview": self._preview(chunk),
                        "similarity": round(similarity, 3),
                    }
                )
        if not offending_chunks:
            return None
        primary = max(offending_chunks, key=lambda item: float(item["similarity"]))
        return HarnessFailure(
            code="accepted_interruption_reuses_unsent_plan",
            reason=(
                "Accepted interruption reply still reuses the abandoned unsent plan too closely "
                f"(similarity {primary['similarity']:.3f}): \"{primary['chunk_preview']}\""
            ),
            context={
                "reply_preview": self._preview(semantic_decision.reply_text or ""),
                "reply_length": len((semantic_decision.reply_text or "").strip()),
                "offending_remaining_chunks": offending_chunks,
                "remaining_reply_chunks": [self._preview(chunk) for chunk in pending.remaining_reply_chunks],
                "recent_window": self._window_context(frame),
                "similarity_threshold": 0.88,
            },
        )

    def _window_context(
        self,
        frame: ContextFramePayload,
        decision: SemanticDecisionSchema | None = None,
    ) -> list[dict[str, object]]:
        return [self._message_subject(message) for message in self._visible_window_messages(frame, decision)]

    def _visible_window_messages(
        self,
        frame: ContextFramePayload,
        decision: SemanticDecisionSchema | None = None,
    ) -> list:
        notification = frame.codex_notification
        if notification is not None and notification.candidate_conversations:
            selected = None
            if decision is not None:
                selected = next(
                    (
                        conversation
                        for conversation in notification.candidate_conversations
                        if conversation.sender_id == (decision.codex_target_sender_id or "")
                        or str(conversation.chat_id) == str(decision.chat_id)
                    ),
                    None,
                )
            if selected is None and len(notification.candidate_conversations) == 1:
                selected = notification.candidate_conversations[0]
            if selected is not None:
                return selected.recent_messages
        return frame.conversation_window_messages or frame.recent_messages

    def _message_subject(
        self,
        message,
        *,
        similarity: float | None = None,
    ) -> dict[str, object]:
        subject: dict[str, object] = {
            "message_id": message.message_id,
            "sender_name": message.sender_name,
            "content_preview": self._preview(message.content),
        }
        if similarity is not None:
            subject["similarity"] = round(similarity, 3)
        return subject

    def _preview(self, text: str, *, limit: int = 120) -> str:
        compact = " ".join(redact_sensitive_text(text).split())
        if len(compact) <= limit:
            return compact
        return f"{compact[: limit - 3]}..."


class AILayer:
    _logger = get_logger("amber.ai.semantic")

    def __init__(self, config: AIConfig, semantic_client: SemanticClient) -> None:
        self._config = config
        self._semantic_client = semantic_client
        self._harness = ConsciousHarness(config)
        EventBus.subscribe("ContextFrameReadyEvent", self.handle_frame)

    def handle_frame(self, event: ContextFrameReadyEvent) -> None:
        with emitter_context("ai"):
            decision = self._call_with_harness(event.payload)
            EventBus.emit(
                SemanticDecisionMadeEvent(
                    correlation_id=event.correlation_id,
                    chat_id=event.chat_id,
                    payload=SemanticDecisionPayload.model_validate(decision.model_dump()),
                )
            )

    def _call_with_harness(self, frame: ContextFramePayload) -> SemanticDecisionSchema:
        if frame.pending_interruption is not None:
            return self._call_interruption_with_harness(frame)
        try:
            started = time.perf_counter()
            first_pass = self._semantic_client.decide(frame)
            self._log_request_timing(frame, phase="first_pass", duration_seconds=time.perf_counter() - started)
        except Exception as exc:
            self._log_semantic_exception(frame, exc, phase="first_pass")
            return self._fallback_decision(frame, [f"semantic_error:{exc.__class__.__name__}"])
        decision = first_pass
        failure = self._harness.evaluate(frame, decision)
        if failure is None:
            return self._normalize_decision(frame, first_pass)
        notes = [failure.reason]
        for retry_index in range(1, self._config.semantic_retry_budget + 1):
            try:
                started = time.perf_counter()
                decision = self._semantic_client.decide(
                    frame,
                    harness_feedback=self._build_retry_feedback(failure, retry_index),
                    previous_decision=decision,
                )
                self._log_request_timing(
                    frame,
                    phase="retry",
                    duration_seconds=time.perf_counter() - started,
                    retry_index=retry_index,
                )
            except Exception as exc:
                self._log_semantic_exception(frame, exc, phase="retry", retry_index=retry_index)
                return self._fallback_decision(frame, [*notes, "retry_failed"], failure=failure)
            failure = self._harness.evaluate(frame, decision)
            if failure is None:
                return self._normalize_decision(frame, decision)
            notes.append(failure.reason)
        return self._fallback_decision(frame, notes, failure=failure)

    def _call_interruption_with_harness(self, frame: ContextFramePayload) -> SemanticDecisionSchema:
        interruption = frame.pending_interruption
        if interruption is None:
            return self._fallback_decision(frame, ["missing_pending_interruption"])
        try:
            started = time.perf_counter()
            first_pass = self._semantic_client.decide_interruption(frame, interruption)
            self._log_request_timing(frame, phase="interruption_first_pass", duration_seconds=time.perf_counter() - started)
        except Exception as exc:
            self._log_semantic_exception(frame, exc, phase="interruption_first_pass")
            return self._fallback_decision(frame, [f"interruption_error:{exc.__class__.__name__}"])
        decision = first_pass
        semantic_decision = self._normalize_interruption_result(frame, decision)
        failure = self._harness.evaluate_interruption(frame, decision, semantic_decision)
        if failure is None:
            return self._normalize_decision(frame, semantic_decision)
        notes = [decision.reason, *decision.notes, failure.reason]
        for retry_index in range(1, self._config.semantic_retry_budget + 1):
            try:
                started = time.perf_counter()
                decision = self._semantic_client.decide_interruption(
                    frame,
                    interruption,
                    harness_feedback=self._build_retry_feedback(failure, retry_index),
                    previous_decision=decision,
                )
                self._log_request_timing(
                    frame,
                    phase="interruption_retry",
                    duration_seconds=time.perf_counter() - started,
                    retry_index=retry_index,
                )
            except Exception as exc:
                self._log_semantic_exception(frame, exc, phase="interruption_retry", retry_index=retry_index)
                return self._fallback_decision(
                    frame,
                    [*notes, "interruption_retry_failed"],
                    failure=failure,
                )
            semantic_decision = self._normalize_interruption_result(frame, decision)
            failure = self._harness.evaluate_interruption(frame, decision, semantic_decision)
            if failure is None:
                return self._normalize_decision(frame, semantic_decision)
            notes.append(failure.reason)
        return self._fallback_decision(frame, notes, failure=failure)

    def _build_retry_feedback(self, failure: HarnessFailure, retry_index: int) -> dict[str, Any]:
        return {
            "code": failure.code,
            "reason": failure.reason,
            "retry_attempt": retry_index,
            "retries_remaining": max(self._config.semantic_retry_budget - retry_index, 0),
            **failure.context,
        }

    def _normalize_decision(self, frame: ContextFramePayload, decision: SemanticDecisionSchema) -> SemanticDecisionSchema:
        visible_memories = {memory.memory_id: memory for memory in frame.relevant_memories}

        # settle reply target before delivery
        if decision.action == "reply":
            recommended_reply_target = frame.recommended_reply_candidate or frame.current_message.message_id
            if decision.reply_to_message_id is None:
                decision.reply_to_message_id = recommended_reply_target
            elif (
                frame.recommended_reply_candidate is not None
                and decision.reply_to_message_id == frame.current_message.message_id
                and frame.recommended_reply_candidate != frame.current_message.message_id
            ):
                decision.reply_to_message_id = frame.recommended_reply_candidate
        else:
            decision.reply_to_message_id = None
            decision.reply_text = None

        # memory fields must point at visible cards
        if decision.action != "expand_memory":
            decision.referenced_memory_ids = []
        if decision.action == "expand_memory" and not decision.referenced_memory_ids:
            return self._fallback_decision(frame, ["expand_memory_without_ids"])
        if decision.action == "disengage":
            visible_sender_ids = {message.sender_id for message in (frame.conversation_window_messages or frame.recent_messages)}
            if decision.disengage_sender_id not in visible_sender_ids:
                decision.disengage_sender_id = frame.current_message.sender_id
            if decision.ignore_for_seconds is not None and decision.ignore_for_seconds <= 0:
                decision.ignore_for_seconds = None
            if decision.create_bad_memory:
                if decision.bad_memory_sender_id not in visible_sender_ids:
                    decision.bad_memory_sender_id = decision.disengage_sender_id or frame.current_message.sender_id
                if not decision.bad_memory_text:
                    decision.bad_memory_text = decision.disengage_reason or frame.current_message.content[:240]
            else:
                decision.bad_memory_sender_id = None
                decision.bad_memory_text = None
        else:
            decision.disengage_sender_id = None
            decision.disengage_reason = None
            decision.ignore_for_seconds = None
            decision.create_bad_memory = False
            decision.bad_memory_sender_id = None
            decision.bad_memory_text = None
        if decision.action == "expand_memory":
            decision.memory_mutation = "none"
        if decision.memory_mutation != "none":
            target_memory = visible_memories.get(decision.target_memory_id or "")
            if target_memory is None:
                decision.memory_mutation = "none"
            else:
                decision.target_memory_id = target_memory.memory_id
                if target_memory.owner_sender_id is not None:
                    decision.target_memory_sender_id = target_memory.owner_sender_id
                if decision.memory_mutation == "rewrite":
                    decision.rewritten_memory_text = (decision.rewritten_memory_text or target_memory.text).strip()
                    decision.rewritten_memory_tags = list(decision.rewritten_memory_tags or target_memory.tags)
                else:
                    decision.rewritten_memory_text = None
                    decision.rewritten_memory_tags = []
        if decision.memory_mutation == "none":
            decision.target_memory_id = None
            decision.target_memory_sender_id = None
            decision.rewritten_memory_text = None
            decision.rewritten_memory_tags = []

        # codex metadata routes only; amber authors replies
        open_question = self._selected_open_question(frame, decision)
        if open_question is not None:
            recovered_clarification = decision.codex_work_dispatched and decision.codex_task_started
            if recovered_clarification:
                # CodexSendReply recovered a lost process into a new task on the same thread.
                decision.codex_tool_call_id = None
            else:
                decision.codex_app_server_id = open_question.app_server_id
                decision.codex_task_id = open_question.task_id
                decision.codex_tool_call_id = open_question.tool_call_id
            candidate_by_sender = {candidate.sender_id: candidate for candidate in open_question.candidate_people}
            candidate_by_chat = {str(candidate.chat_id): candidate for candidate in open_question.candidate_people}
            selected = candidate_by_sender.get(decision.codex_target_sender_id or "") or candidate_by_chat.get(str(decision.chat_id))
            if selected is None and len(open_question.candidate_people) == 1:
                selected = open_question.candidate_people[0]
            if selected is not None:
                decision.codex_target_sender_id = selected.sender_id
                decision.codex_target_sender_name = selected.display_name
                decision.chat_id = selected.chat_id
                decision.reply_to_message_id = None
        elif frame.open_questions:
            decision.codex_target_sender_id = None
            decision.codex_target_sender_name = None
            decision.codex_app_server_id = None
            decision.codex_task_id = None
            decision.codex_tool_call_id = None
            decision.chat_id = frame.chat_id
        elif frame.codex_notification is not None:
            decision.codex_app_server_id = frame.codex_notification.app_server_id
            decision.codex_task_id = frame.codex_notification.task_id
            decision.codex_tool_call_id = None
            candidate_by_sender = {candidate.sender_id: candidate for candidate in frame.codex_notification.candidate_people}
            candidate_by_chat = {str(candidate.chat_id): candidate for candidate in frame.codex_notification.candidate_people}
            selected = candidate_by_sender.get(decision.codex_target_sender_id or "") or candidate_by_chat.get(str(decision.chat_id))
            if selected is None and len(frame.codex_notification.candidate_people) == 1:
                selected = frame.codex_notification.candidate_people[0]
            if selected is not None:
                decision.codex_target_sender_id = selected.sender_id
                decision.codex_target_sender_name = selected.display_name
                decision.chat_id = selected.chat_id
                decision.reply_to_message_id = None
        else:
            decision.codex_target_sender_id = None
            decision.codex_target_sender_name = None
            if not decision.codex_task_started:
                decision.codex_app_server_id = None
                decision.codex_task_id = None
            decision.codex_tool_call_id = None
            decision.chat_id = frame.chat_id
        if frame.linear_task_list is not None and decision.action == "reply":
            decision.action = "ignore"
            decision.reply_to_message_id = None
            decision.reply_text = None
        decision.trigger_message_id = frame.trigger_message_id
        decision.session_id = frame.session_id
        decision.frame_created_at = frame.frame_created_at
        decision.visible_read_not_before = frame.visible_read_not_before
        decision.visible_surfaced_message_ids = list(frame.visible_surfaced_message_ids)
        decision.visible_surfaced_until_message_id = frame.visible_surfaced_until_message_id
        decision.visible_read_through_message_id = frame.visible_read_through_message_id
        return decision

    def _selected_open_question(self, frame: ContextFramePayload, decision: SemanticDecisionSchema):
        questions = list(frame.open_questions)
        if frame.open_question is not None and not any(
            question.app_server_id == frame.open_question.app_server_id
            and question.task_id == frame.open_question.task_id
            and question.tool_call_id == frame.open_question.tool_call_id
            for question in questions
        ):
            questions.append(frame.open_question)
        if not questions:
            return None
        for question in questions:
            if (
                decision.codex_app_server_id == question.app_server_id
                and decision.codex_task_id == question.task_id
                and decision.codex_tool_call_id == question.tool_call_id
            ):
                return question
        if len(questions) == 1:
            return questions[0]
        return None

    def _normalize_interruption_result(
        self,
        frame: ContextFramePayload,
        decision: InterruptionDecisionSchema,
    ) -> SemanticDecisionSchema:
        self._logger.info(
            "semantic.interruption_decision",
            extra={
                "event": "semantic.interruption_decision",
                "context": {
                    "chat_id": frame.chat_id,
                    "session_id": frame.session_id,
                    "trigger_message_id": frame.trigger_message_id,
                    "interrupting_message_id": (
                        frame.pending_interruption.interrupting_message_id if frame.pending_interruption is not None else None
                    ),
                    "interrupt_decision": decision.interrupt_decision,
                    "action": decision.action,
                    "reply_to_message_id": decision.reply_to_message_id,
                    "confidence": round(decision.confidence, 3),
                    "reason": decision.reason,
                    "notes": list(decision.notes),
                },
            },
        )
        notes = [f"interrupt_{decision.interrupt_decision}", decision.reason, *decision.notes]
        return SemanticDecisionSchema(
            action=decision.action,
            work_intent=decision.work_intent,
            codex_work_dispatched=decision.codex_work_dispatched,
            codex_task_started=decision.codex_task_started,
            codex_work_error_code=decision.codex_work_error_code,
            codex_work_error=decision.codex_work_error,
            reply_to_message_id=decision.reply_to_message_id,
            chat_id=frame.chat_id,
            reply_text=decision.reply_text,
            referenced_memory_ids=list(decision.referenced_memory_ids),
            confidence=decision.confidence,
            notes=notes,
            trigger_message_id=frame.trigger_message_id,
            session_id=frame.session_id,
            disengage_sender_id=decision.disengage_sender_id,
            disengage_reason=decision.disengage_reason,
            ignore_for_seconds=decision.ignore_for_seconds,
            create_bad_memory=decision.create_bad_memory,
            bad_memory_sender_id=decision.bad_memory_sender_id,
            bad_memory_text=decision.bad_memory_text,
            memory_mutation=decision.memory_mutation,
            target_memory_id=decision.target_memory_id,
            target_memory_sender_id=decision.target_memory_sender_id,
            rewritten_memory_text=decision.rewritten_memory_text,
            rewritten_memory_tags=list(decision.rewritten_memory_tags),
            codex_app_server_id=decision.codex_app_server_id,
            codex_task_id=decision.codex_task_id,
            frame_created_at=frame.frame_created_at,
            visible_read_not_before=frame.visible_read_not_before,
            visible_surfaced_message_ids=list(frame.visible_surfaced_message_ids),
            visible_surfaced_until_message_id=frame.visible_surfaced_until_message_id,
            visible_read_through_message_id=frame.visible_read_through_message_id,
        )

    def _fallback_decision(
        self,
        frame: ContextFramePayload,
        notes: list[str],
        *,
        failure: HarnessFailure | None = None,
    ) -> SemanticDecisionSchema:
        blocker_reply = failure.user_reply if failure is not None else None
        if frame.response_required or blocker_reply is not None:
            fallback_notes = list(notes)
            if frame.response_required:
                fallback_notes.append("required_response_fallback")
            if blocker_reply is not None:
                fallback_notes.append("codex_work_failure_fallback")
            if failure is not None:
                fallback_notes.append(f"harness_failure:{failure.code}")
            return SemanticDecisionSchema(
                action="reply",
                work_intent="delegate" if blocker_reply is not None else "none",
                codex_work_error_code=(
                    str(failure.context.get("codex_work_error_code") or "") or None
                    if failure is not None
                    else None
                ),
                codex_work_error=(
                    str(failure.context.get("codex_work_error") or "") or None
                    if failure is not None
                    else None
                ),
                reply_to_message_id=frame.recommended_reply_candidate or frame.current_message.message_id,
                chat_id=frame.chat_id,
                reply_text=(
                    blocker_reply
                    or "i couldn't complete that request because i couldn't produce a valid response"
                ),
                referenced_memory_ids=[],
                confidence=0.1,
                notes=fallback_notes,
                trigger_message_id=frame.trigger_message_id,
                session_id=frame.session_id,
                frame_created_at=frame.frame_created_at,
                visible_read_not_before=frame.visible_read_not_before,
                visible_surfaced_message_ids=list(frame.visible_surfaced_message_ids),
                visible_surfaced_until_message_id=frame.visible_surfaced_until_message_id,
                visible_read_through_message_id=frame.visible_read_through_message_id,
            )
        return SemanticDecisionSchema(
            action="ignore",
            reply_to_message_id=None,
            chat_id=frame.chat_id,
            reply_text=None,
            referenced_memory_ids=[],
            confidence=0.0,
            notes=notes,
            trigger_message_id=frame.trigger_message_id,
            session_id=frame.session_id,
            frame_created_at=frame.frame_created_at,
            visible_read_not_before=frame.visible_read_not_before,
            visible_surfaced_message_ids=list(frame.visible_surfaced_message_ids),
            visible_surfaced_until_message_id=frame.visible_surfaced_until_message_id,
            visible_read_through_message_id=frame.visible_read_through_message_id,
        )

    def _log_request_timing(
        self,
        frame: ContextFramePayload,
        *,
        phase: str,
        duration_seconds: float,
        retry_index: int | None = None,
    ) -> None:
        context: dict[str, object] = {
            "phase": phase,
            "chat_id": frame.chat_id,
            "session_id": frame.session_id,
            "trigger_message_id": frame.trigger_message_id,
            "current_message_id": frame.current_message.message_id,
            "duration_seconds": round(duration_seconds, 3),
            "pending_interruption": frame.pending_interruption is not None,
        }
        if retry_index is not None:
            context["retry_index"] = retry_index
        self._logger.info(
            "semantic.request_completed",
            extra={"event": "semantic.request_completed", "context": context},
        )

    def _log_semantic_exception(
        self,
        frame: ContextFramePayload,
        exc: Exception,
        *,
        phase: str,
        retry_index: int | None = None,
    ) -> None:
        context: dict[str, object] = {
            "phase": phase,
            "chat_id": frame.chat_id,
            "session_id": frame.session_id,
            "trigger_message_id": frame.trigger_message_id,
            "current_message_id": frame.current_message.message_id,
            "error_type": exc.__class__.__name__,
            "error_message": str(exc),
        }
        if retry_index is not None:
            context["retry_index"] = retry_index
        self._logger.critical(
            "semantic.decision_failed",
            extra={"event": "semantic.decision_failed", "context": context},
            exc_info=(type(exc), exc, exc.__traceback__),
        )
