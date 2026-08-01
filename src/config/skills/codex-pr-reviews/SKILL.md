---
name: codex-pr-reviews
description: Amber-focused rules for inspecting, fixing, acknowledging, replying to, or resolving GitHub pull request review comments and unresolved threads.
---

# Codex PR Reviews

Use `codex-development` as well whenever addressing feedback requires repository edits.

## Review Comment Handling

- Inspect PR metadata, the diff, requested changes, and unresolved review threads before editing.
- Address every actionable comment at its exact file and line scope. A similar change elsewhere does not address a sibling comment.
- Map each comment to a local fix, an explicit thread reply, or a user-approved deferral.
- Acknowledge a comment with a thumbs-up when a local change addresses it.
- If no local change is appropriate, reply on that exact thread with the reason; do not rely on a reaction alone.
- Leave review threads unresolved so the reviewer can verify the fix. Resolve only when the user explicitly asks or the reviewer delegates resolution.
- Validate the behavior affected by the feedback and report the comment-by-comment outcome.

## Lesson Capture

Route reusable feedback to the narrowest owning skill before finishing:

- Save general development guidance in `codex-development`.
- Save language-specific implementation or style guidance in the matching language skill, such as `python-style-rules`.
- Keep only PR review mechanics in this skill.

Commit and push applicable skill changes with the implementation. Do not save incident wording, blame, private data, or transcript details.
