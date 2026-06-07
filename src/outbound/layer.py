from __future__ import annotations

import re
import textwrap

from src.events.ai import SemanticDecisionMadeEvent, SemanticDecisionPayload
from src.events.bus import EventBus, emitter_context
from src.events.outbound import OutboundMessagePreparedEvent, OutboundMessagePreparedPayload
from src.outbound.config import OutboundPreparationConfig
from src.state.store import GlobalStateStore


class OutboundPreparationLayer:
    _CODEX_PUNCTUATION_TRANSLATION = str.maketrans(
        {
            "—": "-",
            "–": "-",
            "“": '"',
            "”": '"',
            "‘": "'",
            "’": "'",
            "…": "...",
        }
    )

    def __init__(self, config: OutboundPreparationConfig, state_store: GlobalStateStore) -> None:
        self._config = config
        self._state_store = state_store
        EventBus.subscribe("SemanticDecisionMadeEvent", self.handle_semantic_decision)

    def handle_semantic_decision(self, event: SemanticDecisionMadeEvent) -> None:
        with emitter_context("outbound_preparation"):
            state = self._state_store.snapshot()
            payload = event.payload
            if payload.action != "reply":
                EventBus.emit(
                    OutboundMessagePreparedEvent(
                        correlation_id=event.correlation_id,
                        chat_id=event.chat_id,
                        payload=OutboundMessagePreparedPayload(
                            chat_id=payload.chat_id,
                            session_id=payload.session_id,
                            trigger_message_id=payload.trigger_message_id,
                            ordered_messages=[],
                            reply_to_message_id=payload.reply_to_message_id,
                            mood=state.mood,
                            raw_output="",
                            no_send=True,
                            frame_created_at=payload.frame_created_at,
                            visible_read_not_before=payload.visible_read_not_before,
                            visible_surfaced_message_ids=list(payload.visible_surfaced_message_ids),
                            visible_surfaced_until_message_id=payload.visible_surfaced_until_message_id,
                            visible_read_through_message_id=payload.visible_read_through_message_id,
                        ),
                    )
                )
                return

            if payload.codex_app_server_id:
                prepared = self._rewrite_codex_draft(payload.draft_text or "")
                self._emit_prepared(event, payload, prepared, state.mood)
                return

            self._emit_prepared(event, payload, self._prepare_plain_draft(payload.draft_text or ""), state.mood)

    def _emit_prepared(
        self,
        event: SemanticDecisionMadeEvent,
        payload: SemanticDecisionPayload,
        prepared_text: str,
        mood: str,
    ) -> None:
        ordered_messages = self._split_output(prepared_text)
        no_send = not any(item.strip() for item in ordered_messages)
        EventBus.emit(
            OutboundMessagePreparedEvent(
                correlation_id=event.correlation_id,
                chat_id=event.chat_id,
                payload=OutboundMessagePreparedPayload(
                    chat_id=payload.chat_id,
                    session_id=payload.session_id,
                    trigger_message_id=payload.trigger_message_id,
                    ordered_messages=[] if no_send else ordered_messages,
                    reply_to_message_id=payload.reply_to_message_id,
                    mood=mood,
                    raw_output=prepared_text,
                    no_send=no_send,
                    frame_created_at=payload.frame_created_at,
                    visible_read_not_before=payload.visible_read_not_before,
                    visible_surfaced_message_ids=list(payload.visible_surfaced_message_ids),
                    visible_surfaced_until_message_id=payload.visible_surfaced_until_message_id,
                    visible_read_through_message_id=payload.visible_read_through_message_id,
                ),
            )
        )

    def _prepare_plain_draft(self, draft_text: str) -> str:
        return draft_text.strip()

    def _rewrite_codex_draft(self, draft_text: str) -> str:
        text = "\n".join(line.strip() for line in draft_text.strip().splitlines() if line.strip())
        text = text.translate(self._CODEX_PUNCTUATION_TRANSLATION)
        text = re.sub(r"(?i)^(got it|cool|okay|ok|sounds good)[,:\s-]+", "", text).strip()
        text = re.sub(r"(?i)\s*(so )?i can (let|tell) codex know\.?", "", text).strip()
        text = re.sub(r"(?i)\b(i'?ll|i will)\s+(send|pass|forward)\s+(it|this|that)?\s*(along\s+)?to codex\b\.?", "i'll keep going", text)
        text = re.sub(r"(?i)\bsending\s+(it|this|that)?\s*(along\s+)?to codex\b\.?", "i'll keep going", text)
        text = re.sub(r"(?i)\bwhat codex needs\b", "what i need", text)
        text = re.sub(r"(?i)\bcodex needs\b", "i need", text)
        if "```" not in text:
            text = text.lower()
        return text

    def _split_output(self, output_text: str) -> list[str]:
        cleaned = output_text.strip()
        if not cleaned:
            return []
        messages: list[str] = []
        code_block_lines: list[str] = []
        inside_code_block = False

        for raw_line in cleaned.splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()

            if inside_code_block:
                code_block_lines.append(line)
                if stripped.startswith("```"):
                    messages.append("\n".join(code_block_lines).strip())
                    code_block_lines = []
                    inside_code_block = False
                continue

            if not stripped:
                continue

            if stripped.startswith("```"):
                code_block_lines = [line]
                if stripped.count("```") >= 2 and stripped != "```":
                    messages.append("\n".join(code_block_lines).strip())
                    code_block_lines = []
                else:
                    inside_code_block = True
                continue

            messages.extend(self._split_plain_line(stripped))

        if code_block_lines:
            messages.append("\n".join(code_block_lines).strip())

        return messages

    def _split_plain_line(self, line: str) -> list[str]:
        if len(line) <= self._config.max_chunk_chars:
            return [line]

        segments = [segment.strip() for segment in re.split(r"(?<=[.!?])\s+", line) if segment.strip()]
        if len(segments) <= 1:
            return self._wrap_text(line)

        chunks: list[str] = []
        current = ""
        for segment in segments:
            if len(segment) > self._config.max_chunk_chars:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.extend(self._wrap_text(segment))
                continue

            if not current:
                current = segment
                continue

            if len(current) + len(segment) + 1 > self._config.max_chunk_chars:
                chunks.append(current)
                current = segment
            else:
                current = f"{current} {segment}"

        if current:
            chunks.append(current)

        return chunks

    def _wrap_text(self, text: str) -> list[str]:
        return textwrap.wrap(
            text,
            width=self._config.max_chunk_chars,
            break_long_words=True,
            break_on_hyphens=False,
        )
