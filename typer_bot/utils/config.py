"""Configuration constants and environment variables."""

import os

# Environment configuration - supports any platform (Railway, Coolify, local, etc.)
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
IS_PRODUCTION = ENVIRONMENT.lower() in ("production", "prod")

DATA_DIR = os.getenv("DATA_DIR", "./data")
DB_PATH = os.getenv("DB_PATH", f"{DATA_DIR}/typer.db")
BACKUP_DIR = os.getenv("BACKUP_DIR", f"{DATA_DIR}/backups")
