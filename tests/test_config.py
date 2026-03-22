"""Tests for environment-driven config defaults."""

import importlib

from typer_bot.utils import config as config_module


class TestConfigDefaults:
    def test_local_data_defaults_when_env_missing(self, monkeypatch):
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.delenv("DATA_DIR", raising=False)
        monkeypatch.delenv("DB_PATH", raising=False)
        monkeypatch.delenv("BACKUP_DIR", raising=False)

        reloaded = importlib.reload(config_module)

        assert reloaded.ENVIRONMENT == "development"
        assert reloaded.IS_PRODUCTION is False
        assert reloaded.DATA_DIR == "./data"
        assert reloaded.DB_PATH == "./data/typer.db"
        assert reloaded.BACKUP_DIR == "./data/backups"

    def test_explicit_env_overrides_defaults(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("DATA_DIR", "/app/data")
        monkeypatch.setenv("DB_PATH", "/app/data/custom.db")
        monkeypatch.setenv("BACKUP_DIR", "/app/data/custom-backups")

        reloaded = importlib.reload(config_module)

        assert reloaded.IS_PRODUCTION is True
        assert reloaded.DATA_DIR == "/app/data"
        assert reloaded.DB_PATH == "/app/data/custom.db"
        assert reloaded.BACKUP_DIR == "/app/data/custom-backups"

        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.delenv("DATA_DIR", raising=False)
        monkeypatch.delenv("DB_PATH", raising=False)
        monkeypatch.delenv("BACKUP_DIR", raising=False)
        importlib.reload(reloaded)
