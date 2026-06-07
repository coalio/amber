#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT/venv/bin/python}"
DIST_DIR="$ROOT/dist"
BUILD_DIR="$ROOT/build"
APP_NAME="amber"
ASSET_NAME="${AMBER_ASSET_NAME:-amber-linux-x86_64.tar.gz}"
SPLIT_SIZE="${AMBER_SPLIT_SIZE:-1900M}"

if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3.14 || command -v python3)"
fi

if ! "$PYTHON" -c 'import PyInstaller' >/dev/null 2>&1; then
  echo "PyInstaller is not installed for $PYTHON. Run: $PYTHON -m pip install -r requirements.txt" >&2
  exit 1
fi

cd "$ROOT"
rm -rf "$BUILD_DIR/$APP_NAME" "$DIST_DIR/$APP_NAME" "$DIST_DIR/release" "$DIST_DIR/$ASSET_NAME" "$DIST_DIR/$ASSET_NAME".part-*

"$PYTHON" -m PyInstaller \
  --noconfirm \
  --clean \
  --onedir \
  --name "$APP_NAME" \
  --exclude-module pytest \
  --exclude-module _pytest \
  --exclude-module tests \
  main.py

STAGING="$DIST_DIR/release/$APP_NAME"
mkdir -p "$STAGING/resources/system" "$STAGING/resources/prompts" "$STAGING/resources/codex-skills/CodexRules"

cp -a "$DIST_DIR/$APP_NAME/." "$STAGING/"
cp "$ROOT/src/config/config.default.toml" "$STAGING/resources/config.default.toml"
cp "$ROOT/src/config/system/"*.md "$STAGING/resources/system/"
cp "$ROOT/src/config/AI_SYSTEM_CASUAL.md" "$STAGING/resources/prompts/"
cp "$ROOT/src/config/AI_SYSTEM_WORK.md" "$STAGING/resources/prompts/"
cp "$ROOT/src/config/AI_ACTION_CONTRACT.md" "$STAGING/resources/prompts/"
cp "$ROOT/src/config/AI_INTERRUPTION.md" "$STAGING/resources/prompts/"
cp "$ROOT/src/config/MEMORY.md" "$STAGING/resources/prompts/"
cp "$ROOT/src/config/skills/CodexRules/SKILL.md" "$STAGING/resources/codex-skills/CodexRules/SKILL.md"

tar -C "$STAGING" -czf "$DIST_DIR/$ASSET_NAME" .
sha256sum "$DIST_DIR/$ASSET_NAME" > "$DIST_DIR/$ASSET_NAME.sha256"
if [[ "$(stat -c%s "$DIST_DIR/$ASSET_NAME")" -gt 2000000000 ]]; then
  split -b "$SPLIT_SIZE" "$DIST_DIR/$ASSET_NAME" "$DIST_DIR/$ASSET_NAME.part-"
  echo "$DIST_DIR/$ASSET_NAME split into:"
  ls -lh "$DIST_DIR/$ASSET_NAME".part-*
fi
echo "$DIST_DIR/$ASSET_NAME"
