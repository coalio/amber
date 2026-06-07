# Utils

Utilities are shared support code used across multiple subsystems. Keep this folder narrow; layer-specific helpers should stay inside the owning layer.

## Current Utilities

- `files.py`: file helpers.
- `ids.py`: event and correlation IDs.
- `logging.py`: JSON logging setup and entrypoint logging.
- `message_archive.py`: process-local recent message archive.
- `metrics.py`: lightweight metrics registry.
- `openai.py`: OpenAI response helpers.
- `scheduler.py`: process-local timer scheduler.
- `sleep.py`: sleep-window calculations.
- `time.py`: UTC and local time helpers.

## Common Changes

- Put helpers here only when at least two subsystems need them.
- Avoid hidden side effects in utility functions.
- Prefer explicit dependencies over utilities that read global runtime state.
