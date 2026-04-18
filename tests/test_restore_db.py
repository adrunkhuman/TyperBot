"""Tests for scripts/restore_db.py restore semantics."""

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.restore_db import main, validate_backup_sql
from typer_bot.utils.db_backup import create_backup

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
        assert validate_backup_sql("DROP TABLE users;") is False

    def test_accepts_real_sqlite_dump_output(self, tmp_path):
        db_path = tmp_path / "source.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY AUTOINCREMENT, note TEXT)")
        conn.execute("CREATE UNIQUE INDEX idx_sample_note ON sample(note)")
        conn.execute("INSERT INTO sample (note) VALUES ('contains DROP text')")
        conn.commit()
        conn.close()

        backup_file = create_backup(str(db_path), str(tmp_path / "backups"))
        sql_content = Path(backup_file).read_text(encoding="utf-8")

        assert validate_backup_sql(sql_content) is True

    def test_accepts_keywords_inside_string_literals(self):
        sql_content = (
            "BEGIN TRANSACTION;\n"
            "CREATE TABLE sample (id INTEGER PRIMARY KEY AUTOINCREMENT, note TEXT);\n"
            "INSERT INTO sample VALUES(1, 'DELETE DROP UPDATE ALTER');\n"
            'DELETE FROM "sqlite_sequence";\n'
            "INSERT INTO sqlite_sequence VALUES('sample',1);\n"
            "COMMIT;"
        )

        assert validate_backup_sql(sql_content) is True

    def test_accepts_comment_tokens_inside_string_literals(self):
        sql_content = (
            "BEGIN TRANSACTION;\n"
            "CREATE TABLE sample (id INTEGER PRIMARY KEY AUTOINCREMENT, note TEXT);\n"
            "INSERT INTO sample VALUES(1, 'alpha -- beta /* gamma */');\n"
            'DELETE FROM "sqlite_sequence";\n'
            "INSERT INTO sqlite_sequence VALUES('sample',1);\n"
            "COMMIT;"
        )

        assert validate_backup_sql(sql_content) is True


class TestRestoreAtomic:
    def test_restore_cancelled_leaves_live_db_untouched(self, tmp_path):
        """Operator cancellation exits cleanly without replacing the database."""
        db_path = tmp_path / "typer.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE sentinel (val TEXT)")
        conn.execute("INSERT INTO sentinel VALUES ('original')")
        conn.commit()
        conn.close()

        backup_file = tmp_path / "backup.sql"
        backup_file.write_text(VALID_SQL)

        with (
            patch("scripts.restore_db.DB_PATH", str(db_path)),
            patch("builtins.input", return_value="NO"),
            pytest.raises(SystemExit, match="0"),
        ):
            sys.argv = ["restore_db", str(backup_file)]
            main()

        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT val FROM sentinel").fetchall()
        conn.close()
        assert rows == [("original",)]

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
            patch("sys.exit") as mock_exit,
        ):
            sys.argv = ["restore_db", str(backup_file)]
            main()

        mock_exit.assert_called_once_with(1)

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
        assert "old_table" not in tables
        assert users == [("Alice",)]

    def test_successful_restore_clears_stale_temp_file_and_creates_backup_copy(self, tmp_path):
        """Successful restore removes stale temp state and snapshots the old live DB first."""
        db_path = tmp_path / "typer.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE old_table (x TEXT)")
        conn.execute("INSERT INTO old_table VALUES ('before')")
        conn.commit()
        conn.close()

        stale_tmp = tmp_path / "typer.db.restore_tmp"
        stale_tmp.write_text("stale")

        backup_file = tmp_path / "backup.sql"
        backup_file.write_text(VALID_SQL)

        with (
            patch("scripts.restore_db.DB_PATH", str(db_path)),
            patch("builtins.input", return_value="YES"),
        ):
            sys.argv = ["restore_db", str(backup_file)]
            main()

        assert not stale_tmp.exists()
        backup_copies = list(tmp_path.glob("typer.db.bak.*"))
        assert len(backup_copies) == 1

        backup_conn = sqlite3.connect(backup_copies[0])
        old_rows = backup_conn.execute("SELECT x FROM old_table").fetchall()
        backup_conn.close()
        assert old_rows == [("before",)]

    def test_restore_accepts_real_created_backup_output(self, tmp_path):
        source_db = tmp_path / "source.db"
        conn = sqlite3.connect(source_db)
        conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY AUTOINCREMENT, note TEXT)")
        conn.execute("CREATE UNIQUE INDEX idx_sample_note ON sample(note)")
        conn.execute("INSERT INTO sample (note) VALUES (?)", ("alpha -- beta /* gamma */",))
        conn.commit()
        conn.close()

        backup_file = create_backup(str(source_db), str(tmp_path / "backups"))
        restore_target = tmp_path / "typer.db"

        with (
            patch("scripts.restore_db.DB_PATH", str(restore_target)),
            patch("builtins.input", return_value="YES"),
        ):
            sys.argv = ["restore_db", backup_file]
            main()

        conn = sqlite3.connect(restore_target)
        rows = conn.execute("SELECT id, note FROM sample").fetchall()
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='sample'"
            ).fetchall()
        }
        conn.close()

        assert rows == [(1, "alpha -- beta /* gamma */")]
        assert "idx_sample_note" in indexes
