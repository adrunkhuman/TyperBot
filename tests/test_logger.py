"""Tests for runtime logging setup."""

import io
import logging

import pytest

from typer_bot.utils import logger as logger_module


@pytest.fixture(autouse=True)
def restore_logging_state():
    root_logger = logging.getLogger()
    root_handlers = list(root_logger.handlers)
    root_level = root_logger.level
    discord_level = logging.getLogger("discord").level
    discord_http_level = logging.getLogger("discord.http").level
    trace_id = logger_module.get_trace_id()
    log_context = logger_module.get_log_context()

    yield

    root_logger.handlers.clear()
    root_logger.handlers.extend(root_handlers)
    root_logger.setLevel(root_level)
    logging.getLogger("discord").setLevel(discord_level)
    logging.getLogger("discord.http").setLevel(discord_http_level)
    logger_module.set_trace_id(trace_id)
    logger_module.clear_log_context()
    logger_module.set_log_context(**log_context)


def _configure_and_emit(monkeypatch, output: io.StringIO, logger_name: str = "test.logger") -> str:
    monkeypatch.setattr(logger_module.sys, "stdout", output)

    logger_module.setup_logging(logging.INFO)
    logging.getLogger(logger_name).info("readable message")

    return output.getvalue().splitlines()[-1]


def test_setup_logging_emits_plain_logs(monkeypatch):
    output = io.StringIO()

    log_line = _configure_and_emit(monkeypatch, output, "test.plain")

    assert "\x1b[" not in log_line
    assert not log_line.startswith("{")
    assert log_line.startswith("20")
    assert "INFO" in log_line
    assert "test.plain" in log_line
    assert "readable message" in log_line


def test_setup_logging_includes_context_and_extra_fields(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(logger_module.sys, "stdout", output)

    logger_module.set_trace_id("req-1")
    logger_module.set_log_context(guild_id="guild-1")
    logger_module.setup_logging(logging.INFO)
    logging.getLogger("test.context").info(
        "context message",
        extra={
            "event_type": "prediction.saved",
            "error_detail": "Fixture not found",
            "payload": {2: "second", "token": "secret-value", "safe": "visible"},
            "token": "secret-value",
        },
    )

    log_line = output.getvalue().splitlines()[-1]
    assert "context message" in log_line
    assert "secret-value" not in log_line
    assert 'error_detail="Fixture not found"' in log_line
    assert "event_type=prediction.saved" in log_line
    assert "guild_id=guild-1" in log_line
    assert "payload={2:second,safe:visible,token:[REDACTED]}" in log_line
    assert "token=[REDACTED]" in log_line
    assert "trace_id=req-1" in log_line


def test_setup_logging_uses_stdout(monkeypatch):
    output = io.StringIO()

    _configure_and_emit(monkeypatch, output)

    assert "readable message" in output.getvalue()


def test_setup_logging_respects_level(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(logger_module.sys, "stdout", output)

    logger_module.setup_logging(logging.WARNING)
    logging.getLogger("test.level").info("hidden message")
    logging.getLogger("test.level").warning("visible message")

    logged = output.getvalue()
    assert "hidden message" not in logged
    assert "WARNING" in logged
    assert "visible message" in logged


def test_setup_logging_uses_log_level_env(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(logger_module.sys, "stdout", output)
    monkeypatch.setenv("LOG_LEVEL", "WARNING")

    logger_module.setup_logging()
    logging.getLogger("test.env_level").info("hidden message")
    logging.getLogger("test.env_level").warning("visible message")

    logged = output.getvalue()
    assert "hidden message" not in logged
    assert "WARNING" in logged
    assert "visible message" in logged
