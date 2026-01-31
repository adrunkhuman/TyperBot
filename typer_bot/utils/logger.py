"""Logging utilities for Railway-compatible structured logging."""

import contextvars
import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

# Global context for request trace IDs
_trace_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_id", default=None)


def set_trace_id(trace_id: str) -> None:
    """Set the trace ID for the current context."""
    _trace_id_ctx.set(trace_id)


def get_trace_id() -> str | None:
    """Retrieve the trace ID for the current context."""
    return _trace_id_ctx.get()


class RailwayJSONFormatter(logging.Formatter):
    """Railway-compatible JSON formatter for structured logging.

    Outputs single-line JSON that Railway can parse and filter.
    Example: {"level": "info", "message": "...", "timestamp": "...", "logger": "..."}
    """

    STANDARD_RECORD_ATTRS = {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "asctime",
        "timestamp",
        "logger",
        "level",
        "error",
        "trace_id",
    }

    SENSITIVE_KEYS = {"token", "password", "secret", "key", "api_key", "access_token"}

    def _sanitize(self, obj: Any) -> Any:
        if isinstance(obj, dict):
            return {
                k: "[REDACTED]" if str(k).lower() in self.SENSITIVE_KEYS else self._sanitize(v)
                for k, v in obj.items()
            }
        elif isinstance(obj, (list, tuple)):
            return type(obj)(self._sanitize(item) for item in obj)
        return obj

    def format(self, record: logging.LogRecord) -> str:
        # Railway expects ISO8601 with timezone for proper log ordering
        timestamp = datetime.fromtimestamp(record.created, tz=UTC).isoformat()

        log_entry = {
            "level": record.levelname.lower(),
            "message": record.getMessage(),
            "timestamp": timestamp,
            "logger": record.name,
        }

        trace_id = get_trace_id()
        if trace_id:
            log_entry["trace_id"] = trace_id

        if record.exc_info:
            log_entry["error"] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if key not in self.STANDARD_RECORD_ATTRS:
                log_entry[key] = value

        log_entry = self._sanitize(log_entry)

        return json.dumps(log_entry, ensure_ascii=False, separators=(",", ":"))


class LocalFormatter(logging.Formatter):
    """Colorful formatter for local development."""

    GREY = "\x1b[38;20m"
    BLUE = "\x1b[34;20m"
    CYAN = "\x1b[36;20m"
    YELLOW = "\x1b[33;20m"
    RED = "\x1b[31;20m"
    BOLD_RED = "\x1b[31;1m"
    RESET = "\x1b[0m"

    # [TIME] [LEVEL   ] logger.name: Message
    FMT = "[%(asctime)s] [%(levelname)-8s] %(name)s: %(message)s"
    DATEFMT = "%H:%M:%S"

    FORMATS = {
        logging.DEBUG: GREY + FMT + RESET,
        logging.INFO: BLUE + FMT + RESET,
        logging.WARNING: YELLOW + FMT + RESET,
        logging.ERROR: RED + FMT + RESET,
        logging.CRITICAL: BOLD_RED + FMT + RESET,
    }

    def format(self, record: logging.LogRecord) -> str:
        log_fmt = self.FORMATS.get(record.levelno, self.FMT)
        formatter = logging.Formatter(log_fmt, datefmt=self.DATEFMT)
        return formatter.format(record)


def is_railway_environment() -> bool:
    """Detect if running in Railway production environment."""
    return (
        os.getenv("RAILWAY_ENVIRONMENT") is not None
        or os.getenv("RAILWAY_SERVICE_NAME") is not None
    )


def setup_logging(level: int | None = None) -> None:
    """Configure root logger for Railway or local environment.

    Forces ALL output to stdout. Railway treats stderr as error-level logs
    regardless of content, which breaks log level filtering.
    """
    if level is None:
        level_str = os.getenv("LOG_LEVEL", "INFO").upper()
        level = getattr(logging, level_str, logging.INFO)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)

    if is_railway_environment():
        handler.setFormatter(RailwayJSONFormatter())
    else:
        handler.setFormatter(LocalFormatter())

    root_logger.addHandler(handler)

    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)

    logger = logging.getLogger(__name__)
    env_type = "Railway" if is_railway_environment() else "local"
    logger.info(
        f"Logging configured for {env_type} environment at level {logging.getLevelName(level)}"
    )
