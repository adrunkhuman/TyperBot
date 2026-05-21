"""Tests for runtime logging formatter selection."""

import io
import json
import logging

import pytest

from typer_bot.utils import logger as logger_module


class TtyBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


class NonTtyBuffer(io.StringIO):
    def isatty(self) -> bool:
        return False


@pytest.fixture(autouse=True)
def restore_logging_state():
    root_logger = logging.getLogger()
    root_handlers = list(root_logger.handlers)
    root_level = root_logger.level
    discord_level = logging.getLogger("discord").level
    discord_http_level = logging.getLogger("discord.http").level

    yield

    root_logger.handlers.clear()
    root_logger.handlers.extend(root_handlers)
    root_logger.setLevel(root_level)
    logging.getLogger("discord").setLevel(discord_level)
    logging.getLogger("discord.http").setLevel(discord_http_level)


def _configure_and_emit(monkeypatch, output: io.StringIO, logger_name: str = "test.logger") -> str:
    monkeypatch.setattr(logger_module.sys, "stdout", output)

    logger_module.setup_logging(logging.INFO)
    logging.getLogger(logger_name).info("readable message")

    return output.getvalue().splitlines()[-1]


def test_production_environment_emits_json_logs(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    output = NonTtyBuffer()

    log_entry = json.loads(_configure_and_emit(monkeypatch, output, "test.production"))

    assert log_entry["level"] == "info"
    assert log_entry["logger"] == "test.production"
    assert log_entry["message"] == "readable message"


def test_non_interactive_non_production_emits_plain_logs(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    output = NonTtyBuffer()

    log_line = _configure_and_emit(monkeypatch, output, "test.plain")

    assert "\x1b[" not in log_line
    assert not log_line.startswith("{")
    assert "INFO" in log_line
    assert "test.plain" in log_line
    assert "readable message" in log_line


def test_interactive_non_production_emits_color_logs(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    output = TtyBuffer()

    log_line = _configure_and_emit(monkeypatch, output)

    assert "\x1b[" in log_line
    assert "readable message" in log_line


def test_log_format_override_can_force_plain(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("LOG_FORMAT", "plain")
    output = TtyBuffer()

    log_line = _configure_and_emit(monkeypatch, output)

    assert "\x1b[" not in log_line
    assert not log_line.startswith("{")
    assert "readable message" in log_line


def test_log_format_override_can_force_json(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("LOG_FORMAT", "json")
    output = TtyBuffer()

    log_entry = json.loads(_configure_and_emit(monkeypatch, output))

    assert log_entry["level"] == "info"
    assert log_entry["message"] == "readable message"
