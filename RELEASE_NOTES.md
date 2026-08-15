# Amber 0.4.1

## Fixed

- Detect stopped or unhealthy Codex sandbox containers, stale bind mounts, invalid working directories, and unavailable app-server health through ordered workspace doctor stages.
- Let users explicitly recreate a repairable Codex container while preserving bind-mounted workspace data and leaving the optional systemd service untouched.

## Changed

- Have the installer diagnose an existing Codex sandbox before configuration, offer repair interactively, and provide an explicit repair command in headless mode.

## Validation

- The unit suite covers ordered doctor stages, repair eligibility, preserved workspace data, installer repair choices, and packaged doctor CLI behavior.
