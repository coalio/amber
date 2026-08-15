---
name: codex-pr-reviews
description: Rules for addressing GitHub pull request review comments. Use when inspecting, fixing, acknowledging, replying to, or resolving requested changes, unresolved review threads, or inline PR comments. PR-review lesson capture must be saved in codex-development.
---

# CodexPrReviews

## Scope

Use these rules when addressing PR review comments, requested changes, unresolved review threads, or inline reviewer feedback.

For code changes made while addressing PR review feedback, also use `codex-development` for development workflow, commit discipline, validation, post-change review, and lesson capture.

## Review Comment Handling

- Address every actionable review comment at the exact file and line scope where it was made.
- A broad refactor, a similar fix elsewhere, or fixing one instance of a repeated issue does not address sibling comments in other files or on other lines.
- If a comment is addressed with a local change, acknowledge that exact comment with a thumbs-up reaction.
- If there is no direct local change at that comment's file and line scope, leave an explicit GitHub reply explaining why that specific comment does not require a local change.
- Do not rely on reactions alone when there is no file-and-line-local fix.
- Leave review threads unresolved after addressing them so the reviewer can verify the fix.
- Do not mark a thread resolved unless the user explicitly asks or the reviewer has clearly delegated resolution.

## Workflow

1. Inspect the PR metadata, diff, requested changes, and unresolved review threads before editing.
2. Map each actionable comment to one of these outcomes: fixed at the exact scope, replied to at the exact thread, or deferred because the user explicitly approved deferral. If the comment does not become outdated (e.g., the latest commit doesn't modify the exact lines the code is addressing), then you must write an explicit comment explaining what was done.
3. Make scoped edits that address the reviewer feedback without expanding into unrelated cleanup.
4. Validate the behavior affected by the feedback.
5. Acknowledge or reply on each relevant GitHub comment or thread.
6. Report which comments were fixed, which received replies, which validation ran, and any residual risk.

## Lesson Capture

If there's any valuable lesson in the PR review feedback, update the Lessons section in `codex-development/SKILL.md`.

- Save the broad reusable lesson in `codex-development`, not in this skill.
- Keep this skill focused on PR review handling workflow and requirements.
