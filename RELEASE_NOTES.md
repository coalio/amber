# Amber 0.6.0

## Added

- Install the `coalio/codex-hooks` review gate by default at a pinned revision in each workspace's persistent Codex home.
- Configure the hook repository and revision through the workspace `[hooks]` table, including `repository = "none"` disablement.

## Changed

- Trust only exact global hook hashes reported by Codex, without trusting repository-local hooks.
- Preserve first-install conflict protection and use upstream backup behavior for updates from the same Amber-managed hook repository.
- Restart older Codex app-server protocol instances so hook installation and trust take effect after an Amber upgrade.

## Validation

- The 256-test unit suite covers hook configuration, installation, disablement, trust scoping, and app-server upgrade detection.
- The pinned hook package passes its 37-test upstream suite through Amber's generated first-install and managed-update paths.
