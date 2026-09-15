from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from src.config.config import Settings


DEFAULT_REPOSITORY = "https://github.com/coalio/codex-hooks"
DEFAULT_REVISION = "1416ddb8de550eac0889c4dae08a514707a7c999"


@dataclass(frozen=True)
class HookConfig:
    repository: str | None = DEFAULT_REPOSITORY
    revision: str = DEFAULT_REVISION

    def __post_init__(self) -> None:
        if self.repository is None:
            return
        if not self.repository.strip():
            raise RuntimeError("hooks.repository must be a Git repository URL or 'none'.")
        if not self.revision.strip() or self.revision.startswith("-"):
            raise RuntimeError("hooks.revision must be a branch, tag, or commit when hooks are enabled.")
        if any(character.isspace() for character in self.revision):
            raise RuntimeError("hooks.revision must not contain whitespace.")

    @classmethod
    def from_settings(cls, settings: Settings) -> HookConfig:
        return cls(
            repository=settings.hooks_repository,
            revision=settings.hooks_revision,
        )
