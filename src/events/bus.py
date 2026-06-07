from __future__ import annotations

import contextlib
from collections import defaultdict
from collections.abc import Callable
from contextvars import ContextVar
from collections import deque
from threading import Lock
from typing import Any

from src.events.base import BaseEvent
from src.events.observability import build_event_dispatch_context
from src.utils.ids import new_event_id
from src.utils.logging import get_logger, logged_entrypoint
from src.utils.time import utc_now


Handler = Callable[[BaseEvent], None]
_EMITTER_ORIGIN: ContextVar[str | None] = ContextVar("event_origin", default=None)


class EventBus:
    _handlers: dict[str, list[tuple[str, Handler]]] = defaultdict(list)
    _queue: deque[BaseEvent] = deque()
    _lock = Lock()
    _dispatching = False
    _logger = get_logger("amber.event_bus")

    @classmethod
    def _dispatch(cls, event: BaseEvent) -> None:
        handlers = list(cls._handlers.get(event.name, []))
        cls._logger.info(
            "event.dispatch",
            extra={
                "event": event.name,
                "context": build_event_dispatch_context(event, handler_count=len(handlers)),
            },
        )
        for subscription_id, handler in handlers:
            try:
                handler(event)
            except Exception:
                cls._logger.exception(
                    "event.handler_failed",
                    extra={
                        "event": event.name,
                        "context": {
                            "event_id": event.event_id,
                            "correlation_id": event.correlation_id,
                            "subscription_id": subscription_id,
                            "chat_id": event.chat_id,
                        },
                    },
                )

    @classmethod
    @logged_entrypoint("event_bus.emit")
    def emit(cls, event: BaseEvent) -> None:
        if event.timestamp is None:
            event.timestamp = utc_now()
        if not event.origin:
            event.origin = _EMITTER_ORIGIN.get() or "system"
        if not event.event_id:
            event.event_id = new_event_id()
        with cls._lock:
            cls._queue.append(event)
            if cls._dispatching:
                return
            cls._dispatching = True
        try:
            while True:
                with cls._lock:
                    if not cls._queue:
                        cls._dispatching = False
                        return
                    next_event = cls._queue.popleft()
                cls._dispatch(next_event)
        finally:
            with cls._lock:
                cls._dispatching = False

    @classmethod
    @logged_entrypoint("event_bus.subscribe")
    def subscribe(cls, event_name: str, handler: Handler) -> str:
        subscription_id = new_event_id()
        with cls._lock:
            cls._handlers[event_name].append((subscription_id, handler))
        return subscription_id

    @classmethod
    @logged_entrypoint("event_bus.unsubscribe")
    def unsubscribe(cls, subscription_id: str) -> None:
        with cls._lock:
            for event_name, handlers in list(cls._handlers.items()):
                cls._handlers[event_name] = [(sub_id, handler) for sub_id, handler in handlers if sub_id != subscription_id]

    @classmethod
    def wait_until_idle(cls, timeout_seconds: float | None = None) -> None:
        return None

    @classmethod
    def reset_for_tests(cls) -> None:
        with cls._lock:
            cls._handlers = defaultdict(list)
            cls._queue = deque()
            cls._dispatching = False


@contextlib.contextmanager
def emitter_context(origin: str) -> Any:
    token = _EMITTER_ORIGIN.set(origin)
    try:
        yield
    finally:
        _EMITTER_ORIGIN.reset(token)
