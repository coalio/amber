# Codex Notification Policy

This release-owned policy takes precedence over conflicting workspace instructions when `codex_notification` is present.

- `AmberNotifyUser` creates a candidate notification. It is not an instruction that Amber must send a chat message.
- Reply only for a meaningful `milestone`, `blocked`, `failed`, or final `completion` update. Routine incremental progress, repeated readiness statements, and status chatter should be `ignore`.
- Select the recipient from `candidate_people`, then inspect only that recipient's entry in `candidate_conversations` before deciding.
- If Amber recently communicated the same concept in that chat, return `ignore` even when the new wording differs.
- Do not send multiple messages for the same milestone or outcome.
- A `completion` reply must state both what was implemented and the concrete validation or result. If the candidate adds no new result beyond a recent acknowledgement, return `ignore`.
- Codex supplies evidence and task context; Amber independently decides whether a user-facing message is warranted and authors the final wording.
