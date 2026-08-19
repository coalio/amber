from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from src.ai.semantic.config import SemanticConfig
from src.ai.semantic.schema import (
    InterruptionDecisionSchema,
    SemanticDecisionSchema,
)
from src.events.context import ContextFrameMessagePayload, ContextFramePayload, PendingInterruptionPayload
from src.providers.base import ModelProvider
from src.tools.codex_workflow import CodexWorkRoute, CodexWorkStateMachine
from src.tools.registry import ToolSession


class SemanticClient(Protocol):
    def decide(
        self,
        frame: ContextFramePayload,
        *,
        harness_feedback: dict[str, Any] | None = None,
        previous_decision: SemanticDecisionSchema | None = None,
    ) -> SemanticDecisionSchema:
        ...

    def decide_interruption(
        self,
        frame: ContextFramePayload,
        interruption: PendingInterruptionPayload,
        *,
        harness_feedback: dict[str, Any] | None = None,
        previous_decision: InterruptionDecisionSchema | None = None,
    ) -> InterruptionDecisionSchema:
        ...


@dataclass
class SemanticSessionState:
    input_items: list[dict[str, Any]] = field(default_factory=list)
    seen_message_ids: set[int] = field(default_factory=set)
    seeded: bool = False
    previous_response_id: str | None = None
    codex_workflow_trigger_message_id: int | None = None
    codex_workflow: CodexWorkStateMachine | None = None


class SemanticModelClient:
    _CODEX_TASK_STARTED_NOTE = "codex task started"
    _CODEX_TASK_STARTED_ACK = "i'll start on that now"

    def __init__(self, config: SemanticConfig, provider: ModelProvider) -> None:
        self._config = config
        self._provider = provider
        self._session_state: dict[str, SemanticSessionState] = {}

    def decide(
        self,
        frame: ContextFramePayload,
        *,
        harness_feedback: dict[str, Any] | None = None,
        previous_decision: SemanticDecisionSchema | None = None,
    ) -> SemanticDecisionSchema:
        session_state = self._session_state.setdefault(self._session_key(frame), SemanticSessionState())
        input_items = self._build_input_items(
            frame,
            session_state=session_state,
            harness_feedback=harness_feedback,
            previous_decision=previous_decision,
        )
        instructions = "\n\n".join(
            [
                self._config.action_contract_prompt,
                self._config.memory_prompt,
                (
                    "Harness retry mode: revise the previous structured decision using the harness feedback. "
                    "If you still want to reply, produce materially better reply_text that addresses the flagged issue. "
                    "If no good reply exists, choose a different action."
                    if harness_feedback or previous_decision
                    else "Harness retry mode: none"
                ),
            ]
        )
        structured_with_metadata = getattr(self._provider, "generate_structured_with_metadata", None)
        tools = self._new_tool_session(frame, session_state)
        if callable(structured_with_metadata):
            request = {
                "model": self._config.model,
                "instructions": instructions,
                "input_items": input_items,
                "schema": SemanticDecisionSchema,
                "max_output_tokens": self._config.max_output_tokens,
                "temperature": self._config.temperature,
                "reasoning_effort": self._config.reasoning_effort,
                "previous_response_id": session_state.previous_response_id,
            }
            if tools is not None:
                request["tools"] = tools
            decision, response_id = structured_with_metadata(**request)
            if response_id:
                session_state.previous_response_id = response_id
            return self._finalize_work_decision(frame, decision, tools)
        request = {
            "model": self._config.model,
            "instructions": instructions,
            "input_items": input_items,
            "schema": SemanticDecisionSchema,
            "max_output_tokens": self._config.max_output_tokens,
            "temperature": self._config.temperature,
            "reasoning_effort": self._config.reasoning_effort,
        }
        if tools is not None:
            request["tools"] = tools
        decision = self._provider.generate_structured(**request)
        return self._finalize_work_decision(frame, decision, tools)

    def _new_tool_session(
        self,
        frame: ContextFramePayload,
        session_state: SemanticSessionState,
    ) -> ToolSession | None:
        if self._config.tool_registry is None:
            return None
        route = self._codex_work_route(frame)
        if (
            session_state.codex_workflow is None
            or session_state.codex_workflow_trigger_message_id != frame.trigger_message_id
            or session_state.codex_workflow.route != route
        ):
            session_state.codex_workflow_trigger_message_id = frame.trigger_message_id
            session_state.codex_workflow = CodexWorkStateMachine(route)
        return self._config.tool_registry.new_session(
            runtime=self._config.tool_runtime,
            codex_workflow=session_state.codex_workflow,
        )

    def _codex_work_route(self, frame: ContextFramePayload) -> CodexWorkRoute:
        # route active questions back to their blocked task without relying on reply metadata
        questions = list(frame.open_questions)
        if not questions and frame.open_question is not None:
            questions = [frame.open_question]
        if questions:
            if any(question.user_replies for question in questions):
                return CodexWorkRoute.SUBMIT_CLARIFICATION
            return CodexWorkRoute.NONE
        if frame.codex_notification is not None:
            return CodexWorkRoute.NONE
        return CodexWorkRoute.START_TASK

    def _finalize_work_decision(
        self,
        frame: ContextFramePayload,
        decision: SemanticDecisionSchema,
        tools: ToolSession | None,
    ) -> SemanticDecisionSchema:
        # derive delegation state from executed tools instead of model claims
        started_task = self._started_codex_task(tools)
        submitted_reply = self._submitted_codex_reply(tools)
        work_dispatched = started_task is not None or submitted_reply is not None
        decision = decision.model_copy(
            update={
                "work_intent": "delegate" if work_dispatched else decision.work_intent,
                "codex_work_dispatched": work_dispatched,
                "codex_task_started": started_task is not None,
            }
        )
        if submitted_reply is not None:
            return decision.model_copy(
                update={
                    "codex_app_server_id": str(submitted_reply.get("app_server_id") or "") or None,
                    "codex_task_id": str(submitted_reply.get("task_id") or "") or None,
                    "codex_tool_call_id": str(submitted_reply.get("tool_call_id") or "") or None,
                }
            )
        if started_task is None:
            return decision
        decision = decision.model_copy(
            update={
                "codex_app_server_id": str(started_task.get("app_server_id") or "") or None,
                "codex_task_id": str(started_task.get("task_id") or "") or None,
                "codex_tool_call_id": None,
            }
        )
        return self._acknowledge_started_codex_task(frame, decision)

    def _acknowledge_started_codex_task(
        self,
        frame: ContextFramePayload,
        decision: SemanticDecisionSchema,
    ) -> SemanticDecisionSchema:
        # acknowledge only telegram-originated task starts
        if (
            frame.linear_task_list is not None
            or frame.open_question is not None
            or frame.open_questions
            or frame.codex_notification is not None
        ):
            return decision
        if decision.action == "reply" and (decision.reply_text or "").strip():
            return decision
        notes = list(decision.notes)
        if self._CODEX_TASK_STARTED_NOTE not in notes:
            notes.append(self._CODEX_TASK_STARTED_NOTE)

        # reply as amber while preserving task provenance for future replies
        return decision.model_copy(
            update={
                "action": "reply",
                "reply_to_message_id": frame.recommended_reply_candidate or frame.current_message.message_id,
                "chat_id": frame.chat_id,
                "reply_text": self._CODEX_TASK_STARTED_ACK,
                "referenced_memory_ids": [],
                "confidence": max(decision.confidence, 0.9),
                "notes": notes,
                "codex_target_sender_id": None,
                "codex_target_sender_name": None,
                "codex_tool_call_id": None,
            }
        )

    def _started_codex_task(self, tools: ToolSession | None) -> dict[str, Any] | None:
        if tools is None:
            return None
        transition = tools.completed_codex_transition
        if transition is not None and transition.tool_name == "CodexRunTask":
            result = transition.result
            if isinstance(result, dict) and not result.get("error") and result.get("task_id") and result.get("status"):
                return result
        return None

    def _submitted_codex_reply(self, tools: ToolSession | None) -> dict[str, Any] | None:
        if tools is None:
            return None
        transition = tools.completed_codex_transition
        if transition is not None and transition.tool_name == "CodexSendReply":
            result = transition.result
            if isinstance(result, dict) and not result.get("error") and result.get("submitted") is True:
                return transition.arguments
        return None

    def decide_interruption(
        self,
        frame: ContextFramePayload,
        interruption: PendingInterruptionPayload,
        *,
        harness_feedback: dict[str, Any] | None = None,
        previous_decision: InterruptionDecisionSchema | None = None,
    ) -> InterruptionDecisionSchema:
        session_state = self._session_state.setdefault(self._session_key(frame), SemanticSessionState())
        conversation_prefix = self._build_conversation_prefix(frame, session_state=session_state)
        instructions = "\n\n".join(
            [
                self._config.interruption_prompt,
                self._config.action_contract_prompt,
                self._config.memory_prompt,
                (
                    "Harness retry mode: revise the previous interruption decision using the harness feedback. "
                    "If you keep `accept`, rewrite the old plan materially instead of reusing the unsent chunk verbatim. "
                    "If no natural rewritten reply exists, choose `decline` or another action."
                    if harness_feedback or previous_decision
                    else "Harness retry mode: none"
                ),
            ]
        )
        input_items = [
            *conversation_prefix,
            *self._interruption_turn_items(
                frame,
                interruption,
                harness_feedback=harness_feedback,
                previous_decision=previous_decision,
            ),
        ]
        structured_with_metadata = getattr(self._provider, "generate_structured_with_metadata", None)
        tools = self._new_tool_session(frame, session_state)
        if callable(structured_with_metadata):
            request = {
                "model": self._config.model,
                "instructions": instructions,
                "input_items": input_items,
                "schema": InterruptionDecisionSchema,
                "max_output_tokens": self._config.max_output_tokens,
                "temperature": self._config.temperature,
                "reasoning_effort": self._config.reasoning_effort,
                "previous_response_id": session_state.previous_response_id,
            }
            if tools is not None:
                request["tools"] = tools
            decision, response_id = structured_with_metadata(**request)
            if response_id:
                session_state.previous_response_id = response_id
            return self._finalize_interruption_work_decision(frame, decision, tools)
        request = {
            "model": self._config.model,
            "instructions": instructions,
            "input_items": input_items,
            "schema": InterruptionDecisionSchema,
            "max_output_tokens": self._config.max_output_tokens,
            "temperature": self._config.temperature,
            "reasoning_effort": self._config.reasoning_effort,
        }
        if tools is not None:
            request["tools"] = tools
        decision = self._provider.generate_structured(**request)
        return self._finalize_interruption_work_decision(frame, decision, tools)

    def _finalize_interruption_work_decision(
        self,
        frame: ContextFramePayload,
        decision: InterruptionDecisionSchema,
        tools: ToolSession | None,
    ) -> InterruptionDecisionSchema:
        # carry verified work state through interruption normalization
        started_task = self._started_codex_task(tools)
        submitted_reply = self._submitted_codex_reply(tools)
        if started_task is None and submitted_reply is None:
            return decision.model_copy(update={"codex_work_dispatched": False, "codex_task_started": False})
        update: dict[str, Any] = {
            "work_intent": "delegate",
            "codex_work_dispatched": True,
            "codex_task_started": started_task is not None,
        }
        if submitted_reply is not None:
            update.update(
                {
                    "codex_app_server_id": str(submitted_reply.get("app_server_id") or "") or None,
                    "codex_task_id": str(submitted_reply.get("task_id") or "") or None,
                }
            )
        elif started_task is not None:
            update.update(
                {
                    "codex_app_server_id": str(started_task.get("app_server_id") or "") or None,
                    "codex_task_id": str(started_task.get("task_id") or "") or None,
                }
            )
        if started_task is not None and (decision.action != "reply" or not (decision.reply_text or "").strip()):
            update.update(
                {
                    "interrupt_decision": "accept",
                    "action": "reply",
                    "reply_to_message_id": frame.current_message.message_id,
                    "reply_text": self._CODEX_TASK_STARTED_ACK,
                    "confidence": max(decision.confidence, 0.9),
                }
            )
        return decision.model_copy(update=update)

    def _build_input_items(
        self,
        frame: ContextFramePayload,
        *,
        session_state: SemanticSessionState,
        harness_feedback: dict[str, Any] | None,
        previous_decision: SemanticDecisionSchema | None,
    ) -> list[dict[str, Any]]:
        conversation_prefix = self._build_conversation_prefix(frame, session_state=session_state)
        turn_items = self._semantic_turn_items(
            frame,
            harness_feedback=harness_feedback,
            previous_decision=previous_decision,
        )
        return [*conversation_prefix, *turn_items]

    def _build_conversation_prefix(
        self,
        frame: ContextFramePayload,
        *,
        session_state: SemanticSessionState,
    ) -> list[dict[str, Any]]:
        new_conversation_items = [
            self._conversation_item(message)
            for message in self._visible_messages(frame)
            if message.message_id not in session_state.seen_message_ids
        ]
        for message in self._visible_messages(frame):
            session_state.seen_message_ids.add(message.message_id)

        if not session_state.seeded:
            session_state.input_items.append(
                {
                    "role": "developer",
                    "content": [{"type": "input_text", "text": self._config.system_prompt}],
                }
            )
            session_state.seeded = True
        supports_response_chaining = callable(getattr(self._provider, "generate_structured_with_metadata", None))
        if supports_response_chaining and session_state.previous_response_id is not None:
            conversation_prefix = new_conversation_items
        else:
            session_state.input_items.extend(new_conversation_items)
            conversation_prefix = list(session_state.input_items)
        return conversation_prefix

    def _semantic_turn_items(
        self,
        frame: ContextFramePayload,
        *,
        harness_feedback: dict[str, Any] | None,
        previous_decision: SemanticDecisionSchema | None,
    ) -> list[dict[str, Any]]:
        turn_items = [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": self._turn_context_text(frame)}],
            }
        ]
        if harness_feedback or previous_decision:
            turn_items.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": json.dumps(
                                {
                                    "harness_feedback": harness_feedback,
                                    "previous_rejected_decision": (
                                        previous_decision.model_dump(mode="json") if previous_decision is not None else None
                                    ),
                                },
                                ensure_ascii=False,
                                indent=2,
                            ),
                        }
                    ],
                }
            )
        return turn_items

    def _interruption_turn_items(
        self,
        frame: ContextFramePayload,
        interruption: PendingInterruptionPayload,
        *,
        harness_feedback: dict[str, Any] | None,
        previous_decision: InterruptionDecisionSchema | None,
    ) -> list[dict[str, Any]]:
        turn_items = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": json.dumps(
                            {
                                "session_id": frame.session_id,
                                "chat_id": frame.chat_id,
                                "trigger_message_id": frame.trigger_message_id,
                                "current_message_id": frame.current_message.message_id,
                                "interrupting_message": frame.current_message.model_dump(mode="json"),
                                "pending_interruption": interruption.model_dump(mode="json"),
                                "participants": frame.participants,
                                "engaged_user_ids": frame.engaged_user_ids,
                                "topic_summary": frame.topic_summary,
                                "open_loops": frame.open_loops,
                                "relevant_memories": [memory.model_dump(mode="json") for memory in frame.relevant_memories],
                                "attention_classification": (
                                    frame.attention_classification.model_dump(mode="json")
                                    if frame.attention_classification is not None
                                    else None
                                ),
                                "mood": frame.mood,
                                "fatigue_notice": frame.fatigue_notice,
                                "response_required": frame.response_required,
                                "response_required_reason": frame.response_required_reason,
                                "compacted_facts": frame.compacted_facts,
                                "expanded_memory_ids": frame.expanded_memory_ids,
                                "open_question": frame.open_question.model_dump(mode="json") if frame.open_question is not None else None,
                                "open_questions": [question.model_dump(mode="json") for question in frame.open_questions],
                                "codex_followup": (
                                    frame.codex_followup.model_dump(mode="json")
                                    if frame.codex_followup is not None
                                    else None
                                ),
                                "codex_notification": (
                                    frame.codex_notification.model_dump(mode="json")
                                    if frame.codex_notification is not None
                                    else None
                                ),
                                "linear_task_list": (
                                    frame.linear_task_list.model_dump(mode="json")
                                    if frame.linear_task_list is not None
                                    else None
                                ),
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                    }
                ],
            }
        ]
        if harness_feedback or previous_decision:
            turn_items.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": json.dumps(
                                {
                                    "harness_feedback": harness_feedback,
                                    "previous_rejected_interruption_decision": (
                                        previous_decision.model_dump(mode="json") if previous_decision is not None else None
                                    ),
                                },
                                ensure_ascii=False,
                                indent=2,
                            ),
                        }
                    ],
                }
            )
        return turn_items

    def _session_key(self, frame: ContextFramePayload) -> str:
        if frame.session_id:
            return frame.session_id
        return f"chat:{frame.chat_id}"

    def _visible_messages(self, frame: ContextFramePayload) -> list[ContextFrameMessagePayload]:
        return frame.conversation_window_messages or frame.recent_messages

    def _conversation_item(self, message: ContextFrameMessagePayload) -> dict[str, Any]:
        role = "assistant" if message.is_self else "user"
        text = json.dumps(
            {
                "message_id": message.message_id,
                "sender_id": message.sender_id,
                "sender_name": message.sender_name,
                "is_self": message.is_self,
                "reply_to_message_id": message.reply_to_message_id,
                "reply_to_sender_id": message.reply_to_sender_id,
                "reply_to_sender_name": message.reply_to_sender_name,
                "reply_to_content": message.reply_to_content,
                "source": message.source,
                "content": message.content,
            },
            ensure_ascii=False,
            indent=2,
        )
        if message.is_self:
            return {
                "role": role,
                "content": [
                    {
                        "type": "output_text",
                        "text": text,
                        "annotations": [],
                    }
                ],
            }
        return {
            "role": role,
            "content": [{"type": "input_text", "text": text}],
        }

    def _turn_context_text(self, frame: ContextFramePayload) -> str:
        return json.dumps(
            {
                "session_id": frame.session_id,
                "chat_id": frame.chat_id,
                "trigger_message_id": frame.trigger_message_id,
                "current_message_id": frame.current_message.message_id,
                "recommended_reply_candidate": frame.recommended_reply_candidate,
                "participants": frame.participants,
                "engaged_user_ids": frame.engaged_user_ids,
                "topic_summary": frame.topic_summary,
                "open_loops": frame.open_loops,
                "relevant_memories": [memory.model_dump(mode="json") for memory in frame.relevant_memories],
                "attention_classification": (
                    frame.attention_classification.model_dump(mode="json")
                    if frame.attention_classification is not None
                    else None
                ),
                "mood": frame.mood,
                "fatigue_notice": frame.fatigue_notice,
                "response_required": frame.response_required,
                "response_required_reason": frame.response_required_reason,
                "compacted_facts": frame.compacted_facts,
                "expanded_memory_ids": frame.expanded_memory_ids,
                "open_question": frame.open_question.model_dump(mode="json") if frame.open_question is not None else None,
                "open_questions": [question.model_dump(mode="json") for question in frame.open_questions],
                "codex_followup": frame.codex_followup.model_dump(mode="json") if frame.codex_followup is not None else None,
                "codex_notification": (
                    frame.codex_notification.model_dump(mode="json") if frame.codex_notification is not None else None
                ),
                "linear_task_list": (
                    frame.linear_task_list.model_dump(mode="json") if frame.linear_task_list is not None else None
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
