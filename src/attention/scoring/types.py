from __future__ import annotations

from dataclasses import dataclass


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
