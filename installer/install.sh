#!/usr/bin/env bash
set -euo pipefail

REPO="${AMBER_REPO:-coalio/amber-blue}"
AMBER_HOME="${AMBER_HOME:-$HOME/.amber}"
ASSET_NAME="${AMBER_ASSET_NAME:-amber-linux-x86_64.tar.gz}"
WORKSPACE_NAME="${1:-${AMBER_WORKSPACE:-}}"
RELEASE_ARCHIVE="${AMBER_RELEASE_ARCHIVE:-}"
RELEASE_URL="${AMBER_RELEASE_URL:-}"
RELEASE_TAG="${AMBER_RELEASE_TAG:-}"
TTY="${AMBER_TTY:-/dev/tty}"

need_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Amber installer requires '$1' on PATH." >&2
    exit 1
  fi
}

prompt() {
  local label="$1"
  local value=""
  if [[ ! -r "$TTY" ]]; then
    echo "Interactive setup requires a terminal. Set AMBER_WORKSPACE and run configure manually after install." >&2
    exit 1
  fi
  while [[ -z "$value" ]]; do
    read -r -p "$label: " value < "$TTY"
  done
  printf '%s' "$value"
}

latest_release_json() {
  curl -fsSL "https://api.github.com/repos/$REPO/releases/latest"
}

json_field() {
  local field="$1"
  sed -n "s/.*\"$field\": *\"\\([^\"]*\\)\".*/\\1/p" | head -n 1
}

asset_url_for() {
  local asset="$1"
  sed -n 's/.*"browser_download_url": *"\([^"]*\)".*/\1/p' | grep "/$asset$" | head -n 1
}

asset_part_urls_for() {
  local asset="$1"
  sed -n 's/.*"browser_download_url": *"\([^"]*\)".*/\1/p' | grep "/$asset.part-" | sort
}

install_release() {
  need_command curl
  need_command tar
  need_command mktemp

  local json tag url part_urls tmp archive release_tmp release_dir
  if [[ -n "$RELEASE_ARCHIVE" ]]; then
    tag="${RELEASE_TAG:-local}"
    url=""
  elif [[ -n "$RELEASE_URL" ]]; then
    tag="${RELEASE_TAG:-manual}"
    url="$RELEASE_URL"
  else
    json="$(latest_release_json)"
    tag="$(printf '%s\n' "$json" | json_field tag_name)"
    url="$(printf '%s\n' "$json" | asset_url_for "$ASSET_NAME")"
    part_urls="$(printf '%s\n' "$json" | asset_part_urls_for "$ASSET_NAME")"

    if [[ -z "$tag" || ( -z "$url" && -z "$part_urls" ) ]]; then
      echo "Could not find release asset '$ASSET_NAME' or split parts in latest $REPO release." >&2
      exit 1
    fi
  fi

  tmp="$(mktemp -d)"
  archive="$tmp/$ASSET_NAME"
  release_tmp="$tmp/release"
  release_dir="$AMBER_HOME/releases/$tag"

  if [[ -n "$RELEASE_ARCHIVE" ]]; then
    echo "Installing Amber $tag from $RELEASE_ARCHIVE..."
    cp "$RELEASE_ARCHIVE" "$archive"
  elif [[ -n "${part_urls:-}" && -z "$url" ]]; then
    echo "Downloading split Amber $tag assets from $REPO..."
    : > "$archive"
    local part part_file index
    index=0
    while IFS= read -r part; do
      part_file="$tmp/part-$index"
      curl -fL "$part" -o "$part_file"
      cat "$part_file" >> "$archive"
      index=$((index + 1))
    done <<< "$part_urls"
  else
    echo "Downloading Amber $tag from ${RELEASE_URL:-$REPO}..."
    curl -fL "$url" -o "$archive"
  fi
  mkdir -p "$release_tmp" "$AMBER_HOME/releases" "$AMBER_HOME/bin" "$AMBER_HOME/workspaces"
  tar -xzf "$archive" -C "$release_tmp"

  rm -rf "$release_dir"
  mkdir -p "$release_dir"
  cp -a "$release_tmp/." "$release_dir/"
  chmod +x "$release_dir/amber"

  ln -sfn "$tag" "$AMBER_HOME/releases/current"
  ln -sfn "../releases/current/amber" "$AMBER_HOME/bin/amber"
  echo "Installed Amber $tag to $release_dir"
}

configure_workspace() {
  local workspace="$1"
  "$AMBER_HOME/bin/amber" workspace init "$workspace"
  if [[ ! -r "$TTY" ]]; then
    echo "Interactive authentication requires a terminal. Run manually:" >&2
    echo "  $AMBER_HOME/bin/amber workspace configure $workspace" >&2
    exit 1
  fi
  "$AMBER_HOME/bin/amber" workspace configure "$workspace" < "$TTY"
}

maybe_install_service() {
  local workspace="$1"
  local answer="${AMBER_INSTALL_SERVICE:-}"
  if [[ -z "$answer" ]]; then
    if [[ ! -r "$TTY" ]]; then
      answer="n"
    else
      read -r -p "Start Amber automatically for this workspace with systemd --user? [y/N]: " answer < "$TTY"
    fi
  fi
  case "${answer,,}" in
    y|yes|1|true)
      "$AMBER_HOME/bin/amber" service install --workspace "$workspace" --enable --now
      if command -v loginctl >/dev/null 2>&1; then
        echo "Enabling linger lets this user service start after reboot without an interactive login."
        if ! loginctl enable-linger "$USER"; then
          echo "Could not enable linger automatically. Run manually if needed: loginctl enable-linger $USER" >&2
        fi
      fi
      ;;
    *)
      echo "Skipping systemd service setup. Run manually with:"
      echo "  $AMBER_HOME/bin/amber run --workspace $workspace"
      ;;
  esac
}

main() {
  install_release
  if [[ -z "$WORKSPACE_NAME" ]]; then
    WORKSPACE_NAME="$(prompt "Workspace name")"
  fi
  configure_workspace "$WORKSPACE_NAME"
  maybe_install_service "$WORKSPACE_NAME"
  echo "Amber is installed at $AMBER_HOME/bin/amber"
}

main "$@"
