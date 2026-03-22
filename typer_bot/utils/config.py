"""Deployment-sensitive configuration defaults.

The bot boots in smoke-test mode unless ``ENVIRONMENT`` is set to a production
value. Data paths default to a local ``./data`` tree so development does not
silently depend on Railway's ``/app/data`` volume layout.
"""

import os

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
IS_PRODUCTION = ENVIRONMENT.lower() in ("production", "prod")

DATA_DIR = os.getenv("DATA_DIR", "./data")
DB_PATH = os.getenv("DB_PATH", f"{DATA_DIR}/typer.db")
BACKUP_DIR = os.getenv("BACKUP_DIR", f"{DATA_DIR}/backups")
