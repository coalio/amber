# Context

Context turns surfaced events into compact frames that the AI layer can reason over.

## Responsibilities

- Maintain the current conversation session.
- Debounce surfaced messages into a single frame when appropriate.
- Build recent-message and conversation-window payloads.
- Add memory cards, open Codex questions, Codex notifications, Linear queue frames, typing state, visible-read metadata, and pending interruption context.
- Track conversation lifecycle after semantic decisions and delivery events.

## Event Boundaries

Inputs include:

- `AttentionDecisionMadeEvent`
- `CodexQuestionReceivedEvent`
- `CodexNotificationReceivedEvent`
- `LinearTaskListReceivedEvent`
- `MessageReadEvent`
- `SemanticDecisionMadeEvent`
- `OutboundMessageSentEvent`
- `TelegramTypingUpdatedEvent`

Output:

- `ContextFrameReadyEvent`

Context should not call the model directly and should not send Telegram messages.

## Common Changes

- Session state lives in `session/store.py`.
- Timing and debounce policy is configured through `ContextConfig`.
- Frame shape is defined in `src/events/context.py`.
- If a new downstream model input is needed, add it to the context event payload rather than reaching into another layer.
