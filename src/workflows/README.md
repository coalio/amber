# Workflows

Application workflows coordinate operations that must cross subsystem boundaries in a defined order. They are assembled in `runtime.py`; tools delegate to them instead of owning delivery policy.

## Task Dispatch

`TaskDispatchWorkflow` validates the request, resolves a continuation and its trusted origin, synchronously requests a `WorkReceipt` from Action, and only then starts or resumes Codex. It records task provenance, binds the delivered receipt as a reply anchor, and updates Linear task state when applicable.

`ToolInvocation` contains runtime facts, not model-selected recipients. `TaskOrigin` is an immutable opaque delivery route defined in `events/delivery.py`. Origin is sent in the task envelope, outside the worker's editable context; Codex infrastructure propagates it without knowing which ingress or delivery implementation owns it.

The workflow returns authoritative dispatch and acknowledgement results. The semantic client may normalize its decision from those results, but must not deliver acknowledgements or write delivery state. Failed receipt delivery prevents dispatch; retries reuse an already-delivered receipt.
