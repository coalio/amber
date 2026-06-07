from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from threading import Lock, Timer
from typing import Any


class RuntimeScheduler:
    _instance: "RuntimeScheduler | None" = None
    _instance_lock = Lock()

    def __init__(self) -> None:
        self._timers: dict[str, Timer] = {}
        self._lock = Lock()

    @classmethod
    def instance(cls) -> "RuntimeScheduler":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def schedule_after(self, key: str, delay_seconds: float, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        self.cancel(key)
        timer = Timer(delay_seconds, callback, args=args, kwargs=kwargs)
        timer.daemon = True
        with self._lock:
            self._timers[key] = timer
        timer.start()

    def schedule_at(self, key: str, when: datetime, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        delay = max((when - datetime.now(tz=when.tzinfo)).total_seconds(), 0.0)
        self.schedule_after(key, delay, callback, *args, **kwargs)

    def cancel(self, key: str) -> None:
        with self._lock:
            timer = self._timers.pop(key, None)
        if timer is not None:
            timer.cancel()

    def shutdown(self) -> None:
        with self._lock:
            keys = list(self._timers)
        for key in keys:
            self.cancel(key)

