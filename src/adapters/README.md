# Adapters

Adapters wrap external systems behind local Python interfaces. They are used by receivers, tools, and runtime setup.

## Current Adapters

- `codex/`: starts and talks to the Podman-backed Codex app-server, manages task lifecycle notifications, and exposes Codex task operations.
- `linear/`: reads and mutates Linear issues through the GraphQL API.
- `registry.py`: stores adapters by name for tool execution.

## Boundary Rules

- Adapters should hide transport/API details from tools and pipeline layers.
- Adapter return types should be simple runtime objects, not raw external payloads.
- Long-running setup, such as Codex sandbox startup, belongs here or in runtime composition, not inside model-facing tools.

## Common Changes

- Add a new external capability by creating an adapter and registering it in `build_application()`.
- Keep auth and credential paths workspace-scoped.
- Add tests with fake clients before relying on live external services.
