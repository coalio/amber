# Outbound

Outbound preparation turns semantic draft text into delivery-ready Telegram message chunks.

## Responsibilities

- Convert non-reply decisions into `no_send` outbound events.
- Preserve Amber's semantic draft text while preparing Telegram delivery chunks.
- Split long output into chunks while preserving code blocks.
- Preserve reply target and visible-read metadata from the semantic decision.

## Event Boundaries

Input:

- `SemanticDecisionMadeEvent`

Output:

- `OutboundMessagePreparedEvent`

Outbound should not decide whether a message deserves attention and should not perform Telegram transport side effects.

## Common Changes

- Chunk limits live in `OutboundPreparationConfig`.
- Text cleanup and splitting live in `layer.py`.
- Delivery semantics belong in `src/action/`, not here.
