from __future__ import annotations

import os
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from src.attention.constants import (
    ATTENTION_LABEL_HYPOTHESES,
    DEFAULT_ATTENTION_MODEL,
    DEFAULT_ATTENTION_MODEL_REVISION,
    NEGATIVE_ATTENTION_LABELS,
    POSITIVE_ATTENTION_LABELS,
)


@dataclass(frozen=True)
class AttentionModelClassification:
    score: float
    labels: dict[str, float]
    top_labels: list[str]
    tone: str
    model_name: str
    model_revision: str | None

    @property
    def reasons(self) -> list[str]:
        return [f"modernbert:{label}" for label in self.top_labels[:3]]


class AttentionPolicyScorer:
    """ModernBERT NLI scorer used as the learned Attention signal.

    The model is trained as an entailment classifier. Each candidate label is
    scored by pairing the inbound message with a short hypothesis sentence.
    """

    def __init__(
        self,
        model_name: str | None = None,
        *,
        revision: str | None = None,
        device: str | None = None,
        max_length: int = 512,
        cache_size: int = 1024,
        warm: bool = True,
    ) -> None:
        self.model_name = model_name or os.getenv("AMBER_ATTENTION_MODEL") or DEFAULT_ATTENTION_MODEL
        self.model_revision = revision or os.getenv("AMBER_ATTENTION_MODEL_REVISION") or DEFAULT_ATTENTION_MODEL_REVISION
        self.max_length = max_length
        self._cache_size = cache_size
        self._cache: OrderedDict[str, AttentionModelClassification] = OrderedDict()
        self._torch, self._tokenizer, self._model = self._load_model(device)
        self._entailment_index = self._resolve_entailment_index()
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

    def _load_model(self, requested_device: str | None):
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                "ModernBERT Attention scoring requires `torch` and `transformers`. "
                "Install project requirements before running the live runtime."
            ) from exc
        device = requested_device or os.getenv("AMBER_ATTENTION_DEVICE")
        if not device:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        tokenizer = AutoTokenizer.from_pretrained(self.model_name, revision=self.model_revision)
        model = AutoModelForSequenceClassification.from_pretrained(self.model_name, revision=self.model_revision)
        model.to(device)
        model.eval()
        return torch, tokenizer, model

    def _resolve_entailment_index(self) -> int:
        label2id = getattr(self._model.config, "label2id", {}) or {}
        for label, index in label2id.items():
            if str(label).lower() == "entailment":
                return int(index)
        return 0

    def _score_labels(self, text: str) -> dict[str, float]:
        labels = list(ATTENTION_LABEL_HYPOTHESES)
        hypotheses = [ATTENTION_LABEL_HYPOTHESES[label] for label in labels]
        inputs = self._tokenizer(
            [text] * len(hypotheses),
            hypotheses,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=self.max_length,
        )
        device = next(self._model.parameters()).device
        inputs = {key: value.to(device) for key, value in inputs.items()}
        with self._torch.inference_mode():
            logits = self._model(**inputs).logits
            probabilities = self._torch.softmax(logits.float(), dim=-1)[:, self._entailment_index]
        values = probabilities.detach().cpu().tolist()
        return {label: round(float(value), 4) for label, value in zip(labels, values)}

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
