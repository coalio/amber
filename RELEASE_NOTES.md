# Amber 0.7.0

## Added

- `amber gateway send --workspace <workspace> --as=<admin-id> --message "..."` exercises the running chat pipeline with real model calls and workers, while capturing replies locally.
- Repeat `--message` to test bursts, simulate typing with `--typing-seconds`, continue with `--session`, and inspect durable timestamped captures with `amber gateway events`.

## Fixed

- Deliver the work receipt before starting the worker. Follow-up messages cannot cancel it, and later model failures cannot conceal a successful task start.
- Retry token-limited structured responses before parsing or executing their tool calls. The default output budget is now 4096 tokens.
- Preserve messages waiting for debounce when the idle timeout expires, honor longer configured debounce windows, and retain typing activity received before session creation.
- Preserve the originating task context on worker notifications and questions, including local gateway routing. Older worker servers are refreshed automatically.

## Upgrade Notes

- Existing workspace settings and customized prompts remain preserved. A zero `context.debounce_seconds` disables batching; use 5 seconds for the standard quiet window.
- Gateway commands require a running workspace and an allowlisted administrator. Replies stay local; requested tasks and model calls execute normally.
