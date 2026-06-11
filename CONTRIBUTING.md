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

## Release Artifacts

Build the Linux release asset with:

```bash
source venv/bin/activate
scripts/build_release.sh
```

The build writes `dist/amber-linux-x86_64.tar.gz` and a SHA256 file. If the archive is larger than the configured split size, the script also creates `dist/amber-linux-x86_64.tar.gz.part-*`.

Default release builds exclude Torch, Transformers, CUDA/NVIDIA libraries, and related scientific packages. Build a separate heavy local-ML package with:

```bash
AMBER_BUILD_ML=1 scripts/build_release.sh
```

## Installer Overrides

The installer defaults to the latest `coalio/amber` GitHub release. Maintainers can override install behavior with environment variables:

- `AMBER_REPO` changes the GitHub repository.
- `AMBER_HOME` changes the install root.
- `AMBER_RELEASE_ARCHIVE` installs from a local archive.
- `AMBER_RELEASE_URL` installs from a specific archive URL.
- `AMBER_RELEASE_TAG` controls the release directory name for manual installs.
- `AMBER_ASSET_NAME` changes the expected release asset name.

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
