# Amber 0.7.1

## Fixed

- Isolate acknowledgement-before-worker ordering in an application workflow. The semantic model receives the verified result without controlling receipt delivery or delivery-state updates.
- Carry immutable task origin separately from model-authored context through worker questions, notifications, and completion events. Unknown or revoked delivery routes cannot fall back to real Telegram recipients.
- Keep file replies bound to the task's trusted delivery route, even when model arguments request a different recipient.
- Skip local gateway conversations during Telegram backlog replay after restart.

## Internal Boundaries

- Gateway ingress now belongs to Receiver, local transport and routing to Action, and capture persistence to State. The gateway package owns only its operator protocol, CLI, and event observation.
- Gateway ingress uses public message and typing boundaries with injected reply lookup. Source-map and subsystem documentation describe the ownership contracts.

## Upgrade Notes

- Includes the 0.7.0 gateway command, typing-aware batching, acknowledgement ordering, and bounded retries for token-limited model responses.
- Existing workspace settings, customized prompts, CLI flags, and durable gateway captures remain compatible.
- Worker protocol 4 carries trusted origin in the task envelope; Amber automatically refreshes older worker servers.
