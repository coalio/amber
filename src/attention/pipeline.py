from __future__ import annotations

from dataclasses import dataclass

from src.attention.config import AttentionConfig
from src.attention.memory.store import MemoryStore
from src.attention.scoring.zero_shot import AttentionModelClassification, AttentionPolicyScorer
from src.attention.sentiment.heuristics import compute_sentiment_delta
from src.attention.utils import (
    contains_question_intent,
    novelty_score,
)
from src.events.action import MessageReadEvent, MessageReadPayload
from src.events.attention import (
    AttentionClassificationPayload,
    AttentionDecisionMadeEvent,
    AttentionDecisionPayload,
    StickerSignalPayload,
)
from src.events.bus import EventBus, emitter_context
from src.events.receiver import TelegramMessagePayload, TelegramMessageReceivedEvent
from src.state.store import GlobalStateStore
from src.utils.message_archive import MessageArchive
from src.utils.metrics import MetricsRegistry


@dataclass
class AttentionFeatures:
    heuristic_score: float
    reasons: list[str]
    reply_target_candidate: int | None
    sticker_signal: StickerSignalPayload | None


class AttentionLayer:
    def __init__(
        self,
        config: AttentionConfig,
        scorer: AttentionPolicyScorer,
        state_store: GlobalStateStore,
        memory_store: MemoryStore,
        message_archive: MessageArchive,
    ) -> None:
        self._config = config
        self._scorer = scorer
        self._state_store = state_store
        self._memory_store = memory_store
        self._message_archive = message_archive
        self._metrics = MetricsRegistry.instance()
        self._seen_message_ids: set[tuple[str, int]] = set()
        self._subscription_id = EventBus.subscribe("TelegramMessageReceivedEvent", self.handle_message)

    def _heuristic_features(self, message: TelegramMessagePayload) -> AttentionFeatures:
        reasons: list[str] = []
        score = 0.0
        directed = False
        if "amber" in message.content.lower() or "@amber" in message.content.lower():
            score += 0.45
            directed = True
            reasons.append("direct_mention")
        if message.reply_to_sender.id in {None, ""}:
            pass
        elif message.reply_to_sender.name and message.reply_to_sender.name.lower() == "amber":
            score += 0.4
            directed = True
            reasons.append("reply_to_amber")
        if contains_question_intent(message):
            score += 0.12
            reasons.append("question_intent")
        recent_messages = self._message_archive.recent_segment_for_sender(message.chat_id, message.sender.id, message.message_id, limit=6)
        score += 0.08 * novelty_score(message, recent_messages)
        if message.reaction_count:
            score += min(message.reaction_count, 4) * 0.03
            reasons.append("reaction_signal")
        if len(message.content) > 120:
            score += 0.05
            reasons.append("long_message")
        if message.attachment.media_type == "sticker":
            preceding_segment = [item.content for item in recent_messages]
            tones = ["random"]
            if preceding_segment and any("?" in item for item in preceding_segment):
                tones = ["confused", "playful"]
            sticker_signal = StickerSignalPayload(
                sticker_file_id=message.attachment.file_id or message.attachment.file_name,
                sticker_set_id=message.attachment.sticker_set_id,
                inferred_tones=tones,
                confidence=0.35,
                preceding_segment=preceding_segment,
            )
            reasons.append("sticker_signal")
        else:
            sticker_signal = None
        reply_target_candidate = message.reply_to_message_id or message.message_id
        if directed:
            score += 0.1
        return AttentionFeatures(
            heuristic_score=min(score, 1.0),
            reasons=reasons,
            reply_target_candidate=reply_target_candidate,
            sticker_signal=sticker_signal,
        )

    def _feature_row(self, message: TelegramMessagePayload) -> dict[str, object]:
        recent_chat_messages = self._message_archive.recent_segment_for_sender(message.chat_id, message.sender.id, message.message_id, limit=8)
        window_message_count = len(recent_chat_messages) + 1
        reply_to_amber = message.reply_to_sender.name is not None and message.reply_to_sender.name.lower() == "amber"
        return {
            "focus_sender": message.sender.name,
            "focus_content": message.content,
            "reply_to_message_id": message.reply_to_message_id,
            "reply_to_message_id_present": message.reply_to_message_id is not None,
            "reply_to_amber": reply_to_amber,
            "mentions_amber": "amber" in message.content.lower(),
            "media_type": message.attachment.media_type,
            "reaction_count": message.reaction_count,
            "window_message_count": window_message_count,
        }

    def _score_message(self, feature_row: dict[str, object]) -> tuple[float, AttentionModelClassification | None]:
        classify = getattr(self._scorer, "classify", None)
        if callable(classify):
            classification = classify(feature_row)
            return float(classification.score), classification
        return float(self._scorer.score(feature_row)), None

    def _always_surface_sender(self, sender_id: str) -> bool:
        normalized_sender_id = sender_id.removeprefix("user")
        return normalized_sender_id in self._config.always_surface_telegram_ids

    def handle_message(self, event: TelegramMessageReceivedEvent) -> None:
        with emitter_context("attention"):
            self._metrics.increment("inbound_messages")
            message = event.payload
            dedupe_key = (str(message.chat_id), message.message_id)
            if dedupe_key in self._seen_message_ids:
                decision = self._build_decision(event, "discard", 0.0, 0.0, 0.0, ["duplicate_message"], False)
                EventBus.emit(decision)
                return
            state = self._state_store.snapshot()
            open_question_reply = (
                not message.sender.is_self
                and any(
                    str(question.chat_id) == str(message.chat_id)
                    and str(question.sender_id) == str(message.sender.id)
                    for question in state.open_questions.values()
                )
            )
            seen_through_message_id = state.seen_through_by_chat.get(str(message.chat_id), 0)
            if message.message_id <= seen_through_message_id and not open_question_reply:
                self._seen_message_ids.add(dedupe_key)
                EventBus.emit(self._build_decision(event, "discard", 0.0, 0.0, 0.0, ["already_seen"], False))
                return
            self._seen_message_ids.add(dedupe_key)
            if message.sender.is_self:
                decision = self._build_decision(event, "discard", 0.0, 0.0, 0.0, ["self_authored"], False)
                EventBus.emit(decision)
                return
            if self._config.mode == "work" and not self._always_surface_sender(str(message.sender.id)):
                decision = self._build_decision(event, "discard", 0.0, 0.0, 0.0, ["work_mode_sender_not_allowed"], False)
                EventBus.emit(decision)
                return
            self._memory_store.touch_user(message.sender.id, message.sender.name, message.timestamp)
            directed_at_amber = message.reply_to_sender.name is not None and message.reply_to_sender.name.lower() == "amber"
            sentiment_delta = compute_sentiment_delta(message, directed_at_amber=directed_at_amber or "amber" in message.content.lower())
            if sentiment_delta:
                self._memory_store.adjust_sentiment(
                    message.sender.id,
                    message.sender.name,
                    sentiment_delta,
                    self._config.sentiment_multiplier,
                    message.timestamp,
                )
            ignore_rule = self._conversation_ignore_rule(state, message)
            if ignore_rule is not None:
                self._mark_message_seen(event, message)
                EventBus.emit(self._build_decision(event, "discard", 0.0, 0.0, 0.0, ["ignore_window_active"], False))
                return
            if not self._config.disable_sleep_state and self._message_was_sent_while_asleep(state, message):
                self._mark_message_seen(event, message)
                EventBus.emit(self._build_decision(event, "discard", 0.0, 0.0, 0.0, ["sleep_state_asleep"], False))
                return
            active_chat_match = state.active_chat_id is not None and str(state.active_chat_id) == str(message.chat_id)
            pending_chat_match = state.pending_chat_id is not None and str(state.pending_chat_id) == str(message.chat_id)
            if state.active_chat_id is not None and not active_chat_match:
                EventBus.emit(self._build_decision(event, "discard", 0.0, 0.0, 0.0, ["other_chat_active"], False))
                return
            if state.active_chat_id is None and state.pending_chat_id is not None and not pending_chat_match:
                EventBus.emit(self._build_decision(event, "discard", 0.0, 0.0, 0.0, ["other_chat_pending_engagement"], False))
                return
            if active_chat_match:
                self._mark_message_read(event, message)
            engaged_user_ids = set(state.conversation_engaged_user_ids)
            engaged_bypass = str(message.sender.id) in engaged_user_ids and active_chat_match
            pending_bypass = pending_chat_match
            always_surface_sender = self._always_surface_sender(str(message.sender.id))
            if self._config.mode == "work":
                memory_cards = self._memory_store.retrieve(message, self._config.memory_limit)
                reasons = ["work_mode_full_importance", "always_surface_sender"]
                if memory_cards:
                    reasons.append("memory_hit")
                if directed_at_amber:
                    reasons.append("reply_to_amber_sender")
                memory_write = self._memory_store.maybe_write_memory(message)
                if memory_write is not None:
                    reasons.append("memory_write")
                self._metrics.increment("attention_surface_count")
                self._metrics.observe("attention_score", 1.0)
                self._state_store.update_attention_state(mood="annoyed" if sentiment_delta < 0 else "calm")
                EventBus.emit(
                    self._build_decision(
                        event,
                        "surface_urgent",
                        1.0,
                        1.0,
                        1.0,
                        reasons,
                        engaged_bypass,
                        memory_cards=memory_cards,
                        reply_target_candidate=message.reply_to_message_id or message.message_id,
                    )
                )
                return
            heuristics = self._heuristic_features(message)
            model_score, classification = self._score_message(self._feature_row(message))
            attention_score = min((heuristics.heuristic_score * 0.35) + (model_score * 0.65), 1.0)
            reasons = list(heuristics.reasons)
            if classification is not None:
                reasons.extend(classification.reasons)
            memory_cards = self._memory_store.retrieve(message, self._config.memory_limit)
            if memory_cards:
                reasons.append("memory_hit")
                attention_score = min(attention_score + 0.08, 1.0)
            if engaged_bypass:
                reasons.append("engaged_user_bypass")
                attention_score = max(attention_score, self._config.surface_threshold + 0.1)
            elif pending_bypass:
                reasons.append("pending_engagement_bypass")
                attention_score = max(attention_score, self._config.surface_threshold + 0.1)
            elif always_surface_sender:
                reasons.append("always_surface_sender")
                attention_score = max(attention_score, self._config.surface_threshold + 0.1)
            if directed_at_amber:
                reasons.append("reply_to_amber_sender")
            memory_write = self._memory_store.maybe_write_memory(message)
            if memory_write is not None:
                reasons.append("memory_write")
            if heuristics.sticker_signal is not None:
                self._memory_store.persist_sticker_signal(heuristics.sticker_signal, message.sender.id)
            decision = "discard"
            if attention_score >= self._config.urgent_threshold:
                decision = "surface_urgent"
            elif attention_score >= self._config.surface_threshold:
                decision = "surface"
            if "direct_mention" in reasons or "reply_to_amber" in reasons:
                decision = "surface"
            if decision != "discard":
                self._metrics.increment("attention_surface_count")
            else:
                self._metrics.increment("attention_discard_count")
            self._metrics.observe("attention_score", attention_score)
            mood = classification.tone if classification is not None else "joking"
            if sentiment_delta < 0:
                mood = "annoyed"
            elif "question_intent" in reasons:
                mood = "calm"
            self._state_store.update_attention_state(mood=mood)
            EventBus.emit(
                self._build_decision(
                    event,
                    decision,
                    attention_score,
                    heuristics.heuristic_score,
                    model_score,
                    reasons,
                    engaged_bypass,
                    memory_cards=memory_cards,
                    sticker_signal=heuristics.sticker_signal,
                    classification=classification,
                    reply_target_candidate=heuristics.reply_target_candidate,
                )
            )

    def _build_decision(
        self,
        event: TelegramMessageReceivedEvent,
        decision: str,
        attention_score: float,
        heuristic_score: float,
        model_score: float,
        reasons: list[str],
        engaged_user_bypass: bool,
        *,
        memory_cards: list | None = None,
        sticker_signal: StickerSignalPayload | None = None,
        classification: AttentionModelClassification | None = None,
        reply_target_candidate: int | None = None,
    ) -> AttentionDecisionMadeEvent:
        return AttentionDecisionMadeEvent(
            correlation_id=event.correlation_id,
            chat_id=event.chat_id,
            payload=AttentionDecisionPayload(
                decision=decision,
                message=event.payload,
                attention_score=attention_score,
                heuristic_score=heuristic_score,
                model_score=model_score,
                reasons=reasons,
                memory_cards=memory_cards or [],
                sticker_signal=sticker_signal,
                classification=(
                    AttentionClassificationPayload(
                        score=classification.score,
                        labels=classification.labels,
                        top_labels=classification.top_labels,
                        tone=classification.tone,
                        model_name=classification.model_name,
                        model_revision=classification.model_revision,
                    )
                    if classification is not None
                    else None
                ),
                engaged_user_bypass=engaged_user_bypass,
                reply_target_candidate=reply_target_candidate,
            ),
        )

    def _mark_message_seen(self, event: TelegramMessageReceivedEvent, message: TelegramMessagePayload) -> None:
        self._state_store.mark_seen(message.chat_id, message.message_id)
        EventBus.emit(
            MessageReadEvent(
                correlation_id=event.correlation_id,
                chat_id=message.chat_id,
                payload=MessageReadPayload(
                    chat_id=message.chat_id,
                    session_id=None,
                    trigger_message_id=message.message_id,
                    surfaced_message_ids=[],
                    surfaced_until_message_id=None,
                    read_through_message_id=message.message_id,
                    mark_seen=True,
                ),
            )
        )

    def _mark_message_read(self, event: TelegramMessageReceivedEvent, message: TelegramMessagePayload) -> None:
        EventBus.emit(
            MessageReadEvent(
                correlation_id=event.correlation_id,
                chat_id=message.chat_id,
                payload=MessageReadPayload(
                    chat_id=message.chat_id,
                    session_id=None,
                    trigger_message_id=message.message_id,
                    surfaced_message_ids=[],
                    surfaced_until_message_id=None,
                    read_through_message_id=message.message_id,
                    mark_seen=False,
                ),
            )
        )

    def _conversation_ignore_rule(self, state, message: TelegramMessagePayload):
        rule = state.conversation_ignore_rules.get(f"{message.chat_id}:{message.sender.id}")
        if rule is None or rule.ignore_until is None:
            return None
        if message.timestamp < rule.created_at:
            return None
        if message.timestamp > rule.ignore_until:
            return None
        return rule

    def _message_was_sent_while_asleep(self, state, message: TelegramMessagePayload) -> bool:
        if state.sleep_state == "asleep":
            if state.slept_at is None:
                return True
            return message.timestamp >= state.slept_at
        if state.slept_at is None:
            return False
        if state.woke_at <= state.slept_at:
            return False
        return state.slept_at <= message.timestamp < state.woke_at
