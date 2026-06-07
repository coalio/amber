# State

State contains durable runtime data that must survive process restarts.

## Core Pieces

- `models.py`: Pydantic state models for conversation, sleep, delivery, open questions, Linear queue items, and related runtime facts.
- `store.py`: `GlobalStateStore`, the filesystem-backed state owner.

## Ownership

The store persists a single JSON state document at the workspace `runtime-state` path. Layers may read snapshots, but mutation should remain intentional and go through store methods.

State is used for:

- active and pending chat/session tracking
- sleep and presence status
- seen/read watermarks
- open Codex questions and replies
- Linear task queue lifecycle
- pending interruptions and delivery metadata

## Common Changes

- Add model fields with defaults so old workspace state can still load.
- Prefer store methods over direct mutation of snapshots.
- Add tests for restart-safe behavior when a field affects runtime decisions.
