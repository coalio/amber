# Amber 0.5.3

## Fixed

- Preserve complete credential values when authorized workspace owners provide them for delegated Codex tasks and clarification replies.
- Keep secret non-disclosure rules scoped to user-facing replies, logs, and durable records so they do not redact internal execution input.

## Changed

- Document branch-based development in the main repository checkout without requiring additional worktrees.

## Validation

- The 246-test unit suite includes release-policy precedence, exact clarification forwarding, and secret-free logging coverage.
- Focused regressions verify that stale workspace guidance cannot reintroduce credential handoff redaction.
