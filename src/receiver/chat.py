from __future__ import annotations

from typing import Protocol

from src.events.receiver import TelegramMessageReceivedEvent, TelegramTypingUpdatedEvent


class NormalizedChatIngress(Protocol):
    """Public archive-first ingress shared by Telegram and operator simulations."""

    async def receive(self, event: TelegramMessageReceivedEvent) -> None: ...

    async def receive_typing(self, event: TelegramTypingUpdatedEvent) -> None: ...
