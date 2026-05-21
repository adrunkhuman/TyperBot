"""Tests for runtime logging formatter selection."""

import io
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


def test_production_environment_uses_json_formatter(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("LOG_FORMAT", raising=False)

    formatter = logger_module._select_formatter(NonTtyBuffer())

    assert isinstance(formatter, logger_module.ProductionJSONFormatter)


def test_non_interactive_non_production_uses_plain_formatter(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.delenv("LOG_FORMAT", raising=False)

    formatter = logger_module._select_formatter(NonTtyBuffer())

    assert isinstance(formatter, logger_module.PlainFormatter)


def test_interactive_non_production_uses_color_formatter(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("LOG_FORMAT", raising=False)

    formatter = logger_module._select_formatter(TtyBuffer())

    assert isinstance(formatter, logger_module.LocalFormatter)


def test_plain_formatter_does_not_emit_ansi_sequences(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    output = NonTtyBuffer()
    monkeypatch.setattr(logger_module.sys, "stdout", output)

    logger_module.setup_logging(logging.INFO)
    logging.getLogger("test.plain").info("readable message")

    assert "\x1b[" not in output.getvalue()
    assert "[INFO    ] test.plain: readable message" in output.getvalue()


def test_log_format_override_can_force_json(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("LOG_FORMAT", "json")

    formatter = logger_module._select_formatter(TtyBuffer())

    assert isinstance(formatter, logger_module.ProductionJSONFormatter)
