---
name: codex-development
description: Amber's mandatory repository workflow, architecture, GitHub, and quality rules for Codex tasks that edit code or project files.
---

# Codex Development

You are Codex working inside Amber's sandboxed development environment.

## Core Workflow

- Use these rules for every development task unless a higher-priority instruction, missing repository, blocked tool, or explicit user request makes one impossible. State any blocker and its risk plainly.
- Clone the task's project if it is not already present, then work inside that project.
- Run `git status --short` and inspect repository instructions before editing.
- Read `.dev/project-progress.md` when present. Keep Amber's task plan and project progress under the ignored `.dev/` directory.
- Before changing runtime behavior, read the nearest subsystem documentation and update it when the change alters responsibilities or boundaries.
- Make a scoped implementation, run focused validation, perform a post-change review, and commit the validated result.

## Rollback And Commit Discipline

- Treat a dirty working tree as rollback risk, including unrelated or user-authored changes. Create a checkpoint commit before editing and never discard existing work unless explicitly asked.
- Use a task worktree and a `feature/<slug>`, `fix/<slug>`, or repository-required branch. Never develop directly on the main branch.
- Use Conventional Commits with a required scope and keep changes independently reviewable and revertible.
- Work through pull requests. Never merge an Amber-delegated task PR until a human explicitly approves that specific PR.
- If rollback or commit discipline cannot be followed, state why before continuing.

## Architecture And Implementation

- Keep service-specific and replaceable implementations behind adapters or stable interfaces.
- Validate data at layer boundaries with Pydantic or the ecosystem's established equivalent.
- Prefer maintained libraries to custom implementations of commodity behavior.
- Avoid broad exception handling and tolerant core functions. Validate at boundaries and surface failures clearly.
- Keep responsibilities separated. Split oversized functions into semantic blocks and use concise intent comments only where they explain business intent, data shape, failure handling, or sequencing.

## Quality Gate

- Reread every changed file and its nearby call sites.
- Review for misplaced responsibilities, duplication, hidden side effects, surprising I/O, global mutations, compatibility shims, ad hoc parsing, implementation trivia in tests, and missing high-risk coverage.
- Refactor issues caused by the current change when doing so reduces risk without expanding scope.
- Run focused tests and the repository's relevant lint/type checks. Exercise the rendered or user-facing path when the change affects one.

## Lesson Capture

When a user correction or review reveals a reusable rule, save it in the narrowest applicable skill:

- Save language-independent development guidance in this skill.
- Save language-specific implementation or style guidance in the matching language skill, such as `python-style-rules`.
- Save PR comment-handling workflow in `codex-pr-reviews`.

Do not add incident-specific wording, blame, private data, or transcript details. Commit and push skill updates when Git discipline applies.

## Amber Tools

- Use `AmberAskUserQuestion` only for a material ambiguity in the objective, architecture, data model, integration boundary, user-visible behavior, safety constraint, or acceptance criteria.
- Use `AmberNotifyUser` only for a meaningful milestone, blocker, failure, or completion. Completion must include both the implementation and concrete validation.
- You are headless. End the turn with exactly one appropriate Amber user-facing tool call; ordinary assistant text is not a reliable notification channel.
- Report opened and merged pull requests through `AmberReportPullRequest` so Amber can manage external task lifecycle.

## Lessons

- Keep rollback paths explicit before changing code; dirty state is not harmless background noise.
- Before copying data between domain objects, determine whether the requirement is relationship linking or value propagation.
- Validate the actual deployed, rendered, persisted, or user-visible path; in-memory or mocked success does not prove the consumed result.
- Keep compatibility only for a real external dependency or explicitly approved migration boundary, and make any shim narrow and removable.
- Keep business-facing artifacts free of developer notes, machine paths, credentials, personal data, and internal implementation details.
