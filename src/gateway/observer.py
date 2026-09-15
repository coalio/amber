from __future__ import annotations

from src.events.bus import EventBus
from src.state.gateway import GatewayStore, SESSION_RE


class GatewayObserver:
    """Translate runtime observations into the operator capture protocol."""

    def __init__(self, store: GatewayStore) -> None:
        self._store = store

    def register(self, adapter) -> None:
        for name in ("ContextFrameReadyEvent", "SemanticDecisionMadeEvent", "OutboundMessageSentEvent"):
            EventBus.subscribe(name, self._observe)
        adapter.subscribe_task_completed(self._task_completed)

    def _observe(self, event) -> None:
        # observe only capture conversations without changing pipeline decisions
        payload = event.payload
        session = str(payload.chat_id)
        if not SESSION_RE.fullmatch(session):
            return
        if event.name == "ContextFrameReadyEvent":
            self._store.record(session, "frame", message_ids=payload.visible_surfaced_message_ids,
                               trigger_message_id=payload.trigger_message_id)
        elif event.name == "SemanticDecisionMadeEvent":
            self._store.record(session, "decision", action=payload.action, task_id=payload.codex_task_id,
                               acknowledged=payload.work_acknowledged,
                               error_code=payload.codex_work_error_code, notes=payload.notes)
        else:
            self._store.record(session, "turn_complete", task_id=payload.codex_task_id, no_send=payload.no_send,
                               trigger_message_id=payload.trigger_message_id)

    def _task_completed(self, task) -> None:
        if task.origin is not None and SESSION_RE.fullmatch(task.origin.delivery_route):
            self._store.record(task.origin.delivery_route, "task_complete", task_id=task.task_id, status=task.status)
