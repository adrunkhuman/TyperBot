"""Deployment-sensitive configuration defaults.

``ENVIRONMENT`` describes whether the bot is running in production or in some
other environment. It does not control whether the bot connects to Discord.

``DATA_DIR`` intentionally defaults to ``./data`` so local development writes
into a repo-adjacent folder without depending on container volume mounts.
Production deploys must override it to a persistent path such as ``/app/data``.
"""

import os

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
IS_PRODUCTION = ENVIRONMENT.lower() in ("production", "prod")

DATA_DIR = os.getenv("DATA_DIR", "./data")
DB_PATH = os.getenv("DB_PATH", f"{DATA_DIR}/typer.db")
BACKUP_DIR = os.getenv("BACKUP_DIR", f"{DATA_DIR}/backups")
