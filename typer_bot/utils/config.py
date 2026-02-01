"""Configuration constants and environment variables."""

import os

DATA_DIR = os.getenv("DATA_DIR", "/app/data")
DB_PATH = os.getenv("DB_PATH", f"{DATA_DIR}/typer.db")
BACKUP_DIR = os.getenv("BACKUP_DIR", f"{DATA_DIR}/backups")
