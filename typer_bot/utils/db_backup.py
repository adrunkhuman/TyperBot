"""Automatic database backup utilities."""

import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def validate_backup_sql(sql_content: str) -> bool:
    """Validate backup SQL - allows CREATE TABLE IF NOT EXISTS and INSERT only."""
    normalized = re.sub(r"--.*?$", "", sql_content, flags=re.MULTILINE)
    normalized = re.sub(r"/\*.*?\*/", "", normalized, flags=re.DOTALL)
    normalized = re.sub(r"^\s*$", "", normalized, flags=re.MULTILINE)
    normalized = normalized.upper()

    dangerous = [
        "DROP",
        "DELETE",
        "UPDATE",
        "ALTER",
        "TRUNCATE",
        "REPLACE",
        "ATTACH",
        "DETACH",
        "PRAGMA",
    ]

    for keyword in dangerous:
        if re.search(rf"\b{keyword}\b", normalized):
            return False

    lines = normalized.split("\n")
    for line in lines:
        line = line.strip()
        if line.startswith("CREATE") and ("TABLE" not in line or "IF NOT EXISTS" not in line):
            return False

    return True


def create_backup(db_path: str, backup_dir: str) -> str:
    """
    Create timestamped SQL backup of database.

    Args:
        db_path: Path to current database file
        backup_dir: Directory to store backups

    Returns:
        Path to created backup file
    """
    db_path = Path(db_path)
    backup_path = Path(backup_dir)
    backup_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")[:-3]
    backup_file = backup_path / f"backup_{timestamp}.sql"

    conn = sqlite3.connect(db_path)
    with open(backup_file, "w", encoding="utf-8") as f:
        for line in conn.iterdump():
            f.write(f"{line}\n")
    conn.close()

    logger.info(f"Database backed up to {backup_file}")
    return str(backup_file)


def cleanup_old_backups(backup_dir: str, keep: int = 10) -> int:
    """
    Remove oldest backups, keeping only most recent N.

    Args:
        backup_dir: Directory containing backup files
        keep: Number of recent backups to retain

    Returns:
        Number of backups deleted
    """
    backup_path = Path(backup_dir)
    if not backup_path.exists():
        return 0

    backups = sorted(backup_path.glob("backup_*.sql"), key=lambda f: f.stat().st_mtime)

    deleted = 0
    while len(backups) > keep:
        old_backup = backups.pop(0)
        old_backup.unlink()
        logger.info(f"Deleted old backup: {old_backup}")
        deleted += 1

    return deleted
