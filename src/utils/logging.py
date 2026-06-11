from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


_RUN_LOG_PATH: Path | None = None


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "event"):
            payload["event"] = getattr(record, "event")
        if hasattr(record, "context"):
            payload["context"] = getattr(record, "context")
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


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
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
    if log_dir is None:
        return _RUN_LOG_PATH
    if _RUN_LOG_PATH is None:
        _RUN_LOG_PATH = _new_run_log_path(log_dir, timezone_name)
        file_handler = logging.FileHandler(_RUN_LOG_PATH, encoding="utf-8")
        file_handler.setFormatter(JsonFormatter())
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


def _new_run_log_path(log_dir: Path, timezone_name: str) -> Path:
    try:
        tzinfo = ZoneInfo(timezone_name)
    except Exception:
        tzinfo = timezone.utc
    now = datetime.now(tz=tzinfo)
    day_dir = log_dir / f"{now.month}-{now.day}-{now.year}"
    day_dir.mkdir(parents=True, exist_ok=True)
    return day_dir / f"{now.hour:02d}-{now.minute:02d}-{now.second:02d}-{now.microsecond:06d}.log"


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
