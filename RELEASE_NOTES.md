# Amber 0.3.1

## Fixed

- Preserve nested Linear project and issue status defaults when interactive workspace setup saves credentials, preventing fresh installer runs from failing with a missing `linear.project.statuses` value.

## Validation

- The unit suite includes a packaged-installer regression that builds the standard archive, installs it through `installer/install.sh`, and runs the packaged workspace configuration past the former failure point.
