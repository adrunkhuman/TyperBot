#!/usr/bin/env python
"""Manual database restore script - run from Railway console."""

import argparse
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from typer_bot.utils.config import DB_PATH


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


def main():
    parser = argparse.ArgumentParser(
        prog="restore_db", description="Restore database from backup file"
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
        print("Only CREATE TABLE IF NOT EXISTS and INSERT statements are allowed")
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
        print(f"Current database backed up to {bak_path}")

    tmp_path = db_path.with_suffix(".db.restore_tmp")
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
