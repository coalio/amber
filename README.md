# Amber

Amber is a personal Telegram agent that can live in your chats, decide when a message is worth attention, and reply with your configured style. In work mode, it can also coordinate Codex and Linear work from the same Telegram surface.

The normal path is:

1. Install Amber.
2. Create a named workspace.
3. Configure Telegram, OpenAI, Linear, Codex, and GitHub auth.
4. Run Amber manually or as a `systemd --user` service.

## Prerequisites

The published Amber package and installer require:

- Linux on x86-64 with GNU libc 2.35 or newer and the standard zlib runtime (`libz.so.1`). Amber bundles its Python and OpenSSL runtime while keeping external host commands on the host library path.
- Bash 4 or newer plus `curl`, `tar`, GNU core utilities, GNU `find`, `grep`, `sed`, and `awk`.
- Rootless Podman with cgroup v2, `slirp4netns`, and support for the `--userns`, `--network`, `--cgroups`, `--memory`, `--cpus`, and `--pids-limit` run options. The resource-limit options can be disabled for a workspace during interactive installation.
- A Telegram API ID and API hash from `my.telegram.org`, an OpenAI API key, and a Linear API key.
- GitHub auth inside the Codex sandbox if Amber should do repository work.

The optional `systemd --user` service additionally requires `systemctl`, a reachable systemd user manager, and `loginctl` to enable lingering for startup without an interactive login. Installing the service later with `amber service install --enable --now` expects the user manager to already be available.

The installer checks the platform, GNU libc version, local commands, rootless Podman, cgroup v2, `slirp4netns`, and required Podman flags before downloading Amber. It also runs the downloaded package before changing the active-release symlink, which catches missing native runtime libraries such as zlib. Interactive runs can offer to install missing system packages; non-interactive runs never modify system packages. The installer cannot create external accounts for you.

Amber uses lightweight heuristic attention scoring by default. The Full installer choice additionally needs Python 3.10 through 3.14 with `venv` and `pip`, then downloads its optional CPU runtime from PyTorch and PyPI and its pinned model from Hugging Face. Both choices use the same small Amber release archive; Amber does not mirror those third-party dependencies in GitHub release assets.

## Install

Run the installer with the workspace name you want to create:

```bash
curl -fsSL https://raw.githubusercontent.com/coalio/amber/master/installer/install.sh | bash -s -- my-workspace
```

This checks host prerequisites, lets you choose Standard heuristic scoring or Full ModernBERT scoring, asks before reusing cached or recovered packages, downloads the latest GitHub release when needed, installs Amber under `~/.amber`, creates `~/.amber/workspaces/my-workspace`, asks how to handle Codex sandbox cgroup/resource-limit probes, uses Amber doctor to detect and offer to recreate an unhealthy existing sandbox container, runs interactive authentication if you choose to configure the workspace immediately, and asks whether to install the optional user service.

Standard stays self-contained and does not install any ML dependencies. Full additionally creates `~/.amber/ml-runtime`, installs CPU-only PyTorch and Transformers from their maintainers, caches the pinned ModernBERT checkpoint under `~/.amber/models`, and enables `attention.scorer = "modernbert"` for the new workspace.

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

Doctor checks run as ordered stages. To diagnose only the Podman-backed sandbox, or explicitly recreate it when a failed check offers that repair, run:

```bash
~/.amber/bin/amber workspace doctor my-workspace --stage container
~/.amber/bin/amber workspace doctor my-workspace --stage container --repair
```

Container repair preserves bind-mounted workspace data and does not enable Amber's optional user service.

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

If you want the service to start after reboot without an interactive login, enable user lingering first:

```bash
loginctl enable-linger "$USER"
```

Then verify the user manager is reachable before installing or starting the service:

```bash
systemctl --user show-environment
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
- `codex-skills/codex-development/SKILL.md` for Amber's general development workflow.
- `codex-skills/codex-pr-reviews/SKILL.md` for pull-request feedback handling.
- `codex-skills/python-style-rules/SKILL.md` for Python-specific implementation guidance.

Run `workspace doctor` after changing config or auth:

```bash
~/.amber/bin/amber workspace doctor my-workspace --external
```

## Optional Local ML

Source installs can enable the local ModernBERT attention scorer with:

```bash
pip install -r requirements-ml.txt
AMBER_ATTENTION_SCORER=modernbert AMBER_ATTENTION_DEVICE=cpu python main.py run --workspace my-workspace
```

The Full installer choice installs these requirements into Amber's managed optional environment and enables `attention.scorer = "modernbert"` for the workspace. It downloads the CPU PyTorch wheel from `download.pytorch.org`, Transformers from PyPI, and the pinned checkpoint from Hugging Face instead of bundling them in Amber's release archive.

## Developer Docs

Maintainer setup, tests, release building, and documentation rules live in [CONTRIBUTING.md](./CONTRIBUTING.md).

The source tree has local README files for the runtime and each major subsystem. Start with [src/README.md](./src/README.md).
