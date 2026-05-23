"""Logging utilities."""

import contextvars
import functools
import logging
import os
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, ParamSpec, TypeVar, override

_trace_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_id", default=None)

_log_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "log_context", default=None
)


P = ParamSpec("P")
T = TypeVar("T")
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"
SENSITIVE_LOG_KEYS = {"token", "password", "secret", "key", "api_key", "access_token"}
LOG_RECORD_ATTRS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "message",
    "module",
    "msecs",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "taskName",
    "thread",
    "threadName",
}


def set_trace_id(trace_id: str | None) -> None:
    """Set the trace ID for the current context.

    Args:
        trace_id: Unique identifier for the request/message. Format:
            - Interactions: "req-<interaction_id>"
            - Messages: "msg-<message_id>"
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
        event_type: Semantic event classification, e.g. "prediction.saved"
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


def log_context(
    **context_fields: Any,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Decorator that automatically manages log context for async functions.

    Sets context fields at function entry and restores previous context on exit.
    Useful for tracing user journeys across async boundaries.

    Example:
        @log_context(event_type="prediction.saved")
        async def save_prediction(...):
            logger.info("Prediction saved")  # Will include event_type field
    """

    def decorator(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        @functools.wraps(func)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            operation = getattr(func, "__qualname__", getattr(func, "__name__", "unknown"))
            with LogContextManager(**context_fields, operation=operation):
                return await func(*args, **kwargs)

        return async_wrapper

    return decorator


def _format_scalar_log_value(value: Any) -> str:
    text = str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
    if any(char.isspace() for char in text) or "=" in text:
        return f'"{text}"'
    return text


def _format_log_value(key: str, value: Any) -> str:
    if key.lower() in SENSITIVE_LOG_KEYS:
        return "[REDACTED]"
    if isinstance(value, dict):
        return (
            "{"
            + ",".join(
                f"{item_key}:{_format_log_value(str(item_key), item_value)}"
                for item_key, item_value in sorted(value.items(), key=lambda item: str(item[0]))
            )
            + "}"
        )
    if isinstance(value, list | tuple | set):
        return "[" + ",".join(_format_log_value(key, item) for item in value) + "]"
    return _format_scalar_log_value(value)


class PlainFormatter(logging.Formatter):
    @override
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        _ = datefmt
        return datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="seconds")

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        fields: dict[str, Any] = {}

        trace_id = get_trace_id()
        if trace_id:
            fields["trace_id"] = trace_id

        fields.update({key: value for key, value in get_log_context().items() if value is not None})
        fields.update(
            {
                key: value
                for key, value in record.__dict__.items()
                if key not in LOG_RECORD_ATTRS and value is not None
            }
        )

        if not fields:
            return line

        details = " ".join(
            f"{key}={_format_log_value(key, value)}" for key, value in sorted(fields.items())
        )
        return f"{line} {details}"


def setup_logging(level: int | None = None) -> None:
    """Configure root logger on stdout.

    Forces ALL output to stdout. Some hosts treat stderr as error-level logs
    regardless of content, which breaks log level filtering.
    """
    if level is None:
        level_str = os.getenv("LOG_LEVEL", "INFO").upper()
        level = getattr(logging, level_str, logging.INFO)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level)

    stream = sys.stdout
    handler = logging.StreamHandler(stream)
    handler.setLevel(level)
    handler.setFormatter(PlainFormatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))

    root_logger.addHandler(handler)

    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)

    logger = logging.getLogger(__name__)
    logger.info("Logging configured at level %s", logging.getLevelName(level))


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

    Logs completion at the specified level or failure as a warning. Duration is
    always recorded in milliseconds using high-resolution monotonic clock
    (time.perf_counter).

    Args:
        logger: Logger instance to use for output
        operation: Human-readable operation name (e.g., "db.predictions.save_prediction")
        event_type: Optional semantic event type for filtering (e.g., "transaction.commit")
        level: Log level for successful completion (default: DEBUG)
        **extra_fields: Additional context fields to include in log entry

    Attributes:
        duration_ms: Duration of the operation in milliseconds (available after exit)

    Example:
        with LogTimer(logger, "db.predictions.save_prediction"):
            await db.predictions.save_prediction(...)

        # With event_type and extra fields
        with LogTimer(
            logger,
            "db.predictions.save_prediction",
            event_type="prediction.saved",
            user_id=user_id,
            fixture_id=fixture_id
        ):
            await db.predictions.save_prediction(...)
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
