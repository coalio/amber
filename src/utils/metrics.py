from __future__ import annotations

from collections import Counter, defaultdict
from threading import Lock


class MetricsRegistry:
    _instance: "MetricsRegistry | None" = None
    _lock = Lock()

    def __init__(self) -> None:
        self._counters = Counter()
        self._observations: dict[str, list[float]] = defaultdict(list)
        self._data_lock = Lock()

    @classmethod
    def instance(cls) -> "MetricsRegistry":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def increment(self, key: str, amount: int = 1) -> None:
        with self._data_lock:
            self._counters[key] += amount

    def observe(self, key: str, value: float) -> None:
        with self._data_lock:
            self._observations[key].append(value)

    def snapshot(self) -> dict[str, object]:
        with self._data_lock:
            return {
                "counters": dict(self._counters),
                "observations": {key: list(values) for key, values in self._observations.items()},
            }

