# Receiver

Receivers convert external activity into Amber events. They should normalize inputs, attach enough metadata for downstream layers, and avoid making semantic decisions.

## Implementations

- `telegram/`: receives Telegram messages and typing/activity updates through Telethon.
- `codex/`: receives Codex questions and notifications from the local Codex app-server.
- `linear/`: polls Linear for due work and emits queue events.

## Event Boundaries

Receivers emit events such as `TelegramMessageReceivedEvent`, `TelegramTypingUpdatedEvent`, `CodexQuestionReceivedEvent`, `CodexNotificationReceivedEvent`, and `LinearTaskListReceivedEvent`.

Receivers may perform ingress-adjacent side effects when latency matters, such as marking an active Telegram chat read before normal event processing catches up. They should not decide whether Amber replies or how a reply is written.

## Common Changes

- Add or adjust source-specific normalization near the concrete receiver.
- Add new external input types by defining an event contract in `src/events/` first.
- Keep long-running or blocking receiver work off the Telegram event loop.
- Prefer passing facts downstream through events instead of importing later pipeline layers.
