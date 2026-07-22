#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT/venv/bin/python}"
DIST_DIR="$ROOT/dist"
BUILD_DIR="$ROOT/build"
APP_NAME="amber"
DEFAULT_ASSET_NAME="amber-linux-x86_64.tar.gz"
FULL_ASSET_NAME="${AMBER_FULL_ASSET_NAME:-amber-linux-x86_64-full.tar.gz}"
SPLIT_SIZE="${AMBER_SPLIT_SIZE:-1900M}"
BUILD_ML="${AMBER_BUILD_ML:-}"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"

flag_enabled() {
  case "${1,,}" in
    y|yes|1|true|on)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

# reject malformed or mismatched release refs before producing publishable assets
if [[ ! "$VERSION" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
  echo "VERSION must contain a valid SemVer release, got: $VERSION" >&2
  exit 1
fi
CURRENT_BRANCH="$(git -C "$ROOT" branch --show-current)"
EXACT_TAG="$(git -C "$ROOT" describe --tags --exact-match 2>/dev/null || true)"
if [[ "$CURRENT_BRANCH" == release/* && "$CURRENT_BRANCH" != "release/$VERSION" ]]; then
  echo "Release branch $CURRENT_BRANCH does not match VERSION $VERSION" >&2
  exit 1
fi
if [[ -n "$EXACT_TAG" && "$EXACT_TAG" != "v$VERSION" ]]; then
  echo "Release tag $EXACT_TAG does not match VERSION $VERSION" >&2
  exit 1
fi

if [[ -n "${AMBER_ASSET_NAME:-}" ]]; then
  ASSET_NAME="$AMBER_ASSET_NAME"
elif flag_enabled "$BUILD_ML"; then
  ASSET_NAME="$FULL_ASSET_NAME"
else
  ASSET_NAME="$DEFAULT_ASSET_NAME"
fi

if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3.14 || command -v python3)"
fi

if ! "$PYTHON" -c 'import PyInstaller' >/dev/null 2>&1; then
  echo "PyInstaller is not installed for $PYTHON. Run: $PYTHON -m pip install -r requirements.txt" >&2
  exit 1
fi

cd "$ROOT"
rm -rf "$BUILD_DIR/$APP_NAME" "$DIST_DIR/$APP_NAME" "$DIST_DIR/release" "$DIST_DIR/$ASSET_NAME" "$DIST_DIR/$ASSET_NAME".part-*

PYINSTALLER_FLAGS=(
  --noconfirm
  --clean
  --onedir
  --name "$APP_NAME"
  --exclude-module pytest
  --exclude-module _pytest
  --exclude-module tests
)

if flag_enabled "$BUILD_ML"; then
  PYINSTALLER_FLAGS+=(--hidden-import src.attention.scoring.zero_shot)
else
  PYINSTALLER_FLAGS+=(
    --exclude-module torch
    --exclude-module transformers
    --exclude-module safetensors
    --exclude-module tokenizers
    --exclude-module huggingface_hub
    --exclude-module hf_xet
    --exclude-module nvidia
    --exclude-module triton
    --exclude-module cuda
    --exclude-module numpy
    --exclude-module pandas
    --exclude-module scipy
    --exclude-module sklearn
    --exclude-module joblib
    --exclude-module threadpoolctl
  )
fi

"$PYTHON" -m PyInstaller \
  "${PYINSTALLER_FLAGS[@]}" \
  main.py

STAGING="$DIST_DIR/release/$APP_NAME"
mkdir -p "$STAGING/resources/system" "$STAGING/resources/prompts" "$STAGING/resources/codex" "$STAGING/resources/codex-skills/CodexRules"

cp -a "$DIST_DIR/$APP_NAME/." "$STAGING/"
cp "$ROOT/VERSION" "$STAGING/VERSION"
cp "$ROOT/src/config/config.default.toml" "$STAGING/resources/config.default.toml"
cp "$ROOT/src/adapters/codex/app_server.py" "$STAGING/resources/codex/app_server.py"
cp "$ROOT/src/config/system/"*.md "$STAGING/resources/system/"
cp "$ROOT/src/config/AI_SYSTEM_CASUAL.md" "$STAGING/resources/prompts/"
cp "$ROOT/src/config/AI_SYSTEM_WORK.md" "$STAGING/resources/prompts/"
cp "$ROOT/src/config/AI_ACTION_CONTRACT.md" "$STAGING/resources/prompts/"
cp "$ROOT/src/config/AI_INTERRUPTION.md" "$STAGING/resources/prompts/"
cp "$ROOT/src/config/MEMORY.md" "$STAGING/resources/prompts/"
cp "$ROOT/src/config/skills/CodexRules/SKILL.md" "$STAGING/resources/codex-skills/CodexRules/SKILL.md"

tar -C "$STAGING" -czf "$DIST_DIR/$ASSET_NAME" .
(
  cd "$DIST_DIR"
  sha256sum "$ASSET_NAME" > "$ASSET_NAME.sha256"
)
if [[ "$(stat -c%s "$DIST_DIR/$ASSET_NAME")" -gt 2000000000 ]]; then
  split -b "$SPLIT_SIZE" "$DIST_DIR/$ASSET_NAME" "$DIST_DIR/$ASSET_NAME.part-"
  echo "$DIST_DIR/$ASSET_NAME split into:"
  ls -lh "$DIST_DIR/$ASSET_NAME".part-*
fi
echo "$DIST_DIR/$ASSET_NAME"
