from __future__ import annotations

import os

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from src.attention.constants import DEFAULT_ATTENTION_MODEL, DEFAULT_ATTENTION_MODEL_REVISION
from src.attention.scoring.policy import AttentionPolicyScorer as BaseAttentionPolicyScorer


class LocalModernBertInference:
    """In-process ModernBERT inference for source development."""

    def __init__(self, model_name: str, model_revision: str, *, device: str | None = None) -> None:
        # use the configured accelerator only for source installs that own their Python environment
        resolved_device = device or os.getenv("AMBER_ATTENTION_DEVICE")
        if not resolved_device:
            resolved_device = "cuda" if torch.cuda.is_available() else "cpu"
        tokenizer = AutoTokenizer.from_pretrained(model_name, revision=model_revision)
        model = AutoModelForSequenceClassification.from_pretrained(model_name, revision=model_revision)
        model.to(resolved_device)
        model.eval()

        self._torch = torch
        self._tokenizer = tokenizer
        self._model = model
        self._entailment_index = self._resolve_entailment_index()

    def score_labels(self, text: str, hypotheses: dict[str, str], *, max_length: int) -> dict[str, float]:
        labels = list(hypotheses)
        inputs = self._tokenizer(
            [text] * len(labels),
            [hypotheses[label] for label in labels],
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=max_length,
        )
        device = next(self._model.parameters()).device
        inputs = {key: value.to(device) for key, value in inputs.items()}
        with self._torch.inference_mode():
            logits = self._model(**inputs).logits
            probabilities = self._torch.softmax(logits.float(), dim=-1)[:, self._entailment_index]
        values = probabilities.detach().cpu().tolist()
        return {label: round(float(value), 4) for label, value in zip(labels, values)}

    def _resolve_entailment_index(self) -> int:
        label2id = getattr(self._model.config, "label2id", {}) or {}
        for label, index in label2id.items():
            if str(label).lower() == "entailment":
                return int(index)
        return 0


class AttentionPolicyScorer(BaseAttentionPolicyScorer):
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
        resolved_model = model_name or os.getenv("AMBER_ATTENTION_MODEL") or DEFAULT_ATTENTION_MODEL
        resolved_revision = (
            revision or os.getenv("AMBER_ATTENTION_MODEL_REVISION") or DEFAULT_ATTENTION_MODEL_REVISION
        )
        inference = LocalModernBertInference(
            resolved_model,
            resolved_revision,
            device=device,
        )
        super().__init__(
            inference,
            resolved_model,
            revision=resolved_revision,
            max_length=max_length,
            cache_size=cache_size,
            warm=warm,
        )
