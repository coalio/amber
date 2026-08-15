# Amber 0.4.2

## Fixed

- Restore the host dynamic-library environment before frozen Amber launches `systemctl`, Podman, the optional ML runtime, or other host programs. This prevents Amber's bundled OpenSSL from overriding newer host libraries.
- Report systemd user-service failures as concise CLI errors instead of unhandled subprocess tracebacks.

## Changed

- Check Linux x86-64 and GNU libc compatibility before downloading a release, then smoke-test the extracted binary before changing the active-release symlink.
- Enable lingering and wait for the systemd user manager before installing the optional user service.
- Document the complete Standard, Full, Codex sandbox, and optional user-service prerequisites.

## Validation

- The 202-test unit suite covers host-loader restoration, installer compatibility checks, service-manager ordering, and the packaged PyInstaller subprocess boundary.
