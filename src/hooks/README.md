# Hooks

Hooks installs a configured Codex hook package into the workspace's persistent Codex home.

`config.py` maps Amber settings into the hook subsystem. `installer.py` refreshes an Amber-managed repository checkout inside the Codex sandbox and invokes that repository's `install.sh`. First installation retains the upstream installer's conflict checks. Updates from the same Amber-managed repository use `--force`, with conflict backups owned by the upstream installer.

The default workspace config installs `coalio/codex-hooks` at a pinned revision and trusts the exact global hook hashes reported by Codex. Set `hooks.repository = "none"` to disable installed global hooks, or change `hooks.repository` and `hooks.revision` together to select another compatible Git repository. A compatible repository must provide an `install.sh` that accepts an optional `--force` and honors `CODEX_HOME`.

Hook source checkouts live under `/codex-home/.amber-hook-sources` inside the sandbox. Installed files live under `/codex-home/.codex`, which is backed by the workspace's persistent `codex/codex-home` directory.
