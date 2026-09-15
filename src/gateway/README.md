# Operator Gateway

This package owns only the local operator protocol and its observation interface:

- `cli.py` sends requests and follows timestamped captures.
- `server.py` owns the private Unix socket, exclusive runtime lease, and request/response framing.
- `observer.py` translates pipeline and task-completion events into capture records without influencing decisions.

## Boundaries

`receiver/gateway.py` authorizes administrators, validates bursts, normalizes messages and typing, and submits them through the public `NormalizedChatIngress` interface. Reply lookup is injected; it never reads Telegram receiver internals.

`action/gateway.py` owns local message/file capture and route resolution. `action/delivery.py` applies trusted origin to file destinations. Unknown or revoked origins fail closed instead of falling back to Telegram recipients.

`state/gateway.py` owns private JSONL persistence only. Session storage and CLI captures retain the same format across upgrades. `runtime.py` assembles all of these components with the task-dispatch workflow.

Model calls and worker tasks are real. Only reply delivery is captured locally. Maintainer examples and flags are documented in `CONTRIBUTING.md`.
