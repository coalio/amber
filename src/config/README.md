# Config

Config owns settings, workspace initialization, release resources, and prompt locations.

## Runtime Settings

`config.py` loads defaults from `config.default.toml`, merges a workspace `config.toml`, applies environment overrides, resolves workspace-relative paths, and returns a cached `Settings` object.

Important environment variables:

- `AMBER_HOME`: install and workspace root, defaulting to `~/.amber`.
- `AMBER_WORKSPACE`: default workspace when no explicit workspace is passed.
- `AMBER_*`: runtime-specific overrides.
- `API_ID` and `API_HASH`: Telegram credential overrides.
- `OPENAI_API_KEY`: fallback for the Amber AI API key.

## Workspaces

`workspace.py` creates workspace directories, copies editable prompts and Codex skills, writes initial config, renders systemd user units, and runs workspace doctor checks.

Workspace-owned files are intended to be user-editable. Release-level `system/` prompts are shipped with the release because they must track runtime behavior.

## Common Changes

- Add a config option to `config.default.toml`, `Settings`, env override mapping, and the relevant subsystem config class.
- Keep workspace defaults safe for first install.
- Do not make top-level README carry detailed config semantics; document subsystem-specific behavior next to the subsystem.
