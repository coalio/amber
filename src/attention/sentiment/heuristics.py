from __future__ import annotations

from src.attention.constants import NEGATIVE_TOKENS, POSITIVE_TOKENS
from src.attention.utils import tokenize
from src.events.receiver import TelegramMessagePayload


def compute_sentiment_delta(message: TelegramMessagePayload, *, directed_at_amber: bool) -> float:
    if not directed_at_amber:
        return 0.0
    text = message.content.lower()
    tokens = tokenize(text)
    delta = 0.0
    for token in POSITIVE_TOKENS:
        if token in text or token in tokens:
            delta += 0.7
    for token in NEGATIVE_TOKENS:
        if token in text or token in tokens:
            delta -= 1.0
    return delta

