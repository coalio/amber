from __future__ import annotations

from datetime import datetime

from src.events.action import (
    MessageReadEvent,
    OutboundChunkSentEvent,
    OutboundMessageSentEvent,
    PresenceStateChangedEvent,
    SleepStateChangedEvent,
)
from src.events.ai import SemanticDecisionMadeEvent
from src.events.attention import AttentionDecisionMadeEvent
from src.events.base import BaseEvent
from src.events.context import ContextFrameReadyEvent
from src.events.linear import LinearTaskListReceivedEvent
from src.events.outbound import OutboundMessagePreparedEvent
from src.events.receiver import TelegramMessagePayload, TelegramMessageReceivedEvent, TelegramTypingUpdatedEvent


_PREVIEW_LIMIT = 120
_MEMORY_ID_LOG_LIMIT = 3


def build_event_dispatch_context(event: BaseEvent, *, handler_count: int) -> dict[str, object]:
    context: dict[str, object] = {
        "event_id": event.event_id,
        "correlation_id": event.correlation_id,
        "origin": event.origin,
        "handler_count": handler_count,
        "chat_id": event.chat_id,
    }
    payload_summary = summarize_event_payload(event)
    if payload_summary:
        context["payload"] = payload_summary
    return context


def summarize_event_payload(event: BaseEvent) -> dict[str, object] | None:
    if isinstance(event, TelegramMessageReceivedEvent):
        return _telegram_message_summary(event.payload)
    if isinstance(event, TelegramTypingUpdatedEvent):
        payload = event.payload
        return {
            "sender_id": payload.sender.id,
            "sender_name": payload.sender.name,
            "active": payload.active,
            "activity": payload.activity,
            "expires_at": _isoformat(payload.expires_at),
        }
    if isinstance(event, AttentionDecisionMadeEvent):
        payload = event.payload
        return {
            "decision": payload.decision,
            "attention_score": _round_score(payload.attention_score),
            "heuristic_score": _round_score(payload.heuristic_score),
            "model_score": _round_score(payload.model_score),
            "reasons": list(payload.reasons),
            "engaged_user_bypass": payload.engaged_user_bypass,
            "memory_count": len(payload.memory_cards),
            "memory_ids": [item.memory_id for item in payload.memory_cards[:_MEMORY_ID_LOG_LIMIT]],
            "sticker_signal": payload.sticker_signal is not None,
            "classification": (
                {
                    "score": _round_score(payload.classification.score),
                    "top_labels": list(payload.classification.top_labels),
                    "tone": payload.classification.tone,
                    "model_name": payload.classification.model_name,
                }
                if payload.classification is not None
                else None
            ),
            "reply_target_candidate": payload.reply_target_candidate,
            **_telegram_message_summary(payload.message),
        }
    if isinstance(event, ContextFrameReadyEvent):
        payload = event.payload
        return {
            "session_id": payload.session_id,
            "trigger_message_id": payload.trigger_message_id,
            "current_message_id": payload.current_message.message_id,
            "current_sender_name": payload.current_message.sender_name,
            "current_content_preview": _preview(payload.current_message.content),
            "recent_message_count": len(payload.recent_messages),
            "conversation_window_count": len(payload.conversation_window_messages),
            "participant_count": len(payload.participants),
            "relevant_memory_count": len(payload.relevant_memories),
            "attention_classification_labels": (
                list(payload.attention_classification.top_labels) if payload.attention_classification is not None else []
            ),
            "expanded_memory_count": len(payload.expanded_memory_ids),
            "open_loop_count": len(payload.open_loops),
            "mood": payload.mood,
            "fatigue_notice": payload.fatigue_notice is not None,
            "recommended_reply_candidate": payload.recommended_reply_candidate,
            "pending_interruption": payload.pending_interruption is not None,
            "pending_interruption_remaining_chunk_count": (
                len(payload.pending_interruption.remaining_reply_chunks) if payload.pending_interruption is not None else 0
            ),
            "linear_task_count": len(payload.linear_task_list.tasks) if payload.linear_task_list is not None else 0,
        }
    if isinstance(event, LinearTaskListReceivedEvent):
        payload = event.payload
        return {
            "task_count": len(payload.tasks),
            "identifiers": [task.identifier for task in payload.tasks[:5]],
            "window_start_date": payload.window_start_date,
            "window_end_date": payload.window_end_date,
            "queue_hash": payload.queue_hash,
        }
    if isinstance(event, SemanticDecisionMadeEvent):
        payload = event.payload
        draft_text = payload.draft_text or ""
        return {
            "action": payload.action,
            "confidence": _round_score(payload.confidence),
            "reply_to_message_id": payload.reply_to_message_id,
            "trigger_message_id": payload.trigger_message_id,
            "session_id": payload.session_id,
            "referenced_memory_count": len(payload.referenced_memory_ids),
            "notes": list(payload.notes),
            "draft_length": len(draft_text),
            "draft_preview": _preview(draft_text),
            "disengage_sender_id": payload.disengage_sender_id,
            "disengage_reason": _preview(payload.disengage_reason),
            "ignore_for_seconds": payload.ignore_for_seconds,
            "create_bad_memory": payload.create_bad_memory,
            "bad_memory_sender_id": payload.bad_memory_sender_id,
            "bad_memory_preview": _preview(payload.bad_memory_text),
            "memory_mutation": payload.memory_mutation,
            "target_memory_id": payload.target_memory_id,
            "target_memory_sender_id": payload.target_memory_sender_id,
            "rewritten_memory_preview": _preview(payload.rewritten_memory_text),
            "codex_target_sender_id": payload.codex_target_sender_id,
            "codex_app_server_id": payload.codex_app_server_id,
            "codex_task_id": payload.codex_task_id,
            "codex_tool_call_id": payload.codex_tool_call_id,
        }
    if isinstance(event, OutboundMessagePreparedEvent):
        payload = event.payload
        first_message = payload.ordered_messages[0] if payload.ordered_messages else payload.raw_output
        return {
            "no_send": payload.no_send,
            "session_id": payload.session_id,
            "trigger_message_id": payload.trigger_message_id,
            "reply_to_message_id": payload.reply_to_message_id,
            "mood": payload.mood,
            "ordered_message_count": len(payload.ordered_messages),
            "message_preview": _preview(first_message),
        }
    if isinstance(event, MessageReadEvent):
        payload = event.payload
        return {
            "session_id": payload.session_id,
            "trigger_message_id": payload.trigger_message_id,
            "surfaced_message_ids": list(payload.surfaced_message_ids),
            "surfaced_until_message_id": payload.surfaced_until_message_id,
            "read_through_message_id": payload.read_through_message_id,
            "mark_seen": payload.mark_seen,
            "visible_not_before": _isoformat(payload.visible_not_before),
        }
    if isinstance(event, OutboundMessageSentEvent):
        payload = event.payload
        return {
            "no_send": payload.no_send,
            "session_id": payload.session_id,
            "trigger_message_id": payload.trigger_message_id,
            "reply_to_message_id": payload.reply_to_message_id,
            "ordered_message_count": len(payload.ordered_messages),
            "planned_message_count": payload.planned_message_count,
            "sent_message_ids": list(payload.sent_message_ids),
            "interrupted": payload.interrupted,
            "interruption_message_id": payload.interruption_message_id,
        }
    if isinstance(event, OutboundChunkSentEvent):
        payload = event.payload
        return {
            "session_id": payload.session_id,
            "trigger_message_id": payload.trigger_message_id,
            "reply_to_message_id": payload.reply_to_message_id,
            "chunk_index": payload.chunk_index,
            "chunk_count": payload.chunk_count,
            "sent_message_id": payload.sent_message_id,
            "typing_duration_seconds": _round_score(payload.typing_duration_seconds),
            "message_preview": _preview(payload.message_text),
        }
    if isinstance(event, SleepStateChangedEvent):
        payload = event.payload
        return {
            "sleep_state": payload.sleep_state,
            "changed_at": _isoformat(payload.changed_at),
            "scheduled_wake_at": _isoformat(payload.scheduled_wake_at),
        }
    if isinstance(event, PresenceStateChangedEvent):
        payload = event.payload
        return {
            "online": payload.online,
            "changed_at": _isoformat(payload.changed_at),
            "reason": payload.reason,
            "session_id": payload.session_id,
            "trigger_message_id": payload.trigger_message_id,
        }
    return None


def _telegram_message_summary(message: TelegramMessagePayload) -> dict[str, object]:
    return {
        "message_id": message.message_id,
        "sender_id": message.sender.id,
        "sender_name": message.sender.name,
        "content_preview": _preview(message.content),
        "reply_to_message_id": message.reply_to_message_id,
        "mentions": list(message.mentions),
        "media_type": message.attachment.media_type,
        "reaction_count": message.reaction_count,
        "is_self": message.sender.is_self,
        "thread_id": message.thread_id,
    }


def _preview(text: str | None, *, limit: int = _PREVIEW_LIMIT) -> str | None:
    if not text:
        return None
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3]}..."


def _round_score(value: float) -> float:
    return round(value, 3)


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
