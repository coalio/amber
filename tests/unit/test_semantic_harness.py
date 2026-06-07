from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytest

from src.ai.config import AIConfig
from src.ai.semantic.layer import AILayer, ConsciousHarness
from src.ai.semantic.schema import InterruptionDecisionSchema, SemanticDecisionSchema
from src.events.bus import EventBus
from src.events.context import ContextFrameMessagePayload, ContextFramePayload, PendingInterruptionPayload


@pytest.fixture(autouse=True)
def reset_event_bus() -> None:
    EventBus.reset_for_tests()
    yield
    EventBus.reset_for_tests()


def test_harness_allows_short_reply_that_does_not_share_trigger_tokens() -> None:
    harness = ConsciousHarness(AIConfig(semantic_retry_budget=1, max_reply_chars=320))
    frame = _build_frame("hey amber whats up")
    decision = SemanticDecisionSchema(
        action="reply",
        reply_to_message_id=None,
        chat_id=1001001001,
        reply_text="not much, you?",
        referenced_memory_ids=[],
        confidence=0.82,
        notes=[],
        trigger_message_id=1083,
        session_id="session_debug",
    )

    assert harness.evaluate(frame, decision) is None


def test_harness_still_rejects_near_duplicate_reply() -> None:
    harness = ConsciousHarness(AIConfig(semantic_retry_budget=1, max_reply_chars=320))
    frame = _build_frame("hey amber whats up")
    decision = SemanticDecisionSchema(
        action="reply",
        reply_to_message_id=None,
        chat_id=1001001001,
        reply_text="hey amber whats up?",
        referenced_memory_ids=[],
        confidence=0.82,
        notes=[],
        trigger_message_id=1083,
        session_id="session_debug",
    )

    failure = harness.evaluate(frame, decision)

    assert failure is not None
    assert failure.code == "reply_mirrors_trigger_message"
    assert "triggering message 1083" in failure.reason
    assert failure.context["offending_messages"][0]["message_id"] == 1083
    assert failure.context["recent_window"][0]["content_preview"] == "hey amber whats up"


def test_ai_layer_retries_with_descriptive_harness_feedback_and_previous_decision() -> None:
    frame = _build_frame("hey amber whats up")
    client = RecordingSemanticClient(
        [
            SemanticDecisionSchema(
                action="reply",
                reply_to_message_id=None,
                chat_id=1001001001,
                reply_text="hey amber whats up?",
                referenced_memory_ids=[],
                confidence=0.82,
                notes=[],
                trigger_message_id=1083,
                session_id="session_debug",
            ),
            SemanticDecisionSchema(
                action="reply",
                reply_to_message_id=None,
                chat_id=1001001001,
                reply_text="not much, you?",
                referenced_memory_ids=[],
                confidence=0.87,
                notes=[],
                trigger_message_id=1083,
                session_id="session_debug",
            ),
        ]
    )
    layer = AILayer(AIConfig(semantic_retry_budget=3, max_reply_chars=320), client)

    result = layer._call_with_harness(frame)

    assert result.action == "reply"
    assert result.reply_text == "not much, you?"
    assert result.reply_to_message_id == 1083
    assert len(client.calls) == 2
    retry_call = client.calls[1]
    assert retry_call["previous_decision"] is not None
    assert retry_call["previous_decision"].reply_text == "hey amber whats up?"
    assert retry_call["harness_feedback"] is not None
    assert retry_call["harness_feedback"]["code"] == "reply_mirrors_trigger_message"
    assert retry_call["harness_feedback"]["retry_attempt"] == 1
    assert retry_call["harness_feedback"]["retries_remaining"] == 2
    assert retry_call["harness_feedback"]["offending_messages"][0]["message_id"] == 1083
    assert retry_call["harness_feedback"]["recent_window"][0]["content_preview"] == "hey amber whats up"


def test_ai_layer_logs_critical_when_semantic_decision_validation_throws(caplog: pytest.LogCaptureFixture) -> None:
    frame = _build_frame("hey amber whats up")
    layer = AILayer(AIConfig(semantic_retry_budget=1, max_reply_chars=320), InvalidSemanticClient())
    caplog.set_level(logging.CRITICAL)

    result = layer._call_with_harness(frame)

    assert result.action == "ignore"
    assert result.notes == ["semantic_error:ValidationError"]
    record = next(record for record in caplog.records if record.getMessage() == "semantic.decision_failed")
    assert record.levelno == logging.CRITICAL
    assert record.event == "semantic.decision_failed"
    assert record.context["phase"] == "first_pass"
    assert record.context["chat_id"] == 1001001001
    assert record.context["session_id"] == "session_debug"
    assert record.context["trigger_message_id"] == 1083
    assert record.context["error_type"] == "ValidationError"
    assert "disengage" in record.context["error_message"]


def test_ai_layer_uses_interruption_decision_flow_for_pending_interruption() -> None:
    frame = _build_frame(
        "wait, keep it short",
        pending_interruption=PendingInterruptionPayload(
            original_trigger_message_id=1082,
            original_reply_to_message_id=1082,
            interrupting_message_id=1083,
            reply_target_sender_id="1001001001",
            reply_target_sender_name="Fixture User",
            sent_reply_chunks=["first chunk"],
            remaining_reply_chunks=["second chunk", "third chunk"],
        ),
    )
    client = RecordingSemanticClient(
        [],
        interruption_responses=[
            InterruptionDecisionSchema(
                interrupt_decision="accept",
                action="reply",
                reply_to_message_id=1083,
                reply_text="yeah exactly, and the short version is sfinae filters bad overloads during substitution.",
                referenced_memory_ids=[],
                confidence=0.91,
                reason="Interrupting message matches the remaining idea and should steer the continuation.",
                notes=["steered_remaining_plan"],
            )
        ],
    )
    layer = AILayer(AIConfig(semantic_retry_budget=1, max_reply_chars=320), client)

    result = layer._call_with_harness(frame)

    assert result.action == "reply"
    assert result.reply_to_message_id == 1083
    assert result.reply_text == "yeah exactly, and the short version is sfinae filters bad overloads during substitution."
    assert result.notes[0] == "interrupt_accept"
    assert len(client.interruption_calls) == 1
    assert client.interruption_calls[0]["interruption"].interrupting_message_id == 1083


def test_ai_layer_retries_when_accepted_interruption_reuses_old_unsent_plan() -> None:
    frame = _build_frame(
        "i have one too",
        pending_interruption=PendingInterruptionPayload(
            original_trigger_message_id=1082,
            original_reply_to_message_id=1082,
            interrupting_message_id=1083,
            reply_target_sender_id="1001001001",
            reply_target_sender_name="Fixture User",
            sent_reply_chunks=["oh i love those fluffy creatures"],
            remaining_reply_chunks=["do you have any cats"],
        ),
    )
    client = RecordingSemanticClient(
        [],
        interruption_responses=[
            InterruptionDecisionSchema(
                interrupt_decision="accept",
                action="reply",
                reply_to_message_id=1083,
                reply_text="do you have any cats",
                referenced_memory_ids=[],
                confidence=0.88,
                reason="Accepted the interruption.",
                notes=[],
            ),
            InterruptionDecisionSchema(
                interrupt_decision="accept",
                action="reply",
                reply_to_message_id=1083,
                reply_text="yeah exactly, i was about to ask that, how old is yours?",
                referenced_memory_ids=[],
                confidence=0.91,
                reason="Accepted and rewrote the follow-up naturally.",
                notes=["rewritten_plan"],
            ),
        ],
    )
    layer = AILayer(AIConfig(semantic_retry_budget=2, max_reply_chars=320), client)

    result = layer._call_with_harness(frame)

    assert result.reply_text == "yeah exactly, i was about to ask that, how old is yours?"
    assert len(client.interruption_calls) == 2
    retry_call = client.interruption_calls[1]
    assert retry_call["harness_feedback"] is not None
    assert retry_call["harness_feedback"]["code"] == "accepted_interruption_reuses_unsent_plan"
    assert retry_call["previous_decision"] is not None
    assert retry_call["previous_decision"].reply_text == "do you have any cats"


def _build_frame(
    trigger_text: str,
    *,
    pending_interruption: PendingInterruptionPayload | None = None,
) -> ContextFramePayload:
    message = ContextFrameMessagePayload(
        message_id=1083,
        sender_id="1001001001",
        sender_name="Fixture User",
        content=trigger_text,
        timestamp=datetime(2026, 4, 21, 4, 36, 19, tzinfo=timezone.utc),
        reply_to_message_id=None,
        reply_to_sender_id=None,
        reply_to_sender_name=None,
        source="surface",
    )
    return ContextFramePayload(
        session_id="session_debug",
        chat_id=1001001001,
        trigger_message_id=1083,
        current_message=message,
        recent_messages=[message],
        topic_summary="Greeting",
        open_loops=[],
        participants=["Fixture User"],
        relevant_memories=[],
        mood="joking",
        fatigue_notice=None,
        recommended_reply_candidate=1083,
        engaged_user_ids=["1001001001"],
        compacted_facts=[],
        expanded_memory_ids=[],
        pending_interruption=pending_interruption,
    )


class RecordingSemanticClient:
    def __init__(
        self,
        responses: list[SemanticDecisionSchema],
        *,
        interruption_responses: list[InterruptionDecisionSchema] | None = None,
    ) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, object]] = []
        self._interruption_responses = list(interruption_responses or [])
        self.interruption_calls: list[dict[str, object]] = []

    def decide(
        self,
        frame: ContextFramePayload,
        *,
        harness_feedback: dict | None = None,
        previous_decision: SemanticDecisionSchema | None = None,
    ) -> SemanticDecisionSchema:
        self.calls.append(
            {
                "frame": frame,
                "harness_feedback": harness_feedback,
                "previous_decision": previous_decision,
            }
        )
        return self._responses.pop(0)

    def decide_interruption(
        self,
        frame: ContextFramePayload,
        interruption: PendingInterruptionPayload,
        *,
        harness_feedback: dict | None = None,
        previous_decision: InterruptionDecisionSchema | None = None,
    ) -> InterruptionDecisionSchema:
        self.interruption_calls.append(
            {
                "frame": frame,
                "interruption": interruption,
                "harness_feedback": harness_feedback,
                "previous_decision": previous_decision,
            }
        )
        return self._interruption_responses.pop(0)


class InvalidSemanticClient:
    def decide(
        self,
        frame: ContextFramePayload,
        *,
        harness_feedback: dict | None = None,
        previous_decision: SemanticDecisionSchema | None = None,
    ) -> SemanticDecisionSchema:
        return SemanticDecisionSchema.model_validate(
            {
                "action": "broken",
                "chat_id": frame.chat_id,
                "confidence": 0.5,
            }
        )

    def decide_interruption(
        self,
        frame: ContextFramePayload,
        interruption: PendingInterruptionPayload,
        *,
        harness_feedback: dict | None = None,
        previous_decision: InterruptionDecisionSchema | None = None,
    ) -> InterruptionDecisionSchema:
        return InterruptionDecisionSchema.model_validate(
            {
                "interrupt_decision": "accept",
                "action": "broken",
                "confidence": 0.5,
                "reason": "broken",
            }
        )
