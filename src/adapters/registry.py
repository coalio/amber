from __future__ import annotations

from src.adapters.base import BaseAdapter


class AdapterRegistry:
    def __init__(self, adapters: list[BaseAdapter] | None = None) -> None:
        self._adapters: dict[str, BaseAdapter] = {}
        for adapter in adapters or []:
            self.register(adapter)

    def register(self, adapter: BaseAdapter) -> None:
        self._adapters[adapter.name] = adapter

    def get(self, name: str) -> BaseAdapter | None:
        return self._adapters.get(name)

    def require(self, name: str) -> BaseAdapter:
        adapter = self.get(name)
        if adapter is None:
            raise RuntimeError(f"Missing adapter: {name}")
        return adapter
