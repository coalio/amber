# Action

Action performs external side effects after upstream layers have decided what should happen.

## Responsibilities

- Mark Telegram messages read.
- Send prepared Telegram message chunks.
- Preserve reply-target intent when sending.
- Emit delivery and chunk events.
- Manage presence, sleep, wake scheduling, pacing delays, and delivery-time interruption checks.
- Persist durable action-owned state through `GlobalStateStore`.

## Event Boundaries

Inputs include:

- `MessageReadEvent`
- `OutboundMessagePreparedEvent`
- `SemanticDecisionMadeEvent`
- `PresenceStateChangedEvent`

Outputs include:

- `OutboundMessageSentEvent`
- `OutboundChunkSentEvent`
- `SleepStateChangedEvent`
- `PresenceStateChangedEvent`

Action should not call the semantic model or re-score attention.

## Common Changes

- Telegram delivery code is in `telegram/layer.py`.
- Transport-specific send/read behavior is in `telegram/transport.py`.
- Pacing and retry settings are loaded through `ActionConfig`.
- Keep side effects idempotent where possible because Action may observe retries or repeated events.
