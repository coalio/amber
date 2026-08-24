# Amber 0.5.2

## Fixed

- Verify that every semver-tagged package contains the requested Amber version before it is cached, recovered, extracted, or activated.
- Ignore stale temporary downloads and poisoned cache entries instead of installing the largest readable archive under the wrong release tag.
- Reject mismatched downloaded, URL, and explicitly supplied archives before changing the active release symlink.

## Validation

- The 245-test unit suite includes stale temporary recovery, wrong-version caches, mismatched upstream downloads, explicit archive rejection, and preservation of the active release on failure.
- Three fixture-driven work-mode integration tests cover task delegation and Codex event delivery.
