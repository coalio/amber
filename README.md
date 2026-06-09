# Amber

Amber is a personal Telegram agent that can live in your chats, decide when a message is worth attention, and reply with your configured style. In work mode, it can also coordinate Codex and Linear work from the same Telegram surface.

The normal path is:

1. Install Amber.
2. Create a named workspace.
3. Configure Telegram, OpenAI, Linear, Codex, and GitHub auth.
4. Run Amber manually or as a `systemd --user` service.

## Prerequisites

Amber currently expects a Linux user environment with:

- `curl` and `tar`.
- A Telegram API ID and API hash from `my.telegram.org`.
- An OpenAI API key.
- A Linear API key.
- Rootless Podman for the Codex sandbox used by work mode, with cgroup v2 and `slirp4netns` available.
- GitHub auth for the Codex sandbox if Amber should do repository work.

The installer checks local commands, rootless Podman, cgroup v2, `slirp4netns`, and required `podman run` flags before it downloads the Amber release. It can offer an interactive package install for missing system packages, but it will not modify system packages in non-interactive runs. It cannot create external accounts for you.

## Install

Run the installer with the workspace name you want to create:

```bash
curl -fsSL https://raw.githubusercontent.com/coalio/amber/master/installer/install.sh | bash -s -- my-workspace
```

This checks host prerequisites, downloads the latest GitHub release, installs Amber under `~/.amber`, creates `~/.amber/workspaces/my-workspace`, runs interactive authentication, and asks whether to install the optional user service.

To force a fresh package download instead of reusing `~/.amber/packages` or recovered `/tmp` downloads:

```bash
curl -fsSL https://raw.githubusercontent.com/coalio/amber/master/installer/install.sh | AMBER_INSTALL_NO_CACHE=1 bash -s -- my-workspace
```

After install, the Amber binary is here:

```bash
~/.amber/bin/amber
```

Add it to your shell path if you do not want to type the full path:

```bash
export PATH="$HOME/.amber/bin:$PATH"
```

## First Run

Check the workspace before starting the agent:

```bash
~/.amber/bin/amber workspace doctor my-workspace --external --service
```

Run Amber in the foreground:

```bash
~/.amber/bin/amber run --workspace my-workspace
```

You can also use the shorter form:

```bash
~/.amber/bin/amber --workspace my-workspace
```

## User Service

If you skipped service setup during install, add it later:

```bash
~/.amber/bin/amber service install --workspace my-workspace --enable --now
```

Manage it with:

```bash
~/.amber/bin/amber service status --workspace my-workspace
~/.amber/bin/amber service stop --workspace my-workspace
~/.amber/bin/amber service start --workspace my-workspace
~/.amber/bin/amber service uninstall --workspace my-workspace
```

If you want the service to start after reboot without an interactive login, enable user lingering:

```bash
loginctl enable-linger "$USER"
```

## Workspace Files

Amber installs the application once and keeps your data in workspaces:

```text
~/.amber/
  bin/amber
  releases/
  workspaces/
    my-workspace/
      config.toml
      prompts/
      codex-skills/
      telegram/
      memories/
      runtime-state/
      logs/
      codex/
```

The files you are most likely to edit are:

- `config.toml` for model, Telegram, Linear, timing, and runtime settings.
- `prompts/*.md` for workspace-specific voice and behavior.
- `codex-skills/CodexRules/SKILL.md` for the Codex sandbox rules used by this workspace.

Run `workspace doctor` after changing config or auth:

```bash
~/.amber/bin/amber workspace doctor my-workspace --external
```

## Troubleshooting

- If `amber` is not found, use `~/.amber/bin/amber` or add `~/.amber/bin` to `PATH`.
- If Telegram setup fails, rerun `~/.amber/bin/amber workspace configure my-workspace`.
- If Codex setup fails before credential prompts, run `~/.amber/bin/amber workspace doctor my-workspace --external` and fix the failed Podman check. Amber can automatically retry the sandbox with `codex.enforce_resource_limits = false` when cgroup-backed resource limits are the blocker.
- If Codex or GitHub auth fails, rerun workspace configuration from a real terminal so the sandbox can prompt interactively.
- If the installer reused an old package, rerun it with `AMBER_INSTALL_NO_CACHE=1`.
- If the service does not start, check `~/.amber/bin/amber service status --workspace my-workspace` first, then inspect the workspace `logs/` directory.
- If a release asset cannot be found, the installer did not find a compatible latest GitHub release for this platform.

## Developer Docs

Maintainer setup, tests, release building, and documentation rules live in [CONTRIBUTING.md](./CONTRIBUTING.md).

The source tree has local README files for the runtime and each major subsystem. Start with [src/README.md](./src/README.md).
