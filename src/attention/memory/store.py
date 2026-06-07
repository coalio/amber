from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.events.attention import MemoryCardPayload, StickerSignalPayload
from src.events.receiver import TelegramMessagePayload
from src.utils.files import append_jsonl, read_json, write_json
from src.utils.ids import new_memory_id
from src.utils.time import clamp, utc_now


PREFERENCE_RE = re.compile(
    r"\b(i am|i'm|my name is|i like|i love|i hate|my favorite|i work with|i work on)\b",
    re.IGNORECASE,
)


class UserProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    telegram_id: str
    display_name: str
    known_aliases: list[str] = Field(default_factory=list)
    expertise_tags: list[str] = Field(default_factory=list)
    project_owner_tags: list[str] = Field(default_factory=list)
    sentiment: float = 5.0
    last_seen_timestamp: datetime | None = None
    memory_count: int = 0
    last_memory_write_at: datetime | None = None


class UserProfileSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sender_id: str
    chat_id: int | str
    display_name: str
    known_aliases: list[str] = Field(default_factory=list)
    expertise_tags: list[str] = Field(default_factory=list)
    project_owner_tags: list[str] = Field(default_factory=list)


class MemoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str
    created_at: datetime
    updated_at: datetime
    source_message_ids: list[int]
    text: str
    tags: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    salience: float = 0.5


class StickerRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sticker_file_identifier: str
    sticker_set_identifier: str | None = None
    sender_identifiers: list[str] = Field(default_factory=list)
    sampled_preceding_segment: list[str] = Field(default_factory=list)
    inferred_tones: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    topic_tags: list[str] = Field(default_factory=list)
    first_seen_timestamp: datetime
    last_seen_timestamp: datetime
    usage_count: int = 0


class MemoryStore:
    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        self._stickers_dir = base_dir / "stickers"
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._stickers_dir.mkdir(parents=True, exist_ok=True)

    def _folder_name(self, sender_id: str, display_name: str) -> str:
        ascii_name = "".join(char for char in display_name if char.isascii() and (char.isalnum() or char in {" ", "-", "_"})).strip()
        if ascii_name:
            return f"{ascii_name} {sender_id}"
        return f"user {sender_id}"

    def _user_dir(self, sender_id: str, display_name: str) -> Path:
        return self._base_dir / self._folder_name(sender_id, display_name)

    def _user_profile_path(self, sender_id: str, display_name: str) -> Path:
        return self._user_dir(sender_id, display_name) / "user.json"

    def _memory_path(self, sender_id: str, display_name: str) -> Path:
        return self._user_dir(sender_id, display_name) / "memories.jsonl"

    def _profile_paths(self) -> list[Path]:
        return sorted(self._base_dir.glob("*/user.json"))

    def _load_profile_by_sender_id(self, sender_id: str) -> tuple[UserProfile, Path] | None:
        normalized = sender_id.removeprefix("user")
        for path in self._profile_paths():
            payload = read_json(path, None)
            if payload is None:
                continue
            profile = UserProfile.model_validate(payload)
            if profile.telegram_id.removeprefix("user") == normalized:
                return profile, path
        return None

    def _write_memories(self, sender_id: str, display_name: str, memories: list[MemoryEntry]) -> None:
        path = self._memory_path(sender_id, display_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not memories:
            path.write_text("", encoding="utf-8")
            return
        payload = "\n".join(entry.model_dump_json() for entry in memories) + "\n"
        path.write_text(payload, encoding="utf-8")

    def _load_profile(self, sender_id: str, display_name: str) -> UserProfile:
        path = self._user_profile_path(sender_id, display_name)
        payload = read_json(path, None)
        if payload is None:
            profile = UserProfile(telegram_id=sender_id, display_name=display_name, known_aliases=[display_name])
            write_json(path, profile.model_dump(mode="json"))
            return profile
        return UserProfile.model_validate(payload)

    def touch_user(self, sender_id: str, display_name: str, timestamp: datetime) -> UserProfile:
        profile = self._load_profile(sender_id, display_name)
        aliases = set(profile.known_aliases)
        aliases.add(display_name)
        profile.display_name = display_name
        profile.known_aliases = sorted(aliases)
        profile.last_seen_timestamp = timestamp
        write_json(self._user_profile_path(sender_id, display_name), profile.model_dump(mode="json"))
        return profile

    def list_allowlisted_profiles(self, allowlisted_sender_ids: set[str]) -> list[UserProfileSummary]:
        summaries: list[UserProfileSummary] = []
        normalized_allowlist = {sender_id.removeprefix("user") for sender_id in allowlisted_sender_ids}
        for sender_id in sorted(normalized_allowlist):
            existing = self._load_profile_by_sender_id(sender_id)
            if existing is None:
                profile = UserProfile(telegram_id=sender_id, display_name=f"user {sender_id}", known_aliases=[])
            else:
                profile, _ = existing
            summaries.append(
                UserProfileSummary(
                    sender_id=profile.telegram_id,
                    chat_id=int(profile.telegram_id) if profile.telegram_id.isdigit() else profile.telegram_id,
                    display_name=profile.display_name,
                    known_aliases=list(profile.known_aliases),
                    expertise_tags=list(profile.expertise_tags),
                    project_owner_tags=list(profile.project_owner_tags),
                )
            )
        return summaries

    def read_user_memories(
        self,
        sender_id: str,
        *,
        query: str | None = None,
        limit: int = 10,
    ) -> dict[str, object]:
        existing = self._load_profile_by_sender_id(sender_id)
        if existing is None:
            profile = UserProfile(telegram_id=sender_id, display_name=f"user {sender_id}", known_aliases=[])
        else:
            profile, _ = existing
        memories = self._iter_memories(profile.telegram_id, profile.display_name)
        if query:
            query_terms = {token.lower() for token in re.findall(r"[a-zA-Z0-9']+", query)}
            scored: list[tuple[float, MemoryEntry]] = []
            for entry in memories:
                memory_terms = set(entry.tags) | {token.lower() for token in re.findall(r"[a-zA-Z0-9']+", entry.text)}
                score = len(query_terms & memory_terms) + entry.salience + entry.confidence
                if score > 0:
                    scored.append((score, entry))
            scored.sort(key=lambda item: item[0], reverse=True)
            memories = [entry for score, entry in scored]
        return {
            "profile": profile.model_dump(mode="json"),
            "memories": [entry.model_dump(mode="json") for entry in memories[: max(limit, 0)]],
        }

    def create_memory(
        self,
        sender_id: str,
        display_name: str,
        text: str,
        tags: list[str],
        *,
        source_message_id: int | None = None,
        timestamp: datetime | None = None,
    ) -> MemoryEntry:
        cleaned = " ".join(text.split()).strip()
        if not cleaned:
            raise ValueError("Memory text must be non-empty.")
        created_at = timestamp or utc_now()
        entry = MemoryEntry(
            memory_id=new_memory_id(),
            created_at=created_at,
            updated_at=created_at,
            source_message_ids=[] if source_message_id is None else [source_message_id],
            text=cleaned,
            tags=[tag.strip() for tag in tags if tag.strip()],
            confidence=0.8,
            salience=0.7,
        )
        append_jsonl(self._memory_path(sender_id, display_name), [entry.model_dump(mode="json")])
        profile = self._load_profile(sender_id, display_name)
        profile.memory_count += 1
        profile.last_memory_write_at = entry.created_at
        write_json(self._user_profile_path(sender_id, display_name), profile.model_dump(mode="json"))
        return entry

    def update_profile_tags(
        self,
        sender_id: str,
        display_name: str,
        *,
        expertise_tags: list[str] | None = None,
        project_owner_tags: list[str] | None = None,
        timestamp: datetime | None = None,
    ) -> UserProfile:
        profile = self._load_profile(sender_id, display_name)
        if expertise_tags is not None:
            profile.expertise_tags = sorted({*profile.expertise_tags, *[tag.strip() for tag in expertise_tags if tag.strip()]})
        if project_owner_tags is not None:
            profile.project_owner_tags = sorted({*profile.project_owner_tags, *[tag.strip() for tag in project_owner_tags if tag.strip()]})
        profile.last_seen_timestamp = timestamp or utc_now()
        write_json(self._user_profile_path(sender_id, display_name), profile.model_dump(mode="json"))
        return profile

    def adjust_sentiment(self, sender_id: str, display_name: str, delta: float, multiplier: float, timestamp: datetime) -> UserProfile:
        profile = self._load_profile(sender_id, display_name)
        profile.last_seen_timestamp = timestamp
        profile.sentiment = clamp(profile.sentiment + (delta * multiplier), 0.0, 10.0)
        write_json(self._user_profile_path(sender_id, display_name), profile.model_dump(mode="json"))
        return profile

    def maybe_write_memory(self, message: TelegramMessagePayload) -> MemoryEntry | None:
        content = message.content.strip()
        if message.attachment.media_type == "sticker" or len(content) < 12:
            return None
        if not PREFERENCE_RE.search(content):
            return None
        existing = self.retrieve(message, limit=10)
        lowered = content.lower()
        for memory in existing:
            if lowered == memory.text.lower():
                return None
        entry = MemoryEntry(
            memory_id=new_memory_id(),
            created_at=utc_now(),
            updated_at=utc_now(),
            source_message_ids=[message.message_id],
            text=content,
            tags=[token for token in re.findall(r"[a-zA-Z0-9']+", lowered)[:6]],
            confidence=0.75,
            salience=0.65,
        )
        path = self._memory_path(message.sender.id, message.sender.name)
        append_jsonl(path, [entry.model_dump(mode="json")])
        profile = self._load_profile(message.sender.id, message.sender.name)
        profile.memory_count += 1
        profile.last_memory_write_at = entry.created_at
        write_json(self._user_profile_path(message.sender.id, message.sender.name), profile.model_dump(mode="json"))
        return entry

    def rewrite_memory(
        self,
        sender_id: str,
        display_name: str,
        memory_id: str,
        text: str,
        tags: list[str],
        *,
        timestamp: datetime | None = None,
    ) -> MemoryCardPayload | None:
        rewritten_text = " ".join(text.split()).strip()
        rewritten_tags = [item.strip() for item in tags if item.strip()]
        if not rewritten_text:
            raise ValueError("Rewritten memory text must be non-empty.")
        if not rewritten_tags:
            raise ValueError("Rewritten memory tags must be non-empty.")
        memories = self._iter_memories(sender_id, display_name)
        updated_at = timestamp or utc_now()
        updated_entry: MemoryEntry | None = None
        for index, entry in enumerate(memories):
            if entry.memory_id != memory_id:
                continue
            updated_entry = entry.model_copy(
                update={
                    "text": rewritten_text,
                    "tags": rewritten_tags,
                    "updated_at": updated_at,
                }
            )
            memories[index] = updated_entry
            break
        if updated_entry is None:
            return None
        self._write_memories(sender_id, display_name, memories)
        profile = self._load_profile(sender_id, display_name)
        profile.last_seen_timestamp = updated_at
        profile.memory_count = len(memories)
        profile.last_memory_write_at = max((item.updated_at for item in memories), default=None)
        write_json(self._user_profile_path(sender_id, display_name), profile.model_dump(mode="json"))
        return self._memory_card(updated_entry, sender_id=sender_id, sender_name=display_name)

    def forget_memory(self, sender_id: str, display_name: str, memory_id: str, *, timestamp: datetime | None = None) -> bool:
        memories = self._iter_memories(sender_id, display_name)
        remaining = [entry for entry in memories if entry.memory_id != memory_id]
        if len(remaining) == len(memories):
            return False
        self._write_memories(sender_id, display_name, remaining)
        profile = self._load_profile(sender_id, display_name)
        profile.last_seen_timestamp = timestamp or utc_now()
        profile.memory_count = len(remaining)
        profile.last_memory_write_at = max((item.updated_at for item in remaining), default=None)
        write_json(self._user_profile_path(sender_id, display_name), profile.model_dump(mode="json"))
        return True

    def write_bad_memory(
        self,
        sender_id: str,
        display_name: str,
        text: str,
        *,
        source_message_id: int | None = None,
        timestamp: datetime | None = None,
    ) -> MemoryEntry:
        cleaned = " ".join(text.split()).strip()
        if not cleaned:
            raise ValueError("Bad memory text must be non-empty.")
        for existing in reversed(self._iter_memories(sender_id, display_name)):
            if existing.text.lower() == cleaned.lower():
                return existing
        created_at = timestamp or utc_now()
        entry = MemoryEntry(
            memory_id=new_memory_id(),
            created_at=created_at,
            updated_at=created_at,
            source_message_ids=[] if source_message_id is None else [source_message_id],
            text=cleaned,
            tags=["negative_interaction", "disengage"],
            confidence=0.9,
            salience=0.92,
        )
        path = self._memory_path(sender_id, display_name)
        append_jsonl(path, [entry.model_dump(mode="json")])
        profile = self._load_profile(sender_id, display_name)
        profile.last_seen_timestamp = created_at
        profile.memory_count += 1
        profile.last_memory_write_at = created_at
        write_json(self._user_profile_path(sender_id, display_name), profile.model_dump(mode="json"))
        return entry

    def _iter_memories(self, sender_id: str, display_name: str) -> list[MemoryEntry]:
        path = self._memory_path(sender_id, display_name)
        if not path.exists():
            return []
        rows: list[MemoryEntry] = []
        with path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                rows.append(MemoryEntry.model_validate_json(line))
        return rows

    def retrieve(self, message: TelegramMessagePayload, limit: int) -> list[MemoryCardPayload]:
        memories = self._iter_memories(message.sender.id, message.sender.name)
        if not memories:
            return []
        query_terms = {token.lower() for token in re.findall(r"[a-zA-Z0-9']+", message.content)}
        scored: list[tuple[float, MemoryEntry]] = []
        for entry in memories:
            memory_terms = set(entry.tags) | {token.lower() for token in re.findall(r"[a-zA-Z0-9']+", entry.text)}
            lexical = len(query_terms & memory_terms)
            score = lexical + entry.salience + entry.confidence
            scored.append((score, entry))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            self._memory_card(entry, sender_id=message.sender.id, sender_name=message.sender.name)
            for score, entry in scored[:limit]
            if score > 0
        ]

    def expand(self, sender_id: str, display_name: str, memory_ids: list[str]) -> list[MemoryCardPayload]:
        memories = self._iter_memories(sender_id, display_name)
        expanded: list[MemoryCardPayload] = []
        wanted = set(memory_ids)
        for entry in memories:
            if entry.memory_id not in wanted:
                continue
            expanded.append(self._memory_card(entry, sender_id=sender_id, sender_name=display_name))
        return expanded

    def _memory_card(self, entry: MemoryEntry, *, sender_id: str, sender_name: str) -> MemoryCardPayload:
        return MemoryCardPayload(
            memory_id=entry.memory_id,
            owner_sender_id=sender_id,
            owner_sender_name=sender_name,
            text=entry.text,
            tags=entry.tags,
            salience=entry.salience,
            confidence=entry.confidence,
            created_at=entry.created_at,
            updated_at=entry.updated_at,
        )

    def persist_sticker_signal(self, signal: StickerSignalPayload, sender_id: str) -> None:
        if not signal.sticker_file_id:
            return
        path = self._stickers_dir / f"{signal.sticker_file_id}.json"
        payload = read_json(path, None)
        now = utc_now()
        if payload is None:
            record = StickerRecord(
                sticker_file_identifier=signal.sticker_file_id,
                sticker_set_identifier=signal.sticker_set_id,
                sender_identifiers=[sender_id],
                sampled_preceding_segment=signal.preceding_segment,
                inferred_tones=signal.inferred_tones,
                confidence=signal.confidence,
                topic_tags=[],
                first_seen_timestamp=now,
                last_seen_timestamp=now,
                usage_count=1,
            )
        else:
            record = StickerRecord.model_validate(payload)
            record.last_seen_timestamp = now
            record.usage_count += 1
            if sender_id not in record.sender_identifiers:
                record.sender_identifiers.append(sender_id)
            record.sampled_preceding_segment = signal.preceding_segment or record.sampled_preceding_segment
            record.inferred_tones = signal.inferred_tones or record.inferred_tones
            record.confidence = max(record.confidence, signal.confidence)
        write_json(path, record.model_dump(mode="json"))
