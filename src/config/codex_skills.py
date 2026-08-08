from __future__ import annotations

from pathlib import Path


CODEX_DEVELOPMENT_SKILL = "codex-development"
CODEX_PR_REVIEWS_SKILL = "codex-pr-reviews"
PYTHON_STYLE_RULES_SKILL = "python-style-rules"
CODEX_SKILL_NAMES = (
    CODEX_DEVELOPMENT_SKILL,
    CODEX_PR_REVIEWS_SKILL,
    PYTHON_STYLE_RULES_SKILL,
)


def codex_skill_paths(skill_dir: Path) -> tuple[Path, ...]:
    return tuple(skill_dir / name / "SKILL.md" for name in CODEX_SKILL_NAMES)
