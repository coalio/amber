from __future__ import annotations

import os
from collections import OrderedDict
from typing import Any, Protocol

from src.attention.constants import (
    ATTENTION_LABEL_HYPOTHESES,
    DEFAULT_ATTENTION_MODEL,
    DEFAULT_ATTENTION_MODEL_REVISION,
    NEGATIVE_ATTENTION_LABELS,
    POSITIVE_ATTENTION_LABELS,
)
from src.attention.scoring.types import AttentionModelClassification


class AttentionLabelInference(Protocol):
    def score_labels(self, text: str, hypotheses: dict[str, str], *, max_length: int) -> dict[str, float]: ...


class AttentionPolicyScorer:
    """Apply Amber's attention policy to label-inference results."""

    def __init__(
        self,
        inference: AttentionLabelInference,
        model_name: str | None = None,
        *,
        revision: str | None = None,
        max_length: int = 512,
        cache_size: int = 1024,
        warm: bool = True,
    ) -> None:
        self.model_name = model_name or os.getenv("AMBER_ATTENTION_MODEL") or DEFAULT_ATTENTION_MODEL
        self.model_revision = revision or os.getenv("AMBER_ATTENTION_MODEL_REVISION") or DEFAULT_ATTENTION_MODEL_REVISION
        self.max_length = max_length
        self._cache_size = cache_size
        self._cache: OrderedDict[str, AttentionModelClassification] = OrderedDict()
        self._inference = inference
        if warm:
            self.classify_text("hello")

    def score(self, feature_row: dict[str, Any]) -> float:
        return self.classify(feature_row).score

    def classify(self, feature_row: dict[str, Any]) -> AttentionModelClassification:
        return self.classify_text(str(feature_row.get("focus_content") or ""))

    def classify_text(self, text: str) -> AttentionModelClassification:
        normalized = " ".join(text.split())
        if not normalized:
            return self._empty_classification()
        cached = self._cache.get(normalized)
        if cached is not None:
            self._cache.move_to_end(normalized)
            return cached
        labels = self._score_labels(normalized)
        classification = AttentionModelClassification(
            score=self._attention_score(labels),
            labels=labels,
            top_labels=self._top_labels(labels),
            tone=self._tone(labels),
            model_name=self.model_name,
            model_revision=self.model_revision,
        )
        self._cache[normalized] = classification
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return classification

    def _score_labels(self, text: str) -> dict[str, float]:
        return self._inference.score_labels(
            text,
            ATTENTION_LABEL_HYPOTHESES,
            max_length=self.max_length,
        )

    def _attention_score(self, labels: dict[str, float]) -> float:
        positive = max(labels[label] for label in POSITIVE_ATTENTION_LABELS)
        conversational = max(labels["casual_friendly_chat"], labels["romantic_or_flirtatious"])
        negative = max(labels[label] for label in NEGATIVE_ATTENTION_LABELS)
        score = 0.1 + (positive * 0.82) + (conversational * 0.08) - (negative * 0.52)
        return max(0.0, min(score, 1.0))

    def _top_labels(self, labels: dict[str, float]) -> list[str]:
        return [
            label
            for label, value in sorted(labels.items(), key=lambda item: item[1], reverse=True)
            if value >= 0.45
        ][:5]

    def _tone(self, labels: dict[str, float]) -> str:
        negative = max(labels[label] for label in NEGATIVE_ATTENTION_LABELS)
        if negative >= 0.65:
            return "annoyed"
        if labels["professional_or_technical"] >= 0.55 or labels["direct_question_or_request"] >= 0.55:
            return "calm"
        return "joking"

    def _empty_classification(self) -> AttentionModelClassification:
        labels = {label: 0.0 for label in ATTENTION_LABEL_HYPOTHESES}
        return AttentionModelClassification(
            score=0.0,
            labels=labels,
            top_labels=[],
            tone="calm",
            model_name=self.model_name,
            model_revision=self.model_revision,
        )
