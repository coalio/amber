# AI

The AI layer converts a context frame into a semantic decision. It is responsible for model-facing reasoning, not transport behavior.

## Responsibilities

- Build model calls through `SemanticModelClient`.
- Ask the model for a structured semantic decision.
- Run the local harness that rejects empty, oversized, mirrored, or unsafe drafts.
- Retry with harness feedback when configured.
- Normalize reply targets, memory operations, Codex routing, and Linear-aware decisions before emitting the result.

## Event Boundaries

Input:

- `ContextFrameReadyEvent`

Output:

- `SemanticDecisionMadeEvent`

The semantic decision may say to ignore, reply, sleep, update memory, start Codex work, or answer an interruption. It should not perform those side effects itself.

## Common Changes

- Structured schemas live in `semantic/schema.py`.
- Model session behavior lives in `semantic/client.py`.
- Provider calls are routed through `src/providers/`.
- Prompt resources are loaded from `src/config/` and copied into workspaces where they are intended to be editable.
