from __future__ import annotations

import os
import re
import sys
import tomllib
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


SOURCE_ROOT = Path(__file__).resolve().parents[2]
SOURCE_CONFIG_DIR = Path(__file__).resolve().parent

DEFAULT_AMBER_HOME = Path.home() / ".amber"
WORKSPACE_ENV = "AMBER_WORKSPACE"
AMBER_HOME_ENV = "AMBER_HOME"

WORKSPACE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root_dir: Path
    release_dir: Path
    release_version: str
    resources_dir: Path
    workspace_dir: Path
    mode: Literal["casual", "work"]
    ai_api_key: str | None
    ai_provider: str
    ai_model: str
    telegram_api_id: str | None
    telegram_api_hash: str | None
    telegram_session_path: Path
    memories_dir: Path
    runtime_state_path: Path
    timezone_name: str
    enable_real_delays: bool
    disable_sleep_state: bool
    context_debounce_seconds: float
    context_idle_timeout_seconds: float | None
    context_idle_timeout_min_seconds: float
    context_idle_timeout_max_seconds: float
    context_competing_chat_timeout_seconds: float
    context_recent_message_budget: int
    context_max_compacted_facts: int
    context_initial_engagement_delay_min_seconds: float
    context_initial_engagement_delay_max_seconds: float
    context_conversation_window_before: int
    context_conversation_window_after: int
    attention_scorer: str
    attention_surface_threshold: float
    attention_urgent_threshold: float
    attention_memory_limit: int
    always_surface_telegram_ids: tuple[str, ...]
    ai_semantic_retry_budget: int
    ai_max_reply_chars: int
    ai_max_output_tokens: int
    ai_temperature: float
    ai_orchestration_prompt_path: Path
    ai_notification_policy_prompt_path: Path
    ai_interruption_prompt_path: Path
    outbound_max_chunk_chars: int
    action_transport_max_retries: int
    action_transport_retry_delay_seconds: float
    action_typing_baseline_wpm: float
    action_filler_pause_seconds: float
    action_inter_chunk_delay_min_seconds: float
    action_inter_chunk_delay_max_seconds: float
    action_inter_chunk_delay_length_threshold_chars: int
    action_inter_chunk_delay_chars_per_step: int
    action_inter_chunk_delay_step_seconds: float
    action_inter_chunk_delay_total_max_seconds: float
    codex_workdir: Path
    codex_app_server_url: str
    codex_app_server_port: int
    codex_podman_executable: str
    codex_podman_cgroup_manager: str | None
    codex_enforce_resource_limits: bool
    codex_container_name: str
    codex_app_server_command: str | None
    codex_github_auth_dir: Path
    codex_home_dir: Path
    codex_model: str
    codex_reasoning_effort: str
    codex_auto_update: bool
    codex_system_prompt_path: Path
    codex_rules_skill_path: Path
    linear_enabled: bool
    linear_api_key: str | None
    linear_api_url: str
    linear_poll_seconds: float = Field(gt=0)
    linear_due_window_days: int = Field(ge=1)
    linear_project_statuses: dict[str, tuple[str, ...]]
    linear_issue_statuses: dict[str, tuple[str, ...]]
    linear_issue_ready_to_start_statuses: tuple[str, ...]
    linear_issue_status_targets: dict[str, str]

    ai_system_casual_prompt_path: Path
    ai_system_work_prompt_path: Path
    ai_system_prompt_path: Path
    ai_action_contract_prompt_path: Path
    memory_prompt_path: Path
    log_dir: Path


ENV_OVERRIDES: dict[str, tuple[str, ...]] = {
    "AMBER_MODE": ("mode",),
    "AMBER_TIMEZONE": ("timezone",),
    "AMBER_AI_PROVIDER": ("ai", "provider"),
    "AMBER_AI_API_KEY": ("ai", "api_key"),
    "AMBER_AI_MODEL": ("ai", "model"),
    "AMBER_AI_SEMANTIC_RETRY_BUDGET": ("ai", "semantic_retry_budget"),
    "AMBER_AI_MAX_REPLY_CHARS": ("ai", "max_reply_chars"),
    "AMBER_AI_MAX_OUTPUT_TOKENS": ("ai", "max_output_tokens"),
    "AMBER_AI_TEMPERATURE": ("ai", "temperature"),
    "API_ID": ("telegram", "api_id"),
    "API_HASH": ("telegram", "api_hash"),
    "TELEGRAM_SESSION_PATH": ("telegram", "session_path"),
    "AMBER_ENABLE_REAL_DELAYS": ("runtime", "enable_real_delays"),
    "AMBER_DISABLE_SLEEP_STATE": ("runtime", "disable_sleep_state"),
    "AMBER_CONTEXT_DEBOUNCE_SECONDS": ("context", "debounce_seconds"),
    "AMBER_CONTEXT_IDLE_TIMEOUT_SECONDS": ("context", "idle_timeout_seconds"),
    "AMBER_CONTEXT_IDLE_TIMEOUT_MIN_SECONDS": ("context", "idle_timeout_min_seconds"),
    "AMBER_CONTEXT_IDLE_TIMEOUT_MAX_SECONDS": ("context", "idle_timeout_max_seconds"),
    "AMBER_CONTEXT_COMPETING_CHAT_TIMEOUT_SECONDS": ("context", "competing_chat_timeout_seconds"),
    "AMBER_CONTEXT_RECENT_MESSAGE_BUDGET": ("context", "recent_message_budget"),
    "AMBER_CONTEXT_MAX_COMPACTED_FACTS": ("context", "max_compacted_facts"),
    "AMBER_CONTEXT_INITIAL_ENGAGEMENT_DELAY_MIN_SECONDS": (
        "context",
        "initial_engagement_delay_min_seconds",
    ),
    "AMBER_CONTEXT_INITIAL_ENGAGEMENT_DELAY_MAX_SECONDS": (
        "context",
        "initial_engagement_delay_max_seconds",
    ),
    "AMBER_CONTEXT_CONVERSATION_WINDOW_BEFORE": ("context", "conversation_window_before"),
    "AMBER_CONTEXT_CONVERSATION_WINDOW_AFTER": ("context", "conversation_window_after"),
    "AMBER_ATTENTION_SCORER": ("attention", "scorer"),
    "AMBER_ATTENTION_SURFACE_THRESHOLD": ("attention", "surface_threshold"),
    "AMBER_ATTENTION_URGENT_THRESHOLD": ("attention", "urgent_threshold"),
    "AMBER_ATTENTION_MEMORY_LIMIT": ("attention", "memory_limit"),
    "AMBER_OUTBOUND_MAX_CHUNK_CHARS": ("outbound", "max_chunk_chars"),
    "AMBER_ACTION_MAX_RETRIES": ("action", "transport_max_retries"),
    "AMBER_ACTION_RETRY_DELAY_SECONDS": ("action", "transport_retry_delay_seconds"),
    "AMBER_ACTION_TYPING_BASELINE_WPM": ("action", "typing_baseline_wpm"),
    "AMBER_ACTION_FILLER_PAUSE_SECONDS": ("action", "filler_pause_seconds"),
    "AMBER_ACTION_INTER_CHUNK_DELAY_MIN_SECONDS": ("action", "inter_chunk_delay_min_seconds"),
    "AMBER_ACTION_INTER_CHUNK_DELAY_MAX_SECONDS": ("action", "inter_chunk_delay_max_seconds"),
    "AMBER_ACTION_INTER_CHUNK_DELAY_LENGTH_THRESHOLD_CHARS": (
        "action",
        "inter_chunk_delay_length_threshold_chars",
    ),
    "AMBER_ACTION_INTER_CHUNK_DELAY_CHARS_PER_STEP": (
        "action",
        "inter_chunk_delay_chars_per_step",
    ),
    "AMBER_ACTION_INTER_CHUNK_DELAY_STEP_SECONDS": ("action", "inter_chunk_delay_step_seconds"),
    "AMBER_ACTION_INTER_CHUNK_DELAY_TOTAL_MAX_SECONDS": (
        "action",
        "inter_chunk_delay_total_max_seconds",
    ),
    "AMBER_CODEX_WORKDIR": ("codex", "workdir"),
    "AMBER_CODEX_APP_SERVER_URL": ("codex", "app_server_url"),
    "AMBER_CODEX_APP_SERVER_PORT": ("codex", "app_server_port"),
    "AMBER_CODEX_PODMAN": ("codex", "podman_executable"),
    "AMBER_CODEX_CGROUP_MANAGER": ("codex", "podman_cgroup_manager"),
    "AMBER_CODEX_ENFORCE_RESOURCE_LIMITS": ("codex", "enforce_resource_limits"),
    "AMBER_CODEX_CONTAINER_NAME": ("codex", "container_name"),
    "AMBER_CODEX_APP_SERVER_COMMAND": ("codex", "app_server_command"),
    "AMBER_CODEX_GITHUB_AUTH_DIR": ("codex", "github_auth_dir"),
    "AMBER_CODEX_HOME_DIR": ("codex", "home_dir"),
    "AMBER_CODEX_MODEL": ("codex", "model"),
    "AMBER_CODEX_REASONING_EFFORT": ("codex", "reasoning_effort"),
    "AMBER_CODEX_AUTO_UPDATE": ("codex", "auto_update"),
    "AMBER_LINEAR_ENABLED": ("linear", "enabled"),
    "AMBER_LINEAR_API_KEY": ("linear", "api_key"),
    "AMBER_LINEAR_API_URL": ("linear", "api_url"),
    "AMBER_LINEAR_POLL_SECONDS": ("linear", "poll_seconds"),
    "AMBER_LINEAR_DUE_WINDOW_DAYS": ("linear", "due_window_days"),
    "AMBER_LINEAR_PROJECT_STATUS_PLANNED": ("linear", "project", "statuses", "planned"),
    "AMBER_LINEAR_PROJECT_STATUS_STARTED": ("linear", "project", "statuses", "started"),
    "AMBER_LINEAR_PROJECT_STATUS_COMPLETED": ("linear", "project", "statuses", "completed"),
    "AMBER_LINEAR_PROJECT_STATUS_CANCELED": ("linear", "project", "statuses", "canceled"),
    "AMBER_LINEAR_ISSUE_READY_TO_START_STATUSES": ("linear", "issue", "ready_to_start_statuses"),
    "AMBER_LINEAR_ISSUE_STATUS_BACKLOG": ("linear", "issue", "statuses", "backlog"),
    "AMBER_LINEAR_ISSUE_STATUS_UNSTARTED": ("linear", "issue", "statuses", "unstarted"),
    "AMBER_LINEAR_ISSUE_STATUS_STARTED": ("linear", "issue", "statuses", "started"),
    "AMBER_LINEAR_ISSUE_STATUS_COMPLETED": ("linear", "issue", "statuses", "completed"),
    "AMBER_LINEAR_ISSUE_STATUS_CANCELED": ("linear", "issue", "statuses", "canceled"),
    "AMBER_LINEAR_ISSUE_TARGET_IN_PROGRESS": ("linear", "issue", "status_targets", "in_progress"),
    "AMBER_LINEAR_ISSUE_TARGET_UNDER_REVIEW": ("linear", "issue", "status_targets", "under_review"),
    "AMBER_LINEAR_ISSUE_TARGET_COMPLETED": ("linear", "issue", "status_targets", "completed"),
}


def amber_home() -> Path:
    return Path(os.getenv(AMBER_HOME_ENV, str(DEFAULT_AMBER_HOME))).expanduser()


def release_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return SOURCE_ROOT


def release_version() -> str:
    version_path = release_dir() / "VERSION"
    try:
        version = version_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise RuntimeError(f"Amber VERSION file is missing: {version_path}") from exc
    if not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", version):
        raise RuntimeError(f"Amber VERSION is not valid SemVer: {version!r}")
    return version


def resources_dir() -> Path:
    packaged_resources = release_dir() / "resources"
    if packaged_resources.exists():
        return packaged_resources
    return SOURCE_CONFIG_DIR


def workspace_dir(workspace: str | Path | None = None) -> Path:
    raw = str(workspace or os.getenv(WORKSPACE_ENV) or "").strip()
    if not raw:
        return SOURCE_ROOT
    path = Path(raw).expanduser()
    if path.is_absolute() or "/" in raw or raw.startswith("."):
        return path.resolve()
    return (amber_home() / "workspaces" / raw).resolve()


def resource_prompt_dir() -> Path:
    prompt_dir = resources_dir() / "prompts"
    return prompt_dir if prompt_dir.exists() else resources_dir()


def resource_codex_skill_dir() -> Path:
    skill_dir = resources_dir() / "codex-skills"
    return skill_dir if skill_dir.exists() else resources_dir() / "skills"


def default_config_path() -> Path:
    return resources_dir() / "config.default.toml"


def workspace_config_path(workspace: str | Path | None = None) -> Path:
    return workspace_dir(workspace) / "config.toml"


@lru_cache(maxsize=16)
def _get_settings_cached(workspace_key: str | None, config_key: str | None) -> Settings:
    resolved_workspace = workspace_dir(workspace_key)
    config_path = Path(config_key).expanduser().resolve() if config_key else resolved_workspace / "config.toml"
    data = _load_config_data(resolved_workspace, config_path)

    mode = str(data.get("mode") or "work").strip().lower()
    if mode not in {"casual", "work"}:
        raise RuntimeError("mode must be either 'casual' or 'work'.")

    linear_project_statuses = _status_groups_value(_value(data, ("linear", "project", "statuses")))
    linear_issue_statuses = _status_groups_value(_value(data, ("linear", "issue", "statuses")))
    linear_ready_to_start_statuses = _string_list_value(
        _value_or_none(data, ("linear", "issue", "ready_to_start_statuses"))
    ) or linear_issue_statuses.get("unstarted", ())
    linear_issue_status_targets = _linear_issue_status_targets(data, linear_issue_statuses)

    prompt_dir = _workspace_or_resource_dir(resolved_workspace / "prompts", resource_prompt_dir())
    skill_dir = _workspace_or_resource_dir(resolved_workspace / "codex-skills", resource_codex_skill_dir())
    system_dir = resources_dir() / "system"

    legacy_idle = _value_or_none(data, ("context", "idle_timeout_seconds"))
    if legacy_idle is not None:
        idle_timeout_seconds = float(legacy_idle)
        idle_timeout_min = idle_timeout_seconds
        idle_timeout_max = idle_timeout_seconds
    else:
        idle_timeout_seconds = None
        idle_timeout_min = float(_value(data, ("context", "idle_timeout_min_seconds")))
        idle_timeout_max = float(_value(data, ("context", "idle_timeout_max_seconds")))

    casual_prompt_path = prompt_dir / "AI_SYSTEM_CASUAL.md"
    work_prompt_path = prompt_dir / "AI_SYSTEM_WORK.md"

    return Settings(
        root_dir=resolved_workspace,
        release_dir=release_dir(),
        release_version=release_version(),
        resources_dir=resources_dir(),
        workspace_dir=resolved_workspace,
        mode=mode,  # type: ignore[arg-type]
        ai_api_key=_optional_str(_value_or_none(data, ("ai", "api_key"))),
        ai_provider=str(_value(data, ("ai", "provider"))),
        ai_model=str(_value(data, ("ai", "model"))),
        telegram_api_id=_optional_str(_value_or_none(data, ("telegram", "api_id"))),
        telegram_api_hash=_optional_str(_value_or_none(data, ("telegram", "api_hash"))),
        telegram_session_path=_path_value(_value(data, ("telegram", "session_path")), resolved_workspace),
        memories_dir=_path_value(_value(data, ("paths", "memories_dir")), resolved_workspace),
        runtime_state_path=_path_value(_value(data, ("paths", "runtime_state_path")), resolved_workspace),
        timezone_name=str(data.get("timezone")),
        enable_real_delays=_bool_value(_value(data, ("runtime", "enable_real_delays"))),
        disable_sleep_state=_bool_value(_value(data, ("runtime", "disable_sleep_state"))),
        context_debounce_seconds=float(_value(data, ("context", "debounce_seconds"))),
        context_idle_timeout_seconds=idle_timeout_seconds,
        context_idle_timeout_min_seconds=idle_timeout_min,
        context_idle_timeout_max_seconds=idle_timeout_max,
        context_competing_chat_timeout_seconds=float(_value(data, ("context", "competing_chat_timeout_seconds"))),
        context_recent_message_budget=int(_value(data, ("context", "recent_message_budget"))),
        context_max_compacted_facts=int(_value(data, ("context", "max_compacted_facts"))),
        context_initial_engagement_delay_min_seconds=float(
            _value(data, ("context", "initial_engagement_delay_min_seconds"))
        ),
        context_initial_engagement_delay_max_seconds=float(
            _value(data, ("context", "initial_engagement_delay_max_seconds"))
        ),
        context_conversation_window_before=int(_value(data, ("context", "conversation_window_before"))),
        context_conversation_window_after=int(_value(data, ("context", "conversation_window_after"))),
        attention_scorer=str(_value(data, ("attention", "scorer"))),
        attention_surface_threshold=float(_value(data, ("attention", "surface_threshold"))),
        attention_urgent_threshold=float(_value(data, ("attention", "urgent_threshold"))),
        attention_memory_limit=int(_value(data, ("attention", "memory_limit"))),
        always_surface_telegram_ids=_list_value(_value(data, ("attention", "always_surface_telegram_ids"))),
        ai_semantic_retry_budget=int(_value(data, ("ai", "semantic_retry_budget"))),
        ai_max_reply_chars=int(_value(data, ("ai", "max_reply_chars"))),
        ai_max_output_tokens=int(_value(data, ("ai", "max_output_tokens"))),
        ai_temperature=float(_value(data, ("ai", "temperature"))),
        ai_orchestration_prompt_path=system_dir / "AI_ORCHESTRATION.md",
        ai_notification_policy_prompt_path=system_dir / "AI_NOTIFICATION_POLICY.md",
        ai_interruption_prompt_path=prompt_dir / "AI_INTERRUPTION.md",
        outbound_max_chunk_chars=int(_value(data, ("outbound", "max_chunk_chars"))),
        action_transport_max_retries=int(_value(data, ("action", "transport_max_retries"))),
        action_transport_retry_delay_seconds=float(_value(data, ("action", "transport_retry_delay_seconds"))),
        action_typing_baseline_wpm=float(_value(data, ("action", "typing_baseline_wpm"))),
        action_filler_pause_seconds=float(_value(data, ("action", "filler_pause_seconds"))),
        action_inter_chunk_delay_min_seconds=float(_value(data, ("action", "inter_chunk_delay_min_seconds"))),
        action_inter_chunk_delay_max_seconds=float(_value(data, ("action", "inter_chunk_delay_max_seconds"))),
        action_inter_chunk_delay_length_threshold_chars=int(
            _value(data, ("action", "inter_chunk_delay_length_threshold_chars"))
        ),
        action_inter_chunk_delay_chars_per_step=int(_value(data, ("action", "inter_chunk_delay_chars_per_step"))),
        action_inter_chunk_delay_step_seconds=float(_value(data, ("action", "inter_chunk_delay_step_seconds"))),
        action_inter_chunk_delay_total_max_seconds=float(
            _value(data, ("action", "inter_chunk_delay_total_max_seconds"))
        ),
        codex_workdir=_path_value(_value(data, ("codex", "workdir")), resolved_workspace),
        codex_app_server_url=str(_value(data, ("codex", "app_server_url"))),
        codex_app_server_port=int(_value(data, ("codex", "app_server_port"))),
        codex_podman_executable=str(_value(data, ("codex", "podman_executable"))),
        codex_podman_cgroup_manager=_optional_str(_value_or_none(data, ("codex", "podman_cgroup_manager"))),
        codex_enforce_resource_limits=_bool_value(_value(data, ("codex", "enforce_resource_limits"))),
        codex_container_name=str(_value(data, ("codex", "container_name"))),
        codex_app_server_command=_optional_str(_value_or_none(data, ("codex", "app_server_command"))),
        codex_github_auth_dir=_path_value(_value(data, ("codex", "github_auth_dir")), resolved_workspace),
        codex_home_dir=_path_value(_value(data, ("codex", "home_dir")), resolved_workspace),
        codex_model=str(_value(data, ("codex", "model"))),
        codex_reasoning_effort=str(_value(data, ("codex", "reasoning_effort"))),
        codex_auto_update=_bool_value(_value(data, ("codex", "auto_update"))),
        codex_system_prompt_path=system_dir / "CODEX_SYSTEM.md",
        codex_rules_skill_path=skill_dir / "CodexRules" / "SKILL.md",
        linear_enabled=_bool_value(_value(data, ("linear", "enabled"))),
        linear_api_key=_optional_str(_value_or_none(data, ("linear", "api_key"))),
        linear_api_url=str(_value(data, ("linear", "api_url"))),
        linear_poll_seconds=float(_value(data, ("linear", "poll_seconds"))),
        linear_due_window_days=int(_value(data, ("linear", "due_window_days"))),
        linear_project_statuses=linear_project_statuses,
        linear_issue_statuses=linear_issue_statuses,
        linear_issue_ready_to_start_statuses=linear_ready_to_start_statuses,
        linear_issue_status_targets=linear_issue_status_targets,
        ai_system_casual_prompt_path=casual_prompt_path,
        ai_system_work_prompt_path=work_prompt_path,
        ai_system_prompt_path=work_prompt_path if mode == "work" else casual_prompt_path,
        ai_action_contract_prompt_path=prompt_dir / "AI_ACTION_CONTRACT.md",
        memory_prompt_path=prompt_dir / "MEMORY.md",
        log_dir=_path_value(_value(data, ("paths", "log_dir")), resolved_workspace),
    )


def get_settings(workspace: str | Path | None = None, config_path: str | Path | None = None) -> Settings:
    workspace_key = str(workspace) if workspace is not None else None
    config_key = str(config_path) if config_path is not None else None
    return _get_settings_cached(workspace_key, config_key)


def _load_config_data(resolved_workspace: Path, config_path: Path) -> dict[str, Any]:
    defaults = _read_toml(default_config_path())
    workspace_config = _read_toml(config_path) if config_path.exists() else {}
    data = _deep_merge(defaults, workspace_config)
    _apply_env_overrides(data)
    data = _coerce_none(data)
    return _interpolate_workspace(data, resolved_workspace)


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"Amber config file is missing: {path}")
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    if not isinstance(data, dict):
        raise RuntimeError(f"Amber config file must contain a TOML table: {path}")
    return data


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _apply_env_overrides(data: dict[str, Any]) -> None:
    if os.getenv("OPENAI_API_KEY") and not os.getenv("AMBER_AI_API_KEY"):
        _set_path(data, ("ai", "api_key"), os.environ["OPENAI_API_KEY"])
    for fallback in ("MODEL_API_KEY", "OPENAI_KEY"):
        if os.getenv(fallback) and _is_missing(_value_or_none(data, ("ai", "api_key"))):
            _set_path(data, ("ai", "api_key"), os.environ[fallback])
    for env_name, path in ENV_OVERRIDES.items():
        if env_name in os.environ:
            value: Any = os.environ[env_name]
            if env_name == "AMBER_ALWAYS_SURFACE_TELEGRAM_IDS":
                value = _list_value(value)
            _set_path(data, path, value)
    if "AMBER_ALWAYS_SURFACE_TELEGRAM_IDS" in os.environ:
        _set_path(data, ("attention", "always_surface_telegram_ids"), _list_value(os.environ["AMBER_ALWAYS_SURFACE_TELEGRAM_IDS"]))


def _set_path(data: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    target = data
    for key in path[:-1]:
        child = target.get(key)
        if not isinstance(child, dict):
            child = {}
            target[key] = child
        target = child
    target[path[-1]] = value


def _value(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    value = _value_or_none(data, path)
    if value is None:
        joined = ".".join(path)
        raise RuntimeError(f"Amber config is missing required value: {joined}")
    return value


def _value_or_none(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip().lower() in {"", "none"})


def _coerce_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _coerce_none(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_coerce_none(item) for item in value]
    if isinstance(value, str) and value.strip().lower() == "none":
        return None
    return value


def _interpolate_workspace(value: Any, resolved_workspace: Path) -> Any:
    if isinstance(value, dict):
        return {key: _interpolate_workspace(item, resolved_workspace) for key, item in value.items()}
    if isinstance(value, list):
        return [_interpolate_workspace(item, resolved_workspace) for item in value]
    if isinstance(value, str):
        return value.replace("{workspace}", str(resolved_workspace))
    return value


def _path_value(value: Any, resolved_workspace: Path) -> Path:
    path = Path(str(value)).expanduser()
    if path.is_absolute():
        return path
    return resolved_workspace / path


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _list_value(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        raw_items = [str(item) for item in value]
    else:
        raw_items = str(value).replace("\n", ",").replace(";", ",").split(",")
    values = {
        item.strip().removeprefix("user")
        for item in raw_items
        if item.strip()
    }
    return tuple(sorted(values))


def _string_list_value(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        raw_items = [str(item) for item in value]
    else:
        raw_items = str(value).replace("\n", ",").replace(";", ",").split(",")
    values: list[str] = []
    seen: set[str] = set()
    for raw_item in raw_items:
        item = raw_item.strip()
        normalized = item.casefold()
        if item and normalized not in seen:
            values.append(item)
            seen.add(normalized)
    return tuple(values)


def _status_groups_value(value: Any) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, dict):
        raise RuntimeError("Linear status groups must be a TOML table.")
    return {
        str(group).strip(): _string_list_value(statuses)
        for group, statuses in value.items()
        if str(group).strip()
    }


def _linear_issue_status_targets(
    data: dict[str, Any],
    issue_statuses: dict[str, tuple[str, ...]],
) -> dict[str, str]:
    # derive lifecycle targets from ordered issue status groups
    targets = {
        "in_progress": _status_at(issue_statuses.get("started", ()), 0),
        "under_review": _status_at(issue_statuses.get("started", ()), 1)
        or _status_at(issue_statuses.get("started", ()), 0),
        "completed": _status_at(issue_statuses.get("completed", ()), 0),
    }

    # allow workspaces to pin lifecycle targets explicitly
    explicit_targets = _value_or_none(data, ("linear", "issue", "status_targets"))
    if isinstance(explicit_targets, dict):
        for alias, value in explicit_targets.items():
            target = _first_string_value(value)
            if target:
                targets[str(alias).strip()] = target

    # fail early when lifecycle automation lacks a concrete issue status
    missing = [alias for alias, target in targets.items() if not target]
    if missing:
        joined = ", ".join(sorted(missing))
        raise RuntimeError(f"Linear issue status target configuration is missing: {joined}")
    return {alias: target for alias, target in targets.items() if target}


def _status_at(statuses: tuple[str, ...], index: int) -> str | None:
    if index < len(statuses):
        return statuses[index]
    return None


def _first_string_value(value: Any) -> str | None:
    values = _string_list_value(value)
    return values[0] if values else None


def _workspace_or_resource_dir(workspace_path: Path, resource_path: Path) -> Path:
    return workspace_path if workspace_path.exists() else resource_path


get_settings.cache_clear = _get_settings_cached.cache_clear  # type: ignore[attr-defined]
