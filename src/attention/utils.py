from __future__ import annotations

import re
from collections import Counter

from src.events.receiver import TelegramMessagePayload


WORD_RE = re.compile(r"[a-zA-Z0-9']+")


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in WORD_RE.findall(text)]


def contains_question_intent(message: TelegramMessagePayload) -> bool:
    text = message.content.strip().lower()
    if "?" in text:
        return True
    tokens = tokenize(text)
    return bool(tokens and tokens[0] in {"what", "why", "how", "when", "where", "who", "can", "should", "would", "do", "did"})


def novelty_score(current: TelegramMessagePayload, recent_messages: list[TelegramMessagePayload]) -> float:
    current_tokens = set(tokenize(current.content))
    if not current_tokens:
        return 0.0
    if not recent_messages:
        return 1.0
    overlap: list[float] = []
    for message in recent_messages:
        tokens = set(tokenize(message.content))
        if not tokens:
            continue
        overlap.append(len(current_tokens & tokens) / max(len(current_tokens | tokens), 1))
    if not overlap:
        return 1.0
    return 1.0 - max(overlap)


def derive_topic_summary(messages: list[str], max_terms: int = 6) -> str:
    counts = Counter(token for text in messages for token in tokenize(text) if len(token) > 2)
    if not counts:
        return "No stable topic yet."
    terms = ", ".join(term for term, _ in counts.most_common(max_terms))
    return f"Current topic signals: {terms}."
