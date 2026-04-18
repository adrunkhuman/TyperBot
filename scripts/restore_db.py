#!/usr/bin/env python
"""Restore a SQL backup into the live SQLite database.

The script validates the backup, restores it into a temporary SQLite file, and
atomically replaces the live DB only after the restore succeeds. If a live DB is
already present, it is copied to a timestamped backup file first.

Use this for recovery from SQL dump backups. Routine host migration should copy
the live SQLite database file directly instead of round-tripping through SQL.
"""

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from typer_bot.utils.backup_sql import validate_backup_sql
from typer_bot.utils.config import DB_PATH


def main():
    """Restore one backup file after explicit operator confirmation."""
    parser = argparse.ArgumentParser(
        prog="restore_db",
        description="Restore database from SQL dump backup for recovery, not routine migration",
    )
    parser.add_argument("backup_file", help="Path to backup SQL file")
    args = parser.parse_args()

    backup_path = Path(args.backup_file)
    if not backup_path.exists():
        print(f"Error: Backup file not found: {backup_path}")
        sys.exit(1)

    sql_content = backup_path.read_text(encoding="utf-8")
    if not validate_backup_sql(sql_content):
        print("Error: Backup file contains unsafe SQL")
        print("Only SQLite dump statements emitted by the bot backup flow are allowed")
        sys.exit(1)

    print("\nWarning: This will REPLACE the current database!")
    print(f"Backup file: {backup_path}")
    confirm = input("Type 'YES' to confirm: ")

    if confirm != "YES":
        print("Restore cancelled")
        sys.exit(0)

    db_path = Path(DB_PATH)

    if db_path.exists():
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        bak_path = db_path.with_suffix(f".db.bak.{timestamp}")
        shutil.copy(db_path, bak_path)
        with bak_path.open("r+b") as f:
            os.fsync(f.fileno())
        print(f"Current database backed up to {bak_path}")

    tmp_path = db_path.with_suffix(".db.restore_tmp")
    tmp_path.unlink(missing_ok=True)  # clear any remnant from a prior crashed run
    conn = None
    try:
        conn = sqlite3.connect(tmp_path)
        conn.executescript(sql_content)
        conn.close()
        conn = None
        tmp_path.replace(db_path)  # atomic on POSIX and Windows
        print(f"Database restored from {backup_path}")
    except Exception as e:
        if conn is not None:
            conn.close()
        tmp_path.unlink(missing_ok=True)
        print(f"Restore failed: {e}")
        print("Original database was not modified.")
        sys.exit(1)


if __name__ == "__main__":
    main()
