# Attention

Attention decides whether an inbound Telegram message should continue into conversation context or be discarded.

## Responsibilities

- Score Telegram messages using the configured attention scorer.
- Apply sleep, active-chat, engaged-user, ignore-window, direct-mention, and already-seen policy.
- Retrieve compact memory cards that may help downstream reasoning.
- Emit read events when Attention owns the visible-read or durable-seen decision for a message.

## Event Boundaries

Input:

- `TelegramMessageReceivedEvent`

Output:

- `AttentionDecisionMadeEvent`
- `MessageReadEvent` where attention/read policy needs a read side effect

Attention should not write replies, build full model frames, or call the conversational model.

## Common Changes

- Tune thresholds in `src/config/config.default.toml` and `AttentionConfig`.
- Change scoring behavior in `scoring/`. The default runtime uses heuristic-only scoring. Source installs load explicitly installed ML dependencies in-process; packaged Full installs use the installer-managed optional environment and worker.
- Change memory retrieval in `memory/store.py`.
- Keep policy reasons explicit in emitted attention decisions so failures are inspectable from logs.
