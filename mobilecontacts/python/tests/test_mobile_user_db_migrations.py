from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.database import _ensure_book_layout_preset_columns, apply_database_migrations, set_request_db_path
from mobile.user_db import _activate_database


class MobileUserDbMigrationTests(unittest.TestCase):
    def test_existing_database_runs_migrations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "app.sqlite3"
            db_path.write_bytes(b"placeholder")
            set_request_db_path(db_path)
            with patch("mobile.user_db.apply_database_migrations") as mock_migrate:
                with patch("mobile.user_db._ensure_sqlite_journal_mode"):
                    _activate_database(db_path)
            mock_migrate.assert_called_once()

    def test_book_layout_migration_adds_shared_contacts_group_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "app.sqlite3"
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute(
                    """
                    CREATE TABLE book_layout_presets (
                      id INTEGER PRIMARY KEY,
                      book_title TEXT NOT NULL DEFAULT ''
                    )
                    """
                )
                _ensure_book_layout_preset_columns(conn)
                columns = {
                    row["name"]
                    for row in conn.execute("PRAGMA table_info(book_layout_presets)").fetchall()
                }
            finally:
                conn.close()
            self.assertIn("shared_contacts_group_name", columns)


if __name__ == "__main__":
    unittest.main()
