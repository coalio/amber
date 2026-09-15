from __future__ import annotations

from collections.abc import Callable

from src.events.delivery import TaskOrigin, ToolInvocation


class DeliveryPolicy:
    def __init__(
        self, origin_for_chat: Callable[[int | str], TaskOrigin | None],
        candidates: Callable[[TaskOrigin], list[dict]],
    ) -> None:
        self._origin_for_chat = origin_for_chat
        self._candidates = candidates

    def origin_for(self, invocation: ToolInvocation) -> TaskOrigin | None:
        return invocation.task_origin or self._origin_for_chat(invocation.chat_id)

    def file_destination(self, invocation: ToolInvocation | None, requested_chat_id: int | str) -> int | str:
        # inherited delivery authority constrains model-selected file recipients
        origin = self.origin_for(invocation) if invocation is not None else None
        if origin is None:
            return requested_chat_id
        candidates = self._candidates(origin)
        if len(candidates) != 1:
            raise RuntimeError("The task delivery route is unavailable.")
        return candidates[0]["chat_id"]
