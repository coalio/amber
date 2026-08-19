# Amber 0.5.0

## Added

- Inspect matching repository and local-environment continuity records before starting Codex work, using Linear IDs and similar task slugs to reconnect related work.
- Persist conversational task, thread, and turn provenance so operational follow-ups can continue the correct live Codex task.

## Fixed

- Route replies to open Codex clarifications through the waiting task with a single idempotent state transition, preventing a second task and duplicate external actions such as AWS SSO device codes.
- Accept credentials and other sensitive input when the user explicitly authorizes its task-scoped use, while preventing secret amplification in prompts and task-start logs.
- Avoid an idle-expiry logging race when a zero-delay callback expires a context session immediately.
- Keep continuity records instance-local and require durable records for repository and non-repository work.

## Changed

- Strengthen Codex collaboration guidance for private workspace boundaries, concise progress updates, task-scoped GitHub autonomy, and repository workflow checks.
- Enforce verified Codex delegation for action requests while supporting interactive operational input.

## Validation

- The 215-test unit suite covers clarification routing, idempotent retries, task provenance, credential handling, context expiry, and continuity behavior.
