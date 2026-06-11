from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


_RUN_LOG_PATH: Path | None = None
_RESET = "\033[0m"
_LEVEL_COLORS = {
    "DEBUG": "\033[36m",
    "INFO": "\033[32m",
    "WARNING": "\033[33m",
    "ERROR": "\033[31m",
    "CRITICAL": "\033[1;31m",
}
_SAFE_VALUE_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:/@+-")


class HumanReadableFormatter(logging.Formatter):
    def __init__(self, *, use_color: bool = False) -> None:
        super().__init__()
        self._use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        source = _record_text(record, "event") or record.name
        message = record.getMessage()
        context = getattr(record, "context", None)
        detail, context_text = _human_context(context)
        if detail is None and message != source:
            detail = message

        line = f"{self._level(record.levelname)} {source}"
        if detail:
            line = f"{line}: {detail}"
        if context_text:
            line = f"{line} | {context_text}"
        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        if record.stack_info:
            line = f"{line}\n{self.formatStack(record.stack_info)}"
        return line

    def _level(self, level_name: str) -> str:
        label = f"[{level_name}]"
        if not self._use_color:
            return label
        return f"{_LEVEL_COLORS.get(level_name, '')}{label}{_RESET}"


def configure_logging(
    level: int = logging.INFO,
    *,
    log_dir: Path | None = None,
    timezone_name: str = "UTC",
) -> Path | None:
    global _RUN_LOG_PATH
    root = logging.getLogger()
    root.setLevel(level)
    has_stream_handler = any(
        isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler)
        for handler in root.handlers
    )
    if not has_stream_handler and _log_to_stderr_enabled():
        handler = logging.StreamHandler()
        handler.setFormatter(HumanReadableFormatter(use_color=_log_color_enabled(handler.stream)))
        root.addHandler(handler)
    if log_dir is None:
        return _RUN_LOG_PATH
    if _RUN_LOG_PATH is None:
        _RUN_LOG_PATH = _new_run_log_path(log_dir, timezone_name)
        file_handler = logging.FileHandler(_RUN_LOG_PATH, encoding="utf-8")
        file_handler.setFormatter(HumanReadableFormatter(use_color=False))
        root.addHandler(file_handler)
    return _RUN_LOG_PATH


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)


def current_run_log_path() -> Path | None:
    return _RUN_LOG_PATH


def _log_to_stderr_enabled() -> bool:
    value = os.getenv("AMBER_LOG_TO_STDERR")
    if value is None:
        return True
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _log_color_enabled(stream: Any) -> bool:
    value = os.getenv("AMBER_LOG_COLOR")
    if value is not None:
        normalized = value.strip().lower()
        if normalized in {"0", "false", "no", "off", "never"}:
            return False
        if normalized in {"1", "true", "yes", "on", "always"}:
            return True
        if normalized == "auto":
            return bool(getattr(stream, "isatty", lambda: False)())
    return "NO_COLOR" not in os.environ


def _new_run_log_path(log_dir: Path, timezone_name: str) -> Path:
    try:
        tzinfo = ZoneInfo(timezone_name)
    except Exception:
        tzinfo = timezone.utc
    now = datetime.now(tz=tzinfo)
    day_dir = log_dir / f"{now.month}-{now.day}-{now.year}"
    day_dir.mkdir(parents=True, exist_ok=True)
    return day_dir / f"{now.hour:02d}-{now.minute:02d}-{now.second:02d}-{now.microsecond:06d}.log"


def _record_text(record: logging.LogRecord, name: str) -> str | None:
    value = getattr(record, name, None)
    if value is None:
        return None
    return str(value)


def _human_context(context: Any) -> tuple[str | None, str]:
    if not isinstance(context, Mapping):
        if context is None:
            return None, ""
        return None, f"context={_format_log_value(context)}"

    items = dict(context)
    detail = items.pop("message", None)
    detail_text = str(detail) if detail is not None else None
    context_text = " ".join(f"{key}={_format_log_value(value)}" for key, value in items.items())
    return detail_text, context_text


def _format_log_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        if value and all(character in _SAFE_VALUE_CHARS for character in value):
            return value
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False, default=str)


def logged_entrypoint(event_name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            logger = get_logger(func.__module__)
            logger.debug(
                f"{event_name}.start",
                extra={"event": event_name, "context": {"function": func.__qualname__}},
            )
            try:
                result = func(*args, **kwargs)
            except Exception:
                logger.exception(
                    f"{event_name}.error",
                    extra={"event": event_name, "context": {"function": func.__qualname__}},
                )
                raise
            logger.debug(
                f"{event_name}.success",
                extra={"event": event_name, "context": {"function": func.__qualname__}},
            )
            return result

        return wrapper

    return decorator
