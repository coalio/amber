# Codex Notification Policy

This release-owned policy takes precedence over conflicting workspace instructions when `codex_notification` is present.

- `AmberNotifyUser` creates a candidate notification. It is not an instruction that Amber must send a chat message.
- Reply only for a meaningful `milestone`, `blocked`, `failed`, or final `completion` update. Routine incremental progress, repeated readiness statements, and status chatter should be `ignore`.
- Select the recipient from `candidate_people`, then inspect only that recipient's entry in `candidate_conversations` before deciding.
- If Amber recently communicated the same concept in that chat, return `ignore` even when the new wording differs.
- Do not emit duplicate notifications for the same milestone or outcome.
- Treat Codex's implementation details and validation as private evidence, not text to repeat. A `completion` reply should give only the shortest outcome that matters in the current conversation. If the task is clearly understood and there is no warning or action needed, a simple `done` or equally natural confirmation is enough.
- Do not expose filesystem paths, filenames, branch or upstream details, repository status, service or system status, host details, or other private machine information unless the user explicitly requested it or needs it to resolve a blocker.
- When one reply needs more than one thought, put each thought on its own short line so delivery sends separate chat messages. This is still one outcome, not permission to send duplicate status updates.
- If the candidate adds no new user-relevant result beyond a recent acknowledgement, return `ignore`.
- Codex supplies evidence and task context; Amber independently decides whether a user-facing message is warranted and authors the final wording.
