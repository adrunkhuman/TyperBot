"""Tests for scripts/restore_db.py restore semantics."""

import contextlib
import sqlite3
import sys
from unittest.mock import patch

from scripts.restore_db import main, validate_backup_sql

VALID_SQL = """\
CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT);
INSERT INTO users (id, name) VALUES (1, 'Alice');
"""

INVALID_SQL = """\
DROP TABLE users;
"""


class TestValidateBackupSql:
    def test_accepts_create_and_insert(self):
        assert validate_backup_sql(VALID_SQL) is True

    def test_rejects_drop(self):
        assert validate_backup_sql(INVALID_SQL) is False

    def test_rejects_delete(self):
        assert validate_backup_sql("DELETE FROM users;") is False

    def test_rejects_bare_create_without_if_not_exists(self):
        assert validate_backup_sql("CREATE TABLE users (id INTEGER);") is False


class TestRestoreAtomic:
    def test_failed_restore_does_not_corrupt_original(self, tmp_path):
        """A restore that fails mid-way must leave the live DB untouched."""
        db_path = tmp_path / "typer.db"

        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE sentinel (val TEXT)")
        conn.execute("INSERT INTO sentinel VALUES ('original')")
        conn.commit()
        conn.close()

        bad_sql = "THIS IS NOT VALID SQL;"
        backup_file = tmp_path / "backup.sql"
        backup_file.write_text(bad_sql)

        with (
            patch("scripts.restore_db.DB_PATH", str(db_path)),
            patch("scripts.restore_db.validate_backup_sql", return_value=True),
            patch("builtins.input", return_value="YES"),
            patch("sys.exit"),
        ):
            sys.argv = ["restore_db", str(backup_file)]
            with contextlib.suppress(SystemExit):
                main()

        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT val FROM sentinel").fetchall()
        conn.close()
        assert rows == [("original",)]

        assert not (db_path.parent / "typer.db.restore_tmp").exists()

    def test_successful_restore_replaces_db(self, tmp_path):
        """A successful restore must produce a DB containing only the backup data."""
        db_path = tmp_path / "typer.db"

        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE old_table (x TEXT)")
        conn.commit()
        conn.close()

        backup_file = tmp_path / "backup.sql"
        backup_file.write_text(VALID_SQL)

        with (
            patch("scripts.restore_db.DB_PATH", str(db_path)),
            patch("builtins.input", return_value="YES"),
        ):
            sys.argv = ["restore_db", str(backup_file)]
            main()

        conn = sqlite3.connect(db_path)
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        users = conn.execute("SELECT name FROM users").fetchall()
        conn.close()

        assert "users" in tables
        assert "old_table" not in tables  # replaced, not merged
        assert users == [("Alice",)]
