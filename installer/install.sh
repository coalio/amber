#!/usr/bin/env bash
set -euo pipefail

REPO="${AMBER_REPO:-coalio/amber}"
AMBER_HOME="${AMBER_HOME:-$HOME/.amber}"
ASSET_NAME="${AMBER_ASSET_NAME:-amber-linux-x86_64.tar.gz}"
WORKSPACE_NAME="${1:-${AMBER_WORKSPACE:-}}"
RELEASE_ARCHIVE="${AMBER_RELEASE_ARCHIVE:-}"
RELEASE_URL="${AMBER_RELEASE_URL:-}"
RELEASE_TAG="${AMBER_RELEASE_TAG:-}"
TMP_PACKAGE="${AMBER_TMP_PACKAGE:-}"
RECOVER_TMP_PACKAGE="${AMBER_RECOVER_TMP_PACKAGE:-ask}"
TTY="${AMBER_TTY:-/dev/tty}"

if [[ -t 1 && -z "${NO_COLOR:-}" && "${TERM:-}" != "dumb" ]]; then
  COLOR_BOLD=$'\033[1m'
  COLOR_DIM=$'\033[2m'
  COLOR_BLUE=$'\033[34m'
  COLOR_GREEN=$'\033[32m'
  COLOR_YELLOW=$'\033[33m'
  COLOR_RED=$'\033[31m'
  COLOR_RESET=$'\033[0m'
else
  COLOR_BOLD=""
  COLOR_DIM=""
  COLOR_BLUE=""
  COLOR_GREEN=""
  COLOR_YELLOW=""
  COLOR_RED=""
  COLOR_RESET=""
fi

heading() {
  printf '%b%s%b\n' "$COLOR_BOLD$COLOR_YELLOW" "$*" "$COLOR_RESET"
}

info() {
  printf '%b==>%b %s\n' "$COLOR_BLUE" "$COLOR_RESET" "$*"
}

success() {
  printf '%bOK%b %s\n' "$COLOR_GREEN" "$COLOR_RESET" "$*"
}

warn() {
  printf '%bWARN%b %s\n' "$COLOR_YELLOW" "$COLOR_RESET" "$*" >&2
}

error() {
  printf '%bERROR%b %s\n' "$COLOR_RED" "$COLOR_RESET" "$*" >&2
}

prompt_label() {
  printf '%b%s%b' "$COLOR_BOLD" "$1" "$COLOR_RESET"
}

need_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    error "Amber installer requires '$1' on PATH."
    exit 1
  fi
}

prompt() {
  local label="$1"
  local value=""
  if [[ ! -r "$TTY" ]]; then
    error "Interactive setup requires a terminal. Set AMBER_WORKSPACE and run configure manually after install."
    exit 1
  fi
  while [[ -z "$value" ]]; do
    read -r -p "$(prompt_label "$label"): " value < "$TTY"
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
  sed -n 's/.*"browser_download_url": *"\([^"]*\)".*/\1/p' | grep "/$asset$" | head -n 1 || true
}

asset_part_urls_for() {
  local asset="$1"
  sed -n 's/.*"browser_download_url": *"\([^"]*\)".*/\1/p' | grep "/$asset.part-" | sort || true
}

archive_is_readable() {
  local archive="$1"
  local entries
  entries="$(
    set +o pipefail
    tar -tzf "$archive" 2>/dev/null | head -n 20
  )"
  printf '%s\n' "$entries" | grep -Eq '^(\./)?amber$'
}

curl_download() {
  local url="$1"
  local output="$2"
  if [[ -t 2 ]]; then
    curl -fL --progress-bar "$url" -o "$output"
  else
    curl -fsSL "$url" -o "$output"
  fi
}

file_size() {
  local path="$1"
  stat -c%s "$path" 2>/dev/null || wc -c < "$path"
}

confirm_tmp_package_recovery() {
  local archive="$1"
  local answer=""
  if [[ ! -r "$TTY" || ! -t 1 ]]; then
    return 1
  fi
  if ! read -r -p "$(prompt_label "Use existing downloaded package from $archive? [Y/n]"): " answer < "$TTY"; then
    return 1
  fi
  case "${answer,,}" in
    ""|y|yes|1|true)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

recoverable_tmp_archive() {
  local mode="${RECOVER_TMP_PACKAGE,,}"
  local archive size best best_size tmp_root

  if [[ -n "$TMP_PACKAGE" ]]; then
    if [[ -f "$TMP_PACKAGE" ]] && archive_is_readable "$TMP_PACKAGE"; then
      printf '%s\n' "$TMP_PACKAGE"
      return 0
    fi
    warn "AMBER_TMP_PACKAGE does not point to a readable Amber package: $TMP_PACKAGE"
    return 1
  fi

  case "$mode" in
    ""|ask)
      if [[ ! -r "$TTY" || ! -t 1 ]]; then
        return 1
      fi
      ;;
    y|yes|1|true|auto)
      ;;
    n|no|0|false|off|never)
      return 1
      ;;
    *)
      warn "Unknown AMBER_RECOVER_TMP_PACKAGE value '$RECOVER_TMP_PACKAGE'; skipping tmp package recovery."
      return 1
      ;;
  esac

  # prefer the largest readable tmp package so tiny test fixtures lose to real downloads
  best=""
  best_size=0
  tmp_root="${TMPDIR:-/tmp}"
  while IFS= read -r archive; do
    if [[ -f "$archive" ]] && archive_is_readable "$archive"; then
      size="$(file_size "$archive")"
      if (( size > best_size )); then
        best="$archive"
        best_size="$size"
      fi
    fi
  done < <(find "$tmp_root" -maxdepth 3 -type f -name "$ASSET_NAME" 2>/dev/null || true)

  if [[ -z "$best" ]]; then
    return 1
  fi
  if [[ "$mode" == "" || "$mode" == "ask" ]]; then
    confirm_tmp_package_recovery "$best" || return 1
  fi
  printf '%s\n' "$best"
}

copy_archive_to_cache() {
  local source="$1"
  local destination="$2"
  mkdir -p "${destination%/*}"
  cp "$source" "$destination.tmp"
  mv "$destination.tmp" "$destination"
}

download_github_release_archive() {
  local archive="$1"
  local tag="$2"
  local url="$3"
  local part_urls="$4"

  if [[ -n "$part_urls" && -z "$url" ]]; then
    info "Downloading split Amber $tag assets from $REPO..."
    : > "$archive"

    # assemble split assets into the tarball shape the rest of install expects
    local archive_dir part part_file index
    archive_dir="${archive%/*}"
    index=0
    while IFS= read -r part; do
      part_file="$archive_dir/part-$index"
      curl_download "$part" "$part_file"
      cat "$part_file" >> "$archive"
      index=$((index + 1))
    done <<< "$part_urls"
  else
    info "Downloading Amber $tag from $REPO..."
    curl_download "$url" "$archive"
  fi
}

install_release() {
  need_command curl
  need_command tar
  need_command mktemp

  local json tag url part_urls tmp archive release_tmp release_dir cache_dir cached_archive recovered_archive
  # resolve the release source before choosing a local cache path
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
      error "Could not find release asset '$ASSET_NAME' or split parts in latest $REPO release."
      exit 1
    fi
  fi

  # keep extraction work isolated from the persistent release directories
  tmp="$(mktemp -d)"
  archive="$tmp/$ASSET_NAME"
  release_tmp="$tmp/release"
  release_dir="$AMBER_HOME/releases/$tag"

  # cache normal github release downloads by tag and asset name
  if [[ -n "$RELEASE_ARCHIVE" ]]; then
    info "Installing Amber $tag from $RELEASE_ARCHIVE..."
    cp "$RELEASE_ARCHIVE" "$archive"
  elif [[ -n "$RELEASE_URL" ]]; then
    info "Downloading Amber $tag from $RELEASE_URL..."
    curl_download "$url" "$archive"
  else
    cache_dir="$AMBER_HOME/packages/$tag"
    cached_archive="$cache_dir/$ASSET_NAME"
    if [[ -f "$cached_archive" ]] && archive_is_readable "$cached_archive"; then
      info "Reusing downloaded Amber $tag package from $cached_archive..."
      archive="$cached_archive"
    else
      if [[ -f "$cached_archive" ]]; then
        warn "Cached Amber $tag package is not readable; downloading it again..."
      fi
      if recovered_archive="$(recoverable_tmp_archive)"; then
        info "Recovering downloaded Amber $tag package from $recovered_archive..."
        copy_archive_to_cache "$recovered_archive" "$cached_archive"
        archive="$cached_archive"
      else
        download_github_release_archive "$archive" "$tag" "$url" "${part_urls:-}"
        if ! archive_is_readable "$archive"; then
          error "Downloaded Amber $tag package is not a readable tar.gz archive."
          exit 1
        fi
        copy_archive_to_cache "$archive" "$cached_archive"
      fi
    fi
  fi

  # replace the selected release while preserving workspaces and old cached packages
  mkdir -p "$release_tmp" "$AMBER_HOME/releases" "$AMBER_HOME/bin" "$AMBER_HOME/workspaces"
  tar -xzf "$archive" -C "$release_tmp"

  rm -rf "$release_dir"
  mkdir -p "$release_dir"
  cp -a "$release_tmp/." "$release_dir/"
  chmod +x "$release_dir/amber"

  ln -sfn "$tag" "$AMBER_HOME/releases/current"
  ln -sfn "../releases/current/amber" "$AMBER_HOME/bin/amber"
  success "Installed Amber $tag to $release_dir"
}

configure_workspace() {
  local workspace="$1"
  info "Initializing workspace $workspace..."
  "$AMBER_HOME/bin/amber" workspace init "$workspace"
  if [[ ! -r "$TTY" ]]; then
    error "Interactive authentication requires a terminal. Run manually:"
    echo "  $AMBER_HOME/bin/amber workspace configure $workspace" >&2
    exit 1
  fi
  info "Configuring workspace $workspace..."
  printf '%b%s%b\n' "$COLOR_DIM" "Secret input is masked with asterisks." "$COLOR_RESET"
  "$AMBER_HOME/bin/amber" workspace configure "$workspace" < "$TTY"
}

maybe_install_service() {
  local workspace="$1"
  local answer="${AMBER_INSTALL_SERVICE:-}"
  if [[ -z "$answer" ]]; then
    if [[ ! -r "$TTY" ]]; then
      answer="n"
    else
      read -r -p "$(prompt_label "Start Amber automatically for this workspace with systemd --user? [y/N]"): " answer < "$TTY"
    fi
  fi
  case "${answer,,}" in
    y|yes|1|true)
      "$AMBER_HOME/bin/amber" service install --workspace "$workspace" --enable --now
      if command -v loginctl >/dev/null 2>&1; then
        info "Enabling linger lets this user service start after reboot without an interactive login."
        if ! loginctl enable-linger "$USER"; then
          warn "Could not enable linger automatically. Run manually if needed: loginctl enable-linger $USER"
        fi
      fi
      ;;
    *)
      info "Skipping systemd service setup. Run manually with:"
      echo "  $AMBER_HOME/bin/amber run --workspace $workspace"
      ;;
  esac
}

main() {
  heading "Amber installer"
  install_release
  if [[ -z "$WORKSPACE_NAME" ]]; then
    WORKSPACE_NAME="$(prompt "Workspace name")"
  fi
  configure_workspace "$WORKSPACE_NAME"
  maybe_install_service "$WORKSPACE_NAME"
  success "Amber is installed at $AMBER_HOME/bin/amber"
}

main "$@"
