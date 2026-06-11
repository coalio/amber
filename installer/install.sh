#!/usr/bin/env bash
set -euo pipefail

REPO="${AMBER_REPO:-coalio/amber}"
AMBER_HOME="${AMBER_HOME:-$HOME/.amber}"
DEFAULT_ASSET_NAME="amber-linux-x86_64.tar.gz"
FULL_ASSET_NAME="${AMBER_FULL_ASSET_NAME:-amber-linux-x86_64-full.tar.gz}"
ASSET_NAME_OVERRIDE="${AMBER_ASSET_NAME:-}"
ASSET_NAME="${ASSET_NAME_OVERRIDE:-$DEFAULT_ASSET_NAME}"
INSTALL_VARIANT="${AMBER_INSTALL_VARIANT:-}"
WORKSPACE_NAME=""
RELEASE_ARCHIVE="${AMBER_RELEASE_ARCHIVE:-}"
RELEASE_URL="${AMBER_RELEASE_URL:-}"
RELEASE_TAG="${AMBER_RELEASE_TAG:-}"
CODEX_CGROUP_MANAGER_OVERRIDE=""
CODEX_ENFORCE_RESOURCE_LIMITS_OVERRIDE=""
PODMAN_PROBE_ERROR=""
WORKSPACE_CONFIGURED=0
SERVICE_INSTALLED=0
VERBOSE=0
TTY="${AMBER_TTY:-/dev/tty}"
TTY_FD_OPEN=0
TTY_ANSWER=""

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

section() {
  printf '\n%b%s%b\n' "$COLOR_BOLD" "$*" "$COLOR_RESET"
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

installer_verbose() {
  (( VERBOSE ))
}

usage() {
  cat <<'USAGE'
Usage: install.sh [workspace-name] [-v|--verbose]

Options:
  -v, --verbose  Show full Podman probe diagnostics and Amber setup logs.

Examples:
  curl -fsSL https://raw.githubusercontent.com/coalio/amber/master/installer/install.sh | bash -s -- my-workspace
USAGE
}

parse_args() {
  local arg
  for arg in "$@"; do
    case "$arg" in
      -v|--verbose)
        VERBOSE=1
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      --)
        ;;
      -*)
        error "Unknown installer option: $arg"
        usage >&2
        exit 1
        ;;
      *)
        if [[ -n "$WORKSPACE_NAME" ]]; then
          error "Only one workspace name can be passed to the installer."
          usage >&2
          exit 1
        fi
        WORKSPACE_NAME="$arg"
        ;;
    esac
  done
}

progress_enabled() {
  [[ -t 2 && "${TERM:-}" != "dumb" ]]
}

progress_percent() {
  local current="$1"
  local total="$2"
  if (( total <= 0 )); then
    printf '0'
  elif (( current >= total )); then
    printf '100'
  else
    printf '%d' $((current * 100 / total))
  fi
}

draw_progress_bar() {
  local label="$1"
  local current="$2"
  local total="$3"
  local width=28
  local percent filled empty
  percent="$(progress_percent "$current" "$total")"
  filled=$((percent * width / 100))
  empty=$((width - filled))
  printf '\r%b==>%b %s [' "$COLOR_BLUE" "$COLOR_RESET" "$label" >&2
  printf '%*s' "$filled" '' | tr ' ' '#' >&2
  printf '%*s' "$empty" '' | tr ' ' '-' >&2
  printf '] %3d%%' "$percent" >&2
}

draw_activity_bar() {
  local label="$1"
  local step="$2"
  local width=28
  local pos=$((step % width))
  local index=0
  printf '\r%b==>%b %s [' "$COLOR_BLUE" "$COLOR_RESET" "$label" >&2
  while (( index < width )); do
    if (( index == pos )); then
      printf '#' >&2
    else
      printf '-' >&2
    fi
    index=$((index + 1))
  done
  printf ']' >&2
}

finish_progress() {
  printf '\n' >&2
}

need_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    error "Amber installer requires '$1' on PATH."
    exit 1
  fi
}

installer_is_interactive() {
  [[ -r "$TTY" ]]
}

open_tty_input() {
  if (( TTY_FD_OPEN )); then
    return 0
  fi
  [[ -r "$TTY" ]] || return 1
  exec 3< "$TTY"
  TTY_FD_OPEN=1
}

read_tty_answer() {
  local label="$1"
  TTY_ANSWER=""
  open_tty_input || return 1
  read -r -p "$(prompt_label "$label"): " TTY_ANSWER <&3
}

answer_is_yes() {
  case "${1,,}" in
    y|yes|1|true|on)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

choice_menu_enabled() {
  open_tty_input || return 1
  [[ -t 3 && -t 2 && "${TERM:-}" != "dumb" ]]
}

draw_choice_menu() {
  local label="$1"
  local selected="$2"
  shift 2
  local options=("$@")
  local index=0

  printf '\033[2K%b%s%b\n' "$COLOR_BOLD" "$label" "$COLOR_RESET" >&2
  for option in "${options[@]}"; do
    if (( index == selected )); then
      printf '\033[2K  > %s\n' "$option" >&2
    else
      printf '\033[2K    %s\n' "$option" >&2
    fi
    index=$((index + 1))
  done
}

prompt_choice_index() {
  local label="$1"
  local selected="$2"
  shift 2
  local options=("$@")
  local key rest line_count option_count
  option_count="${#options[@]}"
  line_count=$((option_count + 2))

  draw_choice_menu "$label" "$selected" "${options[@]}"
  while IFS= read -rsn1 key <&3; do
    case "$key" in
      "")
        printf '\n' >&2
        printf '%s\n' "$selected"
        return 0
        ;;
      $'\x1b')
        rest=""
        read -rsn2 -t 0.1 rest <&3 || true
        case "$rest" in
          "[A")
            selected=$(((selected + option_count - 1) % option_count))
            ;;
          "[B")
            selected=$(((selected + 1) % option_count))
            ;;
        esac
        ;;
      k|K)
        selected=$(((selected + option_count - 1) % option_count))
        ;;
      j|J)
        selected=$(((selected + 1) % option_count))
        ;;
      [1-9])
        if (( key >= 1 && key <= option_count )); then
          selected=$((key - 1))
        fi
        ;;
    esac
    printf '\033[%dA' "$line_count" >&2
    draw_choice_menu "$label" "$selected" "${options[@]}"
  done

  printf '\n' >&2
  printf '%s\n' "$selected"
}

ask_yes_no() {
  local label="$1"
  local default="$2"
  local prompt answer default_index selected
  if choice_menu_enabled; then
    if [[ "$default" == "yes" ]]; then
      default_index=0
    else
      default_index=1
    fi
    selected="$(prompt_choice_index "$label" "$default_index" "Yes" "No")"
    [[ "$selected" == "0" ]]
    return
  fi

  case "$default" in
    yes)
      prompt="$label [Y/n]"
      ;;
    no)
      prompt="$label [y/N]"
      ;;
    *)
      prompt="$label [y/n]"
      ;;
  esac

  if ! installer_is_interactive || ! read_tty_answer "$prompt"; then
    [[ "$default" == "yes" ]]
    return
  fi

  answer="${TTY_ANSWER,,}"
  if [[ -z "$answer" ]]; then
    [[ "$default" == "yes" ]]
    return
  fi
  answer_is_yes "$answer"
}

normalize_install_variant() {
  local value="${1,,}"
  case "$value" in
    standard|default|lite|minimal)
      printf 'standard'
      ;;
    full|ml|modernbert|bert)
      printf 'full'
      ;;
    *)
      error "Unknown Amber install variant: $1"
      error "Use 'standard' or 'full'."
      exit 1
      ;;
  esac
}

configure_release_asset_choice() {
  if [[ -n "$INSTALL_VARIANT" ]]; then
    INSTALL_VARIANT="$(normalize_install_variant "$INSTALL_VARIANT")"
    if [[ -z "$ASSET_NAME_OVERRIDE" ]]; then
      if [[ "$INSTALL_VARIANT" == "full" ]]; then
        ASSET_NAME="$FULL_ASSET_NAME"
      else
        ASSET_NAME="$DEFAULT_ASSET_NAME"
      fi
    fi
  elif [[ -n "$ASSET_NAME_OVERRIDE" || -n "$RELEASE_ARCHIVE" || -n "$RELEASE_URL" ]]; then
    INSTALL_VARIANT="custom"
  else
    choose_release_asset_variant
  fi

  case "$INSTALL_VARIANT" in
    full)
      info "Using full Amber package with local ModernBERT scorer: $ASSET_NAME"
      ;;
    standard)
      info "Using standard Amber package: $ASSET_NAME"
      ;;
    custom)
      info "Using custom Amber package source: $ASSET_NAME"
      ;;
  esac
}

choose_release_asset_variant() {
  local selected answer prompt
  if choice_menu_enabled; then
    selected="$(
      prompt_choice_index \
        "Which Amber version should be installed?" \
        0 \
        "Standard - smaller install, heuristic attention scoring" \
        "Full - includes local ModernBERT scorer, about 2 GB"
    )"
    if [[ "$selected" == "1" ]]; then
      INSTALL_VARIANT="full"
      ASSET_NAME="$FULL_ASSET_NAME"
    else
      INSTALL_VARIANT="standard"
      ASSET_NAME="$DEFAULT_ASSET_NAME"
    fi
    return
  fi

  if ! installer_is_interactive; then
    INSTALL_VARIANT="standard"
    ASSET_NAME="$DEFAULT_ASSET_NAME"
    return
  fi

  printf '  1) Standard - smaller install, heuristic attention scoring\n' >&2
  printf '  2) Full - includes local ModernBERT scorer, about 2 GB\n' >&2
  prompt="Which Amber version should be installed? [1/2]"
  if ! read_tty_answer "$prompt"; then
    INSTALL_VARIANT="standard"
    ASSET_NAME="$DEFAULT_ASSET_NAME"
    return
  fi

  answer="${TTY_ANSWER,,}"
  case "$answer" in
    2|full|ml|modernbert|bert)
      INSTALL_VARIANT="full"
      ASSET_NAME="$FULL_ASSET_NAME"
      ;;
    *)
      INSTALL_VARIANT="standard"
      ASSET_NAME="$DEFAULT_ASSET_NAME"
      ;;
  esac
}

amber_log_to_stderr() {
  if installer_verbose; then
    printf '1'
  else
    printf '0'
  fi
}

run_amber() {
  AMBER_LOG_TO_STDERR="$(amber_log_to_stderr)" "$AMBER_HOME/bin/amber" "$@"
}

join_words() {
  local word
  local first=1
  for word in "$@"; do
    if (( first )); then
      first=0
    else
      printf ' '
    fi
    printf '%s' "$word"
  done
}

package_for_command() {
  case "$1" in
    curl)
      printf 'curl'
      ;;
    tar)
      printf 'tar'
      ;;
    find)
      printf 'findutils'
      ;;
    grep)
      printf 'grep'
      ;;
    sed)
      printf 'sed'
      ;;
    podman)
      printf 'podman'
      ;;
    slirp4netns)
      printf 'slirp4netns'
      ;;
    *)
      printf 'coreutils'
      ;;
  esac
}

unique_packages_for_commands() {
  local command package seen packages=()
  for command in "$@"; do
    package="$(package_for_command "$command")"
    seen=0
    for existing in "${packages[@]}"; do
      if [[ "$existing" == "$package" ]]; then
        seen=1
        break
      fi
    done
    if (( ! seen )); then
      packages+=("$package")
    fi
  done
  printf '%s\n' "${packages[@]}"
}

system_install_command_text() {
  local packages=("$@")
  local package_text
  package_text="$(join_words "${packages[@]}")"
  if command -v apt-get >/dev/null 2>&1; then
    if (( EUID == 0 )); then
      printf 'apt-get update && apt-get install -y %s' "$package_text"
    else
      printf 'sudo apt-get update && sudo apt-get install -y %s' "$package_text"
    fi
    return 0
  fi
  if command -v dnf >/dev/null 2>&1; then
    if (( EUID == 0 )); then
      printf 'dnf install -y %s' "$package_text"
    else
      printf 'sudo dnf install -y %s' "$package_text"
    fi
    return 0
  fi
  if command -v pacman >/dev/null 2>&1; then
    if (( EUID == 0 )); then
      printf 'pacman -Sy --needed %s' "$package_text"
    else
      printf 'sudo pacman -Sy --needed %s' "$package_text"
    fi
    return 0
  fi
  return 1
}

run_system_install_command() {
  local packages=("$@")
  if command -v apt-get >/dev/null 2>&1; then
    if (( EUID == 0 )); then
      apt-get update && apt-get install -y "${packages[@]}"
    else
      sudo apt-get update && sudo apt-get install -y "${packages[@]}"
    fi
    return
  fi
  if command -v dnf >/dev/null 2>&1; then
    if (( EUID == 0 )); then
      dnf install -y "${packages[@]}"
    else
      sudo dnf install -y "${packages[@]}"
    fi
    return
  fi
  if command -v pacman >/dev/null 2>&1; then
    if (( EUID == 0 )); then
      pacman -Sy --needed "${packages[@]}"
    else
      sudo pacman -Sy --needed "${packages[@]}"
    fi
    return
  fi
  return 1
}

collect_missing_commands() {
  local command
  for command in "$@"; do
    if ! command -v "$command" >/dev/null 2>&1; then
      printf '%s\n' "$command"
    fi
  done
}

offer_system_package_fix() {
  local missing_commands=("$@")
  local packages=()
  local package install_text
  while IFS= read -r package; do
    [[ -n "$package" ]] && packages+=("$package")
  done < <(unique_packages_for_commands "${missing_commands[@]}")

  if ! install_text="$(system_install_command_text "${packages[@]}")"; then
    warn "Install missing prerequisites manually: $(join_words "${missing_commands[@]}")"
    return 1
  fi

  warn "Missing required host commands: $(join_words "${missing_commands[@]}")"
  warn "Install command:"
  printf '  %s\n' "$install_text" >&2

  if ! installer_is_interactive; then
    warn "Non-interactive install will not modify system packages."
    return 1
  fi
  ask_yes_no "Run this system package command now?" "no" || return 1

  run_system_install_command "${packages[@]}"
}

podman_info_output() {
  local podman_executable="$1"
  "$podman_executable" info --debug 2>&1
}

compact_lower() {
  tr '[:upper:]' '[:lower:]' | tr -d '[:space:]'
}

podman_info_has_true() {
  local output="$1"
  local key="$2"
  local compact
  compact="$(printf '%s' "$output" | compact_lower)"
  [[ "$compact" == *"\"${key}\":true"* || "$compact" == *"${key}:true"* ]]
}

podman_info_mentions() {
  local output="$1"
  local key="$2"
  local value="$3"
  local compact normalized_key
  compact="$(printf '%s' "$output" | compact_lower)"
  normalized_key="${key// /}"
  [[ "$compact" == *"\"${normalized_key}\":\"${value}\""* || "$compact" == *"${normalized_key}:${value}"* ]]
}

podman_info_contains() {
  local output="$1"
  local needle="$2"
  local lower_output lower_needle
  lower_output="$(printf '%s' "$output" | tr '[:upper:]' '[:lower:]')"
  lower_needle="$(printf '%s' "$needle" | tr '[:upper:]' '[:lower:]')"
  [[ "$lower_output" == *"$lower_needle"* ]]
}

local_cgroup_v2_ok() {
  local fs_type
  fs_type="$(stat -fc %T /sys/fs/cgroup 2>/dev/null || true)"
  [[ "$fs_type" == "cgroup2fs" ]] || return 1
  grep -q ' - cgroup2 ' /proc/self/mountinfo 2>/dev/null
}

podman_probe_image() {
  local candidate
  local candidates=(
    "amber-codex-sandbox:ubuntu-24.04-codex-cli"
    "localhost/amber-codex-sandbox:ubuntu-24.04-codex-cli"
    "ubuntu:24.04"
    "docker.io/library/ubuntu:24.04"
  )
  for candidate in "${candidates[@]}"; do
    if podman image exists "$candidate" >/dev/null 2>&1; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

run_podman_cgroup_probe() {
  local image="$1"
  local manager="$2"
  local enforce_limits="$3"
  local stderr_file="$4"
  local container_name
  local command=(podman)
  container_name="amber-cgroup-probe-$$-$RANDOM"

  if [[ -n "$manager" ]]; then
    command+=("--cgroup-manager=$manager")
  fi
  command+=(
    run
    --rm
    --name "$container_name"
    --userns=keep-id
    --network=slirp4netns
  )
  if [[ "$enforce_limits" == "true" ]]; then
    command+=(
      "--memory=4g"
      "--cpus=2"
      "--pids-limit=512"
    )
  fi
  command+=(
    --cap-drop=all
    --security-opt=no-new-privileges
    "$image"
    true
  )

  if "${command[@]}" >/dev/null 2>"$stderr_file"; then
    return 0
  fi
  podman rm -f "$container_name" >/dev/null 2>&1 || true
  return 1
}

podman_error_looks_like_cgroup_mode() {
  local message="$1"
  local lower
  lower="$(printf '%s' "$message" | tr '[:upper:]' '[:lower:]')"
  [[ "$lower" == *"cgroup"* \
    || "$lower" == *"interactive authentication required"* \
    || "$lower" == *"nocgroups"* ]]
}

podman_probe_error_summary() {
  local message="$1"
  local lower
  lower="$(printf '%s' "$message" | tr '[:upper:]' '[:lower:]')"

  if [[ "$lower" == *"interactive authentication required"* ]]; then
    printf '%s\n' "Podman could not start a rootless container with its default cgroup manager: interactive authentication required."
    return
  fi
  if [[ "$lower" == *"unable to apply cgroup configuration"* ]]; then
    printf '%s\n' "Podman could not apply its cgroup configuration for the sandbox probe."
    return
  fi
  if [[ "$lower" == *"could not find cgroup mount"* ]]; then
    printf '%s\n' "Podman could not find a usable cgroup mount for the sandbox probe."
    return
  fi
  if [[ "$lower" == *"not compatible with nocgroups"* || "$lower" == *"nocgroups"* ]]; then
    printf '%s\n' "Podman rejected the current cgroup mode for this sandbox probe."
    return
  fi
  printf '%s\n' "Podman failed the sandbox probe."
}

show_podman_probe_error() {
  local message="$1"
  local line
  [[ -n "$message" ]] || return 0

  if installer_verbose; then
    warn "Full Podman probe error:"
    while IFS= read -r line; do
      [[ -n "$line" ]] && warn "  $line"
    done <<< "$message"
    return 0
  fi

  warn "$(podman_probe_error_summary "$message")"
}

probe_codex_cgroup_mode() {
  local image="$1"
  local manager="$2"
  local enforce_limits="$3"
  local stderr_file

  PODMAN_PROBE_ERROR=""
  stderr_file="$(mktemp)"
  if run_podman_cgroup_probe "$image" "$manager" "$enforce_limits" "$stderr_file"; then
    rm -f "$stderr_file"
    return 0
  fi
  PODMAN_PROBE_ERROR="$(cat "$stderr_file" 2>/dev/null || true)"
  rm -f "$stderr_file"
  return 1
}

apply_codex_no_limits_fallback() {
  local image="$1"
  local require_confirmation="$2"
  local failure_message="$3"

  if ! podman_error_looks_like_cgroup_mode "$PODMAN_PROBE_ERROR"; then
    warn "Codex Podman probe failed, but it did not look like a cgroup-mode error."
    show_podman_probe_error "$PODMAN_PROBE_ERROR"
    return 1
  fi

  warn "$failure_message"
  show_podman_probe_error "$PODMAN_PROBE_ERROR"
  if [[ "$require_confirmation" == "true" ]]; then
    ask_yes_no "Try Amber's workspace-only fallback with cgroupfs and no Codex resource limits?" "yes" || return 1
  else
    info "Trying Amber's workspace-only fallback with cgroupfs and Codex resource limits disabled..."
  fi

  if probe_codex_cgroup_mode "$image" "cgroupfs" "false"; then
    CODEX_CGROUP_MANAGER_OVERRIDE="cgroupfs"
    CODEX_ENFORCE_RESOURCE_LIMITS_OVERRIDE="false"
    success "Podman fallback probe passed with cgroupfs and Codex resource limits disabled"
    return 0
  fi

  warn "Podman fallback probe still failed; leaving Codex Podman settings unchanged."
  show_podman_probe_error "$PODMAN_PROBE_ERROR"
  return 1
}

apply_codex_no_limits_fallback_if_confirmed() {
  local image="$1"
  apply_codex_no_limits_fallback \
    "$image" \
    "true" \
    "Codex Podman probe failed with the requested cgroup/resource-limit mode."
}

apply_codex_no_limits_fallback_after_disabled_choice() {
  local image="$1"
  apply_codex_no_limits_fallback \
    "$image" \
    "false" \
    "Codex Podman probe still failed with resource limits disabled because Podman rejected its default cgroup mode."
}

configure_codex_cgroup_choice() {
  local podman_info="$1"
  local image

  CODEX_CGROUP_MANAGER_OVERRIDE=""
  CODEX_ENFORCE_RESOURCE_LIMITS_OVERRIDE=""

  if ! ask_yes_no "Use cgroup-backed resource limits for the Codex sandbox?" "yes"; then
    CODEX_ENFORCE_RESOURCE_LIMITS_OVERRIDE="false"
    info "Codex resource limits will be disabled for this workspace."
    info "Probing Podman without Codex resource-limit flags..."
    if image="$(podman_probe_image)" && ! probe_codex_cgroup_mode "$image" "" "false"; then
      apply_codex_no_limits_fallback_after_disabled_choice "$image" || true
    fi
    return 0
  fi

  if image="$(podman_probe_image)"; then
    info "Probing Podman with Codex cgroup-backed resource limits..."
    if probe_codex_cgroup_mode "$image" "" "true"; then
      success "Podman resource-limit probe passed"
      return 0
    fi
    apply_codex_no_limits_fallback_if_confirmed "$image" || true
    return 0
  fi

  if podman_info_mentions "$podman_info" "cgroupManager" "systemd" \
    && podman_info_contains "$podman_info" "microsoft-standard-wsl"; then
    warn "Podman is rootless with systemd cgroups inside WSL, but no local image was available for a smoke probe."
    if ask_yes_no "Apply Amber's workspace-only cgroupfs/no-limits fallback anyway?" "yes"; then
      CODEX_CGROUP_MANAGER_OVERRIDE="cgroupfs"
      CODEX_ENFORCE_RESOURCE_LIMITS_OVERRIDE="false"
    fi
  fi
}

preflight_installer() {
  local required_commands=(
    curl tar mktemp stat find grep sed awk head tr wc cp mv sort cat chmod ln rm mkdir sleep podman slirp4netns
  )
  local missing=()
  local still_missing=()
  local failures=()
  local command podman_info podman_help flag
  local required_podman_flags=(--userns --network)

  info "Checking host prerequisites..."
  while IFS= read -r command; do
    [[ -n "$command" ]] && missing+=("$command")
  done < <(collect_missing_commands "${required_commands[@]}")

  if (( ${#missing[@]} )); then
    offer_system_package_fix "${missing[@]}" || true
    while IFS= read -r command; do
      [[ -n "$command" ]] && still_missing+=("$command")
    done < <(collect_missing_commands "${required_commands[@]}")
    if (( ${#still_missing[@]} )); then
      failures+=("Missing required host commands: $(join_words "${still_missing[@]}")")
    fi
  fi

  if command -v podman >/dev/null 2>&1; then
    if ! podman_info="$(podman_info_output podman)"; then
      failures+=("podman info failed. Run 'podman info --debug' and fix the reported Podman error before installing Amber.")
    else
      if ! podman_info_has_true "$podman_info" "rootless"; then
        failures+=("Podman must run rootless. Verify with: podman info --debug | grep -i rootless")
      fi
      if ! podman_info_mentions "$podman_info" "cgroupversion" "v2" \
        && ! podman_info_mentions "$podman_info" "cgroup version" "v2"; then
        failures+=("Podman must report cgroup v2. Verify with: podman info --debug | grep -i cgroup")
      fi
      configure_codex_cgroup_choice "$podman_info"
    fi
    if [[ "$CODEX_ENFORCE_RESOURCE_LIMITS_OVERRIDE" != "false" ]]; then
      required_podman_flags+=(--memory --cpus --pids-limit)
    fi
    if [[ "$CODEX_ENFORCE_RESOURCE_LIMITS_OVERRIDE" != "false" ]] && ! local_cgroup_v2_ok; then
      failures+=("The host must expose a cgroup v2 mount. Verify with: stat -fc %T /sys/fs/cgroup")
    elif [[ "$CODEX_ENFORCE_RESOURCE_LIMITS_OVERRIDE" == "false" ]] && ! local_cgroup_v2_ok; then
      warn "The host does not expose cgroup v2; continuing because Codex resource limits are disabled."
    fi
    if ! podman_help="$(podman run --help 2>&1)"; then
      failures+=("podman run --help failed. Upgrade or repair Podman before installing Amber.")
    else
      for flag in "${required_podman_flags[@]}"; do
        if [[ "$podman_help" != *"$flag"* ]]; then
          failures+=("Podman is missing required run flag '$flag'. Upgrade Podman before installing Amber.")
        fi
      done
    fi
  fi

  if (( ${#failures[@]} )); then
    error "Amber installer preflight failed before downloading the release."
    printf '%s\n' "${failures[@]/#/- }" >&2
    exit 1
  fi
  success "Host prerequisites look ready"
}

prompt() {
  local label="$1"
  local value=""
  if ! installer_is_interactive; then
    error "Interactive setup requires a terminal. Pass a workspace name as the first installer argument."
    exit 1
  fi
  while [[ -z "$value" ]]; do
    read_tty_answer "$label" || {
      error "Interactive setup requires a terminal. Pass a workspace name as the first installer argument."
      exit 1
    }
    value="$TTY_ANSWER"
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

copy_with_progress() {
  local source="$1"
  local destination="$2"
  local label="$3"
  local current=0
  local total
  local pid
  local status

  if ! progress_enabled; then
    info "$label..."
    cp "$source" "$destination"
    return
  fi

  total="$(file_size "$source")"
  cp "$source" "$destination" &
  pid="$!"
  while kill -0 "$pid" >/dev/null 2>&1; do
    if [[ -f "$destination" ]]; then
      current="$(file_size "$destination")"
    fi
    draw_progress_bar "$label" "$current" "$total"
    sleep 0.2
  done
  if wait "$pid"; then
    status=0
  else
    status="$?"
  fi
  if [[ -f "$destination" ]]; then
    current="$(file_size "$destination")"
  fi
  draw_progress_bar "$label" "$current" "$total"
  finish_progress
  return "$status"
}

run_with_activity_progress() {
  local label="$1"
  shift
  local pid
  local status
  local step=0

  if ! progress_enabled; then
    info "$label..."
    "$@"
    return
  fi

  "$@" &
  pid="$!"
  while kill -0 "$pid" >/dev/null 2>&1; do
    draw_activity_bar "$label" "$step"
    step=$((step + 1))
    sleep 0.2
  done
  if wait "$pid"; then
    status=0
  else
    status="$?"
  fi
  draw_progress_bar "$label" 1 1
  finish_progress
  return "$status"
}

confirm_tmp_package_recovery() {
  local archive="$1"
  ask_yes_no "Use existing downloaded package from $archive?" "yes"
}

confirm_cached_archive_use() {
  local archive="$1"
  ask_yes_no "Use cached Amber package from $archive?" "yes"
}

recoverable_tmp_archive() {
  local archive size best best_size tmp_root

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
  confirm_tmp_package_recovery "$best" || return 1
  printf '%s\n' "$best"
}

copy_archive_to_cache() {
  local source="$1"
  local destination="$2"
  mkdir -p "${destination%/*}"
  copy_with_progress "$source" "$destination.tmp" "Caching Amber package"
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
    archive="$RELEASE_ARCHIVE"
  elif [[ -n "$RELEASE_URL" ]]; then
    info "Downloading Amber $tag from $RELEASE_URL..."
    curl_download "$url" "$archive"
  else
    cache_dir="$AMBER_HOME/packages/$tag"
    cached_archive="$cache_dir/$ASSET_NAME"
    if [[ -f "$cached_archive" ]] && archive_is_readable "$cached_archive" && confirm_cached_archive_use "$cached_archive"; then
      info "Using cached Amber $tag package from $cached_archive..."
      archive="$cached_archive"
    else
      if [[ -f "$cached_archive" ]] && ! archive_is_readable "$cached_archive"; then
        warn "Cached Amber $tag package is not readable; downloading it again..."
      elif [[ -f "$cached_archive" ]]; then
        info "Downloading a fresh Amber $tag package..."
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
        archive="$cached_archive"
      fi
    fi
  fi

  # replace the selected release while preserving workspaces and old cached packages
  mkdir -p "$release_tmp" "$AMBER_HOME/releases" "$AMBER_HOME/bin" "$AMBER_HOME/workspaces"
  run_with_activity_progress "Extracting Amber $tag" tar -xzf "$archive" -C "$release_tmp"

  rm -rf "$release_dir"
  mkdir -p "$release_dir"
  run_with_activity_progress "Installing Amber $tag" cp -a "$release_tmp/." "$release_dir/"
  chmod +x "$release_dir/amber"

  ln -sfn "$tag" "$AMBER_HOME/releases/current"
  ln -sfn "../releases/current/amber" "$AMBER_HOME/bin/amber"
  success "Installed Amber $tag to $release_dir"
}

set_toml_key() {
  local file="$1"
  local section="$2"
  local key="$3"
  local value="$4"
  local tmp
  tmp="$file.tmp"
  awk -v section="$section" -v key="$key" -v value="$value" '
    BEGIN {
      in_section = 0
      section_seen = 0
      key_set = 0
    }
    $0 == "[" section "]" {
      if (in_section && !key_set) {
        print key " = " value
        key_set = 1
      }
      in_section = 1
      section_seen = 1
      print
      next
    }
    /^\[/ {
      if (in_section && !key_set) {
        print key " = " value
        key_set = 1
      }
      in_section = 0
    }
    in_section && $0 ~ "^[[:space:]]*" key "[[:space:]]*=" {
      print key " = " value
      key_set = 1
      next
    }
    { print }
    END {
      if (!section_seen) {
        print ""
        print "[" section "]"
        print key " = " value
      } else if (in_section && !key_set) {
        print key " = " value
      }
    }
  ' "$file" > "$tmp"
  mv "$tmp" "$file"
}

apply_codex_workspace_overrides() {
  local workspace="$1"
  local config_path="$AMBER_HOME/workspaces/$workspace/config.toml"

  if [[ -z "$CODEX_CGROUP_MANAGER_OVERRIDE" && -z "$CODEX_ENFORCE_RESOURCE_LIMITS_OVERRIDE" ]]; then
    return 0
  fi
  if [[ ! -f "$config_path" ]]; then
    warn "Could not find workspace config at $config_path; Codex Podman settings were not changed."
    return 0
  fi

  info "Applying Codex Podman workspace settings..."
  if [[ -n "$CODEX_CGROUP_MANAGER_OVERRIDE" ]]; then
    set_toml_key "$config_path" "codex" "podman_cgroup_manager" "\"$CODEX_CGROUP_MANAGER_OVERRIDE\""
  fi
  if [[ -n "$CODEX_ENFORCE_RESOURCE_LIMITS_OVERRIDE" ]]; then
    set_toml_key "$config_path" "codex" "enforce_resource_limits" "$CODEX_ENFORCE_RESOURCE_LIMITS_OVERRIDE"
  fi
  chmod 600 "$config_path" || true
}

apply_full_release_workspace_overrides() {
  local workspace="$1"
  local config_path="$AMBER_HOME/workspaces/$workspace/config.toml"

  if [[ "$INSTALL_VARIANT" != "full" ]]; then
    return 0
  fi
  if [[ ! -f "$config_path" ]]; then
    warn "Could not find workspace config at $config_path; local ML attention scoring was not enabled."
    return 0
  fi

  info "Enabling local ModernBERT attention scorer for this workspace..."
  set_toml_key "$config_path" "attention" "scorer" "\"modernbert\""
  chmod 600 "$config_path" || true
}

configure_workspace() {
  local workspace="$1"
  info "Initializing workspace $workspace..."
  run_amber workspace init "$workspace"
  apply_codex_workspace_overrides "$workspace"
  apply_full_release_workspace_overrides "$workspace"

  if ! installer_is_interactive; then
    warn "No terminal is available for interactive workspace configuration. Run manually:"
    echo "  $AMBER_HOME/bin/amber workspace configure $workspace" >&2
    WORKSPACE_CONFIGURED=0
    return 0
  fi
  if ! ask_yes_no "Configure Telegram, OpenAI, Linear, Codex, and GitHub now?" "yes"; then
    info "Skipping workspace configuration. Run manually with:"
    echo "  $AMBER_HOME/bin/amber workspace configure $workspace"
    WORKSPACE_CONFIGURED=0
    return 0
  fi

  info "Configuring workspace $workspace..."
  printf '%b%s%b\n' "$COLOR_DIM" "Secret input is masked with asterisks." "$COLOR_RESET"
  open_tty_input || {
    error "Interactive authentication requires a terminal. Run manually:"
    echo "  $AMBER_HOME/bin/amber workspace configure $workspace" >&2
    exit 1
  }
  run_amber workspace configure "$workspace" <&3
  WORKSPACE_CONFIGURED=1
}

maybe_install_service() {
  local workspace="$1"
  if (( ! WORKSPACE_CONFIGURED )); then
    info "Skipping service setup until the workspace is configured."
    return 0
  fi
  if ! ask_yes_no "Start Amber automatically for this workspace with systemd --user?" "no"; then
    info "Skipping systemd service setup. Run manually with:"
    echo "  $AMBER_HOME/bin/amber run --workspace $workspace"
    return 0
  fi

  run_amber service install --workspace "$workspace" --enable --now
  SERVICE_INSTALLED=1
  if command -v loginctl >/dev/null 2>&1; then
    info "Enabling linger lets this user service start after reboot without an interactive login."
    if ! loginctl enable-linger "$USER"; then
      warn "Could not enable linger automatically. Run manually if needed: loginctl enable-linger $USER"
    fi
  fi
}

shell_quote() {
  printf '%q' "$1"
}

print_next_steps() {
  local workspace="$1"
  local amber_bin="$AMBER_HOME/bin/amber"
  local quoted_workspace quoted_amber_bin

  quoted_workspace="$(shell_quote "$workspace")"
  quoted_amber_bin="$(shell_quote "$amber_bin")"

  section "Next steps"
  printf 'Amber is installed.\n\n'
  printf 'Workspace: %s\n' "$workspace"
  printf 'Binary:    %s\n\n' "$amber_bin"

  if (( WORKSPACE_CONFIGURED )); then
    printf 'Check the workspace:\n'
    printf '  %s workspace doctor %s --external --service\n\n' "$quoted_amber_bin" "$quoted_workspace"
  else
    printf 'Finish configuration:\n'
    printf '  %s workspace configure %s\n\n' "$quoted_amber_bin" "$quoted_workspace"
    printf 'Then check the workspace:\n'
    printf '  %s workspace doctor %s --external --service\n\n' "$quoted_amber_bin" "$quoted_workspace"
  fi

  if (( SERVICE_INSTALLED )); then
    printf 'Manage the user service:\n'
    printf '  %s service status --workspace %s\n' "$quoted_amber_bin" "$quoted_workspace"
    printf '  %s service stop --workspace %s\n' "$quoted_amber_bin" "$quoted_workspace"
    printf '  %s service start --workspace %s\n\n' "$quoted_amber_bin" "$quoted_workspace"
  else
    printf 'Run Amber in the foreground:\n'
    printf '  %s run --workspace %s\n\n' "$quoted_amber_bin" "$quoted_workspace"
    if (( WORKSPACE_CONFIGURED )); then
      printf 'Install the optional user service later:\n'
      printf '  %s service install --workspace %s --enable --now\n\n' "$quoted_amber_bin" "$quoted_workspace"
    fi
  fi

  printf 'Happy hacking.\n'
}

main() {
  parse_args "$@"
  heading "Amber"
  section "Check host"
  preflight_installer
  section "Install Amber"
  configure_release_asset_choice
  install_release
  if [[ -z "$WORKSPACE_NAME" ]]; then
    WORKSPACE_NAME="$(prompt "Workspace name")"
  fi
  section "Configure workspace"
  configure_workspace "$WORKSPACE_NAME"
  section "Start options"
  maybe_install_service "$WORKSPACE_NAME"
  print_next_steps "$WORKSPACE_NAME"
}

main "$@"
