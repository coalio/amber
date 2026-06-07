# Source Map

`src/` contains the Amber runtime. The CLI enters through [../main.py](../main.py), dispatches commands in [cli.py](./cli.py), and builds the application in [runtime.py](./runtime.py).

## Runtime Shape

Amber is assembled as one process with an in-memory event bus:

```text
receiver -> attention -> context -> ai -> outbound -> action
```

`build_application()` wires the stores, adapters, receivers, semantic model client, pipeline layers, and Telegram transport. `AmberBlueApplication.run_telegram_forever()` registers external receivers, starts Telegram, replays any open-question backlog, syncs presence, and then runs until Telegram disconnects.

## Subsystems

- [receiver](./receiver/README.md): converts external inputs into events.
- [attention](./attention/README.md): decides whether Telegram messages should surface.
- [context](./context/README.md): builds compact conversation frames.
- [ai](./ai/README.md): calls the semantic model and validates decisions.
- [outbound](./outbound/README.md): prepares reply text for delivery.
- [action](./action/README.md): performs Telegram side effects.
- [events](./events/README.md): defines cross-layer contracts and dispatch.
- [config](./config/README.md): loads settings, prompts, resources, and workspaces.
- [state](./state/README.md): persists durable runtime state.
- [adapters](./adapters/README.md): wraps external systems used by tools and receivers.
- [tools](./tools/README.md): exposes work-mode tool calls to the semantic model.
- [providers](./providers/README.md): wraps model-provider APIs.
- [utils](./utils/README.md): shared runtime utilities.

## Investigation Path

For a user-visible Telegram reply, start with [receiver/telegram](./receiver/telegram), follow the event types in [events](./events), then inspect the matching layer README. For startup, auth, or workspace problems, start with [config](./config/README.md), [cli.py](./cli.py), and [runtime.py](./runtime.py).
