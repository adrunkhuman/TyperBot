"""Deployment-sensitive configuration defaults.

The bot boots in smoke-test mode unless ``ENVIRONMENT`` is set to a production
value.

``DATA_DIR`` intentionally defaults to ``./data`` so local development writes
into a repo-adjacent folder without depending on Railway's volume mount.
Production deploys must override it to a persistent path such as ``/app/data``.
"""

import os

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
IS_PRODUCTION = ENVIRONMENT.lower() in ("production", "prod")

DATA_DIR = os.getenv("DATA_DIR", "./data")
DB_PATH = os.getenv("DB_PATH", f"{DATA_DIR}/typer.db")
BACKUP_DIR = os.getenv("BACKUP_DIR", f"{DATA_DIR}/backups")
