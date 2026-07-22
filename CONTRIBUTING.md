# Contributing

This file is for maintainers and contributors. The top-level README is intentionally reserved for installation and first-run usage.

## Local Setup

Use Python 3.14 when working on the repository:

```bash
python3.14 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

The development CLI entrypoint is:

```bash
python main.py --help
```

For installed-release behavior, use the packaged `amber` binary instead.

The local ModernBERT attention scorer is optional. Install it only when testing local ML scoring:

```bash
pip install -r requirements-ml.txt
AMBER_ATTENTION_SCORER=modernbert AMBER_ATTENTION_DEVICE=cpu python main.py run --workspace my-workspace
```

## Workspace Development

Create and configure a local workspace:

```bash
python main.py workspace init my-workspace
python main.py workspace configure my-workspace
python main.py workspace doctor my-workspace --external --service
```

Run from source:

```bash
python main.py run --workspace my-workspace
```

Workspace configuration is seeded from [src/config/config.default.toml](./src/config/config.default.toml). Workspace prompts and the workspace Codex skill are copied into `~/.amber/workspaces/<name>/` during `workspace init`.

## Tests

Run the unit suite:

```bash
source venv/bin/activate
pytest tests/unit -q
```

Integration tests are slower and may require live OpenAI, Telegram, Linear, GitHub, Codex, and Podman access. Prefer the smallest focused test that covers the change.

## Commits And Branches

Develop in a worktree on `feature/<slug>`, `fix/<slug>`, or `release/X.Y.Z`; do not work directly on `master`. All changes reach `master` through a pull request.

Use scoped Conventional Commits:

```text
fix(codex): prevent duplicate task notifications

Description: suppress the completion fallback after an explicit terminal notification.
Breaking: no
```

Use `!` plus a `BREAKING CHANGE:` footer when users must migrate an API, CLI, configuration, storage, packaging, or installation contract. The complete repository policy is in [AGENTS.md](./AGENTS.md).

Choose release versions from commits since the latest tag: `fix`/`perf` increments patch, `feat` increments minor, and a breaking change increments major. Non-functional commit types alone do not require a version bump. Record the version in `VERSION` without the tag's `v` prefix.

## Release Artifacts

Prepare releases on `release/X.Y.Z`. After its reviewed pull request is rebase-merged, create an annotated `vX.Y.Z` tag on the resulting `master` commit and build from that exact tag. Release branches are retained; published tags are immutable.

Every GitHub release must include both installer-selectable Linux packages and their SHA256 files:

- Standard package: `amber-linux-x86_64.tar.gz`
- Standard checksum: `amber-linux-x86_64.tar.gz.sha256`
- Full local-ML package: `amber-linux-x86_64-full.tar.gz`
- Full local-ML checksum: `amber-linux-x86_64-full.tar.gz.sha256`

Do not publish a release with only one package variant. The installer offers both the standard and full local-ML choices, so a missing full package breaks ModernBERT installs. If an archive exceeds GitHub's upload size limit and the build script splits it, upload the split files with the generated package-name prefix, such as `amber-linux-x86_64.tar.gz.part-*` or `amber-linux-x86_64-full.tar.gz.part-*`, plus the matching `.sha256` file.

Build the standard Linux release asset with:

```bash
source venv/bin/activate
scripts/build_release.sh
```

The standard build writes `dist/amber-linux-x86_64.tar.gz` and a SHA256 file. If the archive is larger than the configured split size, the script also creates `dist/amber-linux-x86_64.tar.gz.part-*`.

Default release builds exclude Torch, Transformers, CUDA/NVIDIA libraries, and related scientific packages. Build the separate full local-ML package with:

```bash
AMBER_BUILD_ML=1 scripts/build_release.sh
```

The full build writes `dist/amber-linux-x86_64-full.tar.gz`, `dist/amber-linux-x86_64-full.tar.gz.sha256`, and split files named `dist/amber-linux-x86_64-full.tar.gz.part-*` if the archive crosses the split threshold.

Before publishing, verify both checksums and confirm the packaged `VERSION` matches the branch and tag. Publish both variants and all required split parts together.

## Installer Overrides

The installer defaults to the latest `coalio/amber` GitHub release. Maintainers can override install behavior with environment variables:

- `AMBER_REPO` changes the GitHub repository.
- `AMBER_HOME` changes the install root.
- `AMBER_RELEASE_ARCHIVE` installs from a local archive.
- `AMBER_RELEASE_URL` installs from a specific archive URL.
- `AMBER_RELEASE_TAG` controls the release directory name for manual installs.
- `AMBER_ASSET_NAME` changes the expected release asset name.
- `AMBER_INSTALL_VARIANT=standard|full` selects the default or full release asset.
- `AMBER_FULL_ASSET_NAME` changes the expected full release asset name.

Example local archive install:

```bash
AMBER_RELEASE_ARCHIVE=dist/amber-linux-x86_64.tar.gz AMBER_RELEASE_TAG=local ./installer/install.sh my-workspace
```

## Documentation Rules

- Keep [README.md](./README.md) focused on user installation, first run, and troubleshooting.
- Put maintainer commands in this file.
- Put implementation guidance next to the subsystem it describes.
- Do not re-create a single monolithic architecture document.
- Historical planning notes belong under ignored `.dev/`, not in tracked root docs.
