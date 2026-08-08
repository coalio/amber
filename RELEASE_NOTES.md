# Amber 0.4.0

## Added

- Let Full installs add CPU-only PyTorch, Transformers, and the pinned ModernBERT checkpoint from their upstream maintainers while Standard installs remain unchanged.
- Install focused Codex development, pull-request review, and Python style skills in every workspace.

## Changed

- Run optional ModernBERT scoring through an Amber-managed worker so the release archive stays small and does not bundle third-party ML runtimes.

## Validation

- The unit suite covers Standard and Full installer selection, optional runtime setup, packaged ModernBERT checks, and workspace skill installation.
