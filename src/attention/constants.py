from __future__ import annotations


POSITIVE_TOKENS = {
    "thanks",
    "thank you",
    "love you",
    "based",
    "cute",
    "nice",
    "good",
    "mwah",
}

NEGATIVE_TOKENS = {
    "stfu",
    "fuck you",
    "idiot",
    "retard",
    "bitch",
    "annoying",
    "hate you",
}

QUESTION_PREFIXES = {
    "what",
    "why",
    "how",
    "when",
    "where",
    "who",
    "can",
    "should",
    "would",
    "do",
    "did",
}

DEFAULT_ATTENTION_MODEL = "MoritzLaurer/ModernBERT-base-zeroshot-v2.0"
DEFAULT_ATTENTION_MODEL_REVISION = "d421c4545a438fd006fb43f8b981c5d908faa1e1"

ATTENTION_LABEL_HYPOTHESES = {
    "worth_replying_to": "A human should respond to this message.",
    "direct_question_or_request": "The message asks a question or requests help.",
    "professional_or_technical": "The message discusses work, software, engineering, or another technical topic.",
    "personal_or_emotional": "The message shares a personal feeling, situation, or emotionally meaningful detail.",
    "casual_friendly_chat": "The message is normal casual conversation.",
    "romantic_or_flirtatious": "The message is affectionate, romantic, or flirtatious.",
    "spam_or_advertisement": "The message is unsolicited spam, advertising, self-promotion, or a scam.",
    "toxic_or_insulting": "The message is toxic, insulting, abusive, or a personal attack, but it is not racist.",
    "political_or_ideological": "The message discusses politics, government, elections, ideology, or public policy.",
    "low_value_trash_talk": "The message is low-value noise, trash talk, or a throwaway reaction.",
}

POSITIVE_ATTENTION_LABELS = (
    "worth_replying_to",
    "direct_question_or_request",
    "professional_or_technical",
    "personal_or_emotional",
    "casual_friendly_chat",
    "romantic_or_flirtatious",
)

NEGATIVE_ATTENTION_LABELS = (
    "spam_or_advertisement",
    "toxic_or_insulting",
    "political_or_ideological",
    "low_value_trash_talk",
)
