---
name: python-style-rules
description: Python implementation and review guidance for Amber's Codex worker. Use whenever a task reads, changes, or reviews Python code.
---

# Python Style Rules

## Imports And Optional Dependencies

- Put imports at module scope. Do not hide required or optional dependency imports inside arbitrary functions, methods, constructors, or exception handlers.
- Import required pinned runtime dependencies directly so missing installations fail clearly.
- When only an optional feature needs a heavy dependency, isolate that feature in its own module or adapter. Keep dependency-free policy and interfaces in a separate module so standard code paths never import the optional implementation.
- Use an explicit feature boundary for degraded behavior instead of a function-local import guard.

## Boundaries And Tests

- Keep framework-independent policy separate from framework-backed inference, transport, storage, or presentation implementations.
- Test the dependency-free policy with a small fake that implements the stable interface, and test the real optional module with an import or execution smoke check in an environment that installs its dependencies.

## Lesson Capture

Add broad reusable Python guidance learned from implementation or PR feedback here. Keep language-independent workflow in `codex-development` and PR comment mechanics in `codex-pr-reviews`.
