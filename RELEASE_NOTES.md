# Amber 0.5.1

## Fixed

- Keep Codex clarification turns token-idle and durable for as long as a person needs to reply, without the previous clarification metadata or six-hour runner deadlines.
- Recover a clarification after an app-server, worker, or host restart by continuing its recorded Codex thread instead of deadlocking behind the duplicate-task state-machine guard.
- Make clarification delivery idempotent across dropped and replayed HTTP responses, detect conflicting replays, and reset event cursors when only the app-server process restarts.
- Report the concrete verified work blocker after retries are exhausted instead of claiming Amber only needs more time.
- Persist runtime state atomically with private permissions and redact credential-shaped content from logs.

## Validation

- The 242-test unit suite covers multi-day waits, malformed input, stale identifiers, missing threads, failed recovery, dead worker pipes, process and event-log restarts, interrupted state writes, dropped and truncated responses, idempotent and conflicting replays, truthful fallback replies, private state permissions, and log redaction.
- Three fixture-driven work-mode integration tests cover task delegation and Codex event delivery.
