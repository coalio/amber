# Events

Events are the cross-layer contracts for the Amber runtime. Layers communicate by emitting typed events instead of importing one another's internals.

## Core Pieces

- `base.py`: shared `BaseEvent` metadata.
- `bus.py`: in-process synchronous event dispatch.
- `observability.py`: compact logging context for important events.
- Domain files such as `receiver.py`, `attention.py`, `context.py`, `ai.py`, `outbound.py`, `action.py`, `codex.py`, and `linear.py`.

## Contract Rules

- Event names describe facts that happened, not commands to execute.
- Payloads should be Pydantic models at layer boundaries.
- Add fields in a way that keeps existing tests and consumers understandable.
- Keep transport-specific details out unless downstream behavior actually needs them.

## Common Changes

When adding a behavior that crosses layers:

1. Add or extend the event payload here.
2. Update emitters and subscribers explicitly.
3. Add tests around the boundary, not only the implementation detail.
