# Amber Repository Rules

These rules apply to the entire repository.

## Branches And Pull Requests

- Never develop directly on `master`. Use a worktree with a `feature/<slug>`, `fix/<slug>`, or `release/X.Y.Z` branch.
- Every change reaches `master` through a pull request with required checks passing.
- Preserve release branches after merge so the preparation history remains auditable.
- Rebase-merge release pull requests so individual Conventional Commits remain in `master` without an extra merge commit.
- Amber-delegated project task pull requests require explicit human approval before Amber or its Codex worker merges them.
- A maintainer or maintenance agent may merge and publish an Amber release only when the maintainer explicitly authorizes that release work.

## Commits

- Use Conventional Commits in the form `type(scope): summary`; scope is required.
- Every commit body must include `Description:` with the concrete change and `Breaking:` with `no` or the migration impact.
- Use `fix` for bug fixes, `feat` for backward-compatible features, and `perf` for performance fixes. Use `docs`, `test`, `refactor`, `build`, `ci`, and `chore` only when those types accurately describe the change.
- Signal breaking changes with `type(scope)!:` and a `BREAKING CHANGE:` footer. Do not label a change breaking unless users must change an API, CLI, configuration, storage, packaging, or installation workflow.
- Keep commits independently reviewable and revertible; do not mix implementation, generated artifacts, and unrelated cleanup.

## Semantic Versions

- Derive the next version from all commits since the latest `vX.Y.Z` tag. The highest-impact change wins.
- `fix` and `perf` increment the patch version. `feat` increments the minor version. Any breaking change increments the major version.
- Documentation, tests, refactors, build work, CI, and chores alone do not require a release bump.
- Store the release without a `v` prefix in `VERSION`. The release branch must be `release/X.Y.Z`; the annotated tag must be `vX.Y.Z`.
- Never move or replace a published release tag. Correct a released defect with a new version.

## Release Gate

- Prepare the version and release notes on `release/X.Y.Z`, validate the full unit suite, and build both package variants from the exact release commit.
- Merge the reviewed release pull request before creating the tag. Tag the resulting `master` commit with an annotated `vX.Y.Z` tag.
- Every GitHub release must include the standard and full local-ML archives, each checksum, and any generated split parts.
- Verify archive checksums, packaged `VERSION`, installer behavior, workspace doctor, service health, and the release-specific regression before announcing completion.
- Do not include credentials, personal identifiers, machine-specific paths, local state, or ignored development artifacts in commits or releases.
