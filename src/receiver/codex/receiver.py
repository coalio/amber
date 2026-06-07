from __future__ import annotations

from collections.abc import Iterable

from src.adapters.codex import CodexAdapter, CodexNotification, CodexQuestion
from src.attention.memory.store import MemoryStore
from src.events.bus import EventBus, emitter_context
from src.events.codex import (
    CodexNotificationPayload,
    CodexNotificationReceivedEvent,
    CodexQuestionPayload,
    CodexQuestionReceivedEvent,
)
from src.utils.time import utc_now


class CodexReceiver:
    def __init__(
        self,
        adapter: CodexAdapter,
        memory_store: MemoryStore,
        allowlisted_sender_ids: Iterable[str],
    ) -> None:
        self._adapter = adapter
        self._memory_store = memory_store
        self._allowlisted_sender_ids = tuple(str(sender_id).removeprefix("user") for sender_id in allowlisted_sender_ids)
        self._question_subscription_id: str | None = None
        self._notification_subscription_id: str | None = None

    def register(self) -> None:
        if self._question_subscription_id is None:
            self._question_subscription_id = self._adapter.subscribe_questions(self._handle_question)
        if self._notification_subscription_id is None:
            self._notification_subscription_id = self._adapter.subscribe_notifications(self._handle_notification)

    def unregister(self) -> None:
        if self._question_subscription_id is not None:
            self._adapter.unsubscribe(self._question_subscription_id)
            self._question_subscription_id = None
        if self._notification_subscription_id is not None:
            self._adapter.unsubscribe(self._notification_subscription_id)
            self._notification_subscription_id = None

    def _handle_question(self, question: CodexQuestion) -> None:
        candidates = self._allowlisted_candidates()
        with emitter_context("receiver.codex"):
            EventBus.emit(
                CodexQuestionReceivedEvent(
                    chat_id=f"codex:{question.task_id}",
                    payload=CodexQuestionPayload(
                        app_server_id=question.app_server_id,
                        task_id=question.task_id,
                        tool_call_id=question.tool_call_id,
                        questions=list(question.questions),
                        task_description=question.task_description,
                        context=dict(question.context),
                        candidate_people=candidates,
                        created_at=utc_now(),
                    ),
                )
            )

    def _handle_notification(self, notification: CodexNotification) -> None:
        candidates = self._allowlisted_candidates()
        with emitter_context("receiver.codex"):
            EventBus.emit(
                CodexNotificationReceivedEvent(
                    chat_id=f"codex:{notification.task_id}",
                    payload=CodexNotificationPayload(
                        app_server_id=notification.app_server_id,
                        task_id=notification.task_id,
                        notification_id=notification.notification_id,
                        message=notification.message,
                        task_description=notification.task_description,
                        context=dict(notification.context),
                        candidate_people=candidates,
                        created_at=utc_now(),
                    ),
                )
            )

    def _allowlisted_candidates(self) -> list[dict]:
        return [
            candidate.model_dump(mode="json")
            for candidate in self._memory_store.list_allowlisted_profiles(self._allowlisted_sender_ids)
        ]
