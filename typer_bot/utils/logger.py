"""Logging utilities for Railway-compatible structured logging.

This module provides structured logging capabilities for tracing user journeys,
timing operations, and debugging production issues.

Event Type Naming Convention:
    Event types use dot notation with the format: <entity>.<action> or <entity>.<action>.<detail>

    Examples:
        - prediction.saved: User prediction was saved successfully
        - prediction.updated: Existing prediction was modified
        - prediction.save_failed: Error saving prediction
        - prediction.parse_failed: User input couldn't be parsed
        - prediction.duplicate_blocked: Race condition prevented duplicate
        - fixture.created: New fixture created by admin
        - results.entered: Admin entered actual scores
        - session.fixture.started: Fixture creation DM flow began
        - session.fixture.completed: Fixture creation flow ended
        - transaction.begin/commit/rollback: Database transaction boundaries

    Severity Guidelines:
        - DEBUG: Session lifecycle, DB timing metrics, transaction boundaries
        - INFO: Business events (saved, created, entered)
        - WARNING: Validation failures, parsing errors, duplicate blocks
        - ERROR: Unhandled exceptions, system errors
"""

import contextvars
import functools
import json
import logging
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, ParamSpec, TypeVar

# Global context for request trace IDs
_trace_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_id", default=None)

# Global context for log context fields (user_id, fixture_id, etc.)
_log_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "log_context", default=None
)


P = ParamSpec("P")
T = TypeVar("T")


def set_trace_id(trace_id: str | None) -> None:
    """Set the trace ID for the current context.

    Args:
        trace_id: Unique identifier for the request/message. Format:
            - Interactions: "req-<interaction_id>"
            - Messages: "msg-<message_id>"
            - Edits: "edit-<message_id>"
            - Deletions: "del-<message_id>"
            Pass None to clear the trace ID.
    """
    _trace_id_ctx.set(trace_id)


def get_trace_id() -> str | None:
    """Get the current trace ID from context.

    Returns:
        Trace ID string (e.g., "req-123456") or None if not set.
    """
    return _trace_id_ctx.get()


def clear_trace_id() -> None:
    """Remove the trace ID from the current context.

    Should be called in finally blocks to prevent ID leakage between requests.
    """
    _trace_id_ctx.set(None)


def set_log_context(**kwargs: Any) -> None:
    """Set context fields for logging (user_id, fixture_id, etc.).

    Merge Behavior:
        New fields are merged with existing context. Existing fields are
        overwritten if keys match. Always creates a copy to avoid mutation
        of shared state.

    Common Fields:
        user_id: Discord user ID (string)
        fixture_id: Database fixture ID (int)
        guild_id: Discord guild/server ID (string)
        source: Where prediction came from ('dm', 'thread', 'command')
        event_type: Semantic event classification (see module docstring)
        operation: Function/method name being executed
    """
    current = _log_context.get()
    current = {} if current is None else current.copy()
    current.update(kwargs)
    _log_context.set(current)


def get_log_context() -> dict[str, Any]:
    """Get a copy of the current log context fields.

    Returns:
        Dict with context fields (user_id, fixture_id, source, etc.) or empty dict.
    """
    current = _log_context.get()
    return current.copy() if current is not None else {}


def clear_log_context() -> None:
    """Remove all fields from the current log context.

    Should be called in finally blocks to prevent context leakage between requests.
    """
    _log_context.set({})


class LogContextManager:
    """Context manager for temporary log context.

    Example:
        with LogContextManager(user_id="123", fixture_id=42):
            logger.info("Processing prediction")  # Will include user_id and fixture_id
    """

    def __init__(self, **context_fields: Any):
        self.context_fields = context_fields
        self.old_context: dict[str, Any] = {}

    def __enter__(self) -> "LogContextManager":
        self.old_context = get_log_context()
        set_log_context(**self.context_fields)
        return self

    def __exit__(self, exc_type: type | None, exc_val: Exception | None, exc_tb: Any) -> None:
        _log_context.set(self.old_context)


def log_context(**context_fields: Any) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Decorator that automatically manages log context for async functions.

    Sets context fields at function entry and restores previous context on exit.
    Useful for tracing user journeys across async boundaries.

    Example:
        @log_context(event_type="prediction.saved")
        async def save_prediction(...):
            logger.info("Prediction saved")  # Will include event_type field
    """

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @functools.wraps(func)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            operation = getattr(func, "__qualname__", getattr(func, "__name__", "unknown"))
            with LogContextManager(**context_fields, operation=operation):
                return await func(*args, **kwargs)  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            operation = getattr(func, "__qualname__", getattr(func, "__name__", "unknown"))
            with LogContextManager(**context_fields, operation=operation):
                return func(*args, **kwargs)

        # Return appropriate wrapper based on whether function is async
        import inspect

        if inspect.iscoroutinefunction(func):
            return async_wrapper  # type: ignore[return-value]
        return sync_wrapper

    return decorator


class RailwayJSONFormatter(logging.Formatter):
    """Railway-compatible JSON formatter for structured logging.

    Outputs single-line JSON that Railway can parse and filter.
    Example: {"level": "info", "message": "...", "timestamp": "...", "logger": "...", "event_type": "..."}
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
        "event_type",
        "user_id",
        "fixture_id",
        "source",
        "operation",
        "duration_ms",
        "rows_affected",
        "session_id",
        "step",
    }

    SENSITIVE_KEYS = {"token", "password", "secret", "key", "api_key", "access_token"}

    def _sanitize(self, obj: Any) -> Any:
        """Recursively sanitize sensitive data from log entries.

        Scans dict keys for sensitive patterns (tokens, passwords, secrets)
        and replaces their values with "[REDACTED]". Recursively processes
        nested dicts and lists.

        Args:
            obj: Object to sanitize (dict, list, or primitive)

        Returns:
            Sanitized object with sensitive values redacted
        """
        if isinstance(obj, dict):
            return {
                k: "[REDACTED]" if str(k).lower() in self.SENSITIVE_KEYS else self._sanitize(v)
                for k, v in obj.items()
            }
        if isinstance(obj, list | tuple):
            return type(obj)(self._sanitize(item) for item in obj)
        return obj

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as Railway-compatible JSON.

        Railway requires ISO8601 timestamps with timezone for proper log
        ordering. All output goes to stdout - Railway treats stderr as
        error-level logs regardless of content.

        Args:
            record: Standard library LogRecord to format

        Returns:
            Single-line JSON string for Railway log ingestion
        """
        # Railway requires ISO8601 with timezone for proper log ordering
        timestamp = datetime.fromtimestamp(record.created, tz=UTC).isoformat()

        log_entry = {
            "level": record.levelname.lower(),
            "message": record.getMessage(),
            "timestamp": timestamp,
            "logger": record.name,
        }

        # Add trace ID if available
        trace_id = get_trace_id()
        if trace_id:
            log_entry["trace_id"] = trace_id

        # Add event_type from record if set
        if hasattr(record, "event_type") and record.event_type:
            log_entry["event_type"] = record.event_type

        # Add exception info if available
        if record.exc_info:
            log_entry["error"] = self.formatException(record.exc_info)

        # Add context from contextvars (user_id, fixture_id, source, etc.)
        context = get_log_context()
        for key, value in context.items():
            if value is not None and key not in log_entry:
                log_entry[key] = value

        # Add any extra fields from the record
        for key, value in record.__dict__.items():
            if key not in self.STANDARD_RECORD_ATTRS and key not in log_entry:
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


def log_event(
    logger: logging.Logger,
    event_type: str,
    message: str,
    level: int = logging.INFO,
    **extra_fields: Any,
) -> None:
    """Log a business event with structured fields.

    Args:
        logger: The logger instance to use
        event_type: Semantic event type (e.g., 'prediction.saved', 'fixture.created')
        message: Human-readable message
        level: Log level (default: INFO)
        **extra_fields: Additional fields to include in the log entry
    """
    extra = {"event_type": event_type, **extra_fields}
    logger.log(level, message, extra=extra)


class LogTimer:
    """Context manager for timing operations and logging with duration.

    Automatically logs operation start (DEBUG), completion (specified level),
    or failure (WARNING). Duration is always recorded in milliseconds using
    high-resolution monotonic clock (time.perf_counter).

    Args:
        logger: Logger instance to use for output
        operation: Human-readable operation name (e.g., "db.save_prediction")
        event_type: Optional semantic event type for filtering (e.g., "transaction.commit")
        level: Log level for successful completion (default: DEBUG)
        **extra_fields: Additional context fields to include in log entry

    Attributes:
        duration_ms: Duration of the operation in milliseconds (available after exit)

    Example:
        with LogTimer(logger, "database.save_prediction"):
            await db.save_prediction(...)

        # With event_type and extra fields
        with LogTimer(
            logger,
            "db.save_prediction",
            event_type="prediction.saved",
            user_id=user_id,
            fixture_id=fixture_id
        ):
            await db.save_prediction(...)
    """

    def __init__(
        self,
        logger: logging.Logger,
        operation: str,
        event_type: str | None = None,
        level: int = logging.DEBUG,
        **extra_fields: Any,
    ):
        self.logger = logger
        self.operation = operation
        self.event_type = event_type
        self.level = level
        self.extra_fields = extra_fields
        self.start_time: float | None = None
        self.duration_ms: float | None = None

    def __enter__(self) -> "LogTimer":
        self.start_time = time.perf_counter()
        return self

    def __exit__(self, exc_type: type | None, exc_val: Exception | None, exc_tb: Any) -> None:
        end_time = time.perf_counter()
        if self.start_time is not None:
            self.duration_ms = (end_time - self.start_time) * 1000
        else:
            self.duration_ms = 0.0

        extra = {
            "operation": self.operation,
            "duration_ms": round(self.duration_ms, 2),
            **self.extra_fields,
        }

        if self.event_type:
            extra["event_type"] = self.event_type

        if exc_type is not None:
            extra["error_type"] = exc_type.__name__
            extra["success"] = False
            self.logger.warning(
                f"{self.operation} failed after {self.duration_ms:.2f}ms: {exc_val}",
                extra=extra,
            )
        else:
            extra["success"] = True
            self.logger.log(
                self.level, f"{self.operation} completed in {self.duration_ms:.2f}ms", extra=extra
            )
