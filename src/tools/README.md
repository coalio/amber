# Tools

Tools are model-callable operations exposed to Amber's semantic layer in work mode.

## Model Exposure

`ToolRegistry` always exposes `GetTool` first. Other tools must be enabled by calling `GetTool` so the model reads the exact schema before use.

Current tools:

- `GetTool`: inspect and enable another tool.
- `GetMemory`: read workspace memory.
- `ManageMemory`: create or update memory.
- `CodexRunTask`: start or resume Codex work.
- `CodexSendReply`: answer a Codex clarification.
- `SendFile`: send a file from the Codex workspace back to Telegram.

## Runtime Dependencies

Tools receive a `ToolRuntime` containing the memory store, adapter registry, global state store, Telegram transport, and Codex workspace path as needed.

## Common Changes

- Add a new tool by implementing `BaseTool`, adding tests, and registering it in `default_tool_registry()`.
- Keep tool schemas explicit and small.
- Return structured errors instead of raising when the model can recover.
