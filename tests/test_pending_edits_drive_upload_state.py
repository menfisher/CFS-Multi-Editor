import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.changes_list_service import pending_edits_has_drive_upload


class PendingEditsDriveUploadStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE google_sync_state (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              changes_list_needs_drive_export INTEGER NOT NULL DEFAULT 0,
              updated_at TEXT NOT NULL DEFAULT ''
            );
            INSERT INTO google_sync_state (id, changes_list_needs_drive_export) VALUES (1, 1);

            CREATE TABLE changes_list_entries (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              sort_order INTEGER NOT NULL DEFAULT 1,
              automatic_date TEXT NOT NULL DEFAULT '',
              name_event TEXT NOT NULL DEFAULT '',
              changes_text TEXT NOT NULL DEFAULT '',
              completed_by TEXT NOT NULL DEFAULT '',
              completed_at TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO changes_list_entries (sort_order) VALUES (1);

            CREATE TABLE contacts (
              id INTEGER PRIMARY KEY,
              photo TEXT NOT NULL DEFAULT '',
              photo_needs_export INTEGER NOT NULL DEFAULT 0,
              shared_drive_needs_export INTEGER NOT NULL DEFAULT 0
            );
            """
        )

    def tearDown(self) -> None:
        self.conn.close()

    def test_clears_stale_changes_flag_when_only_blank_row_exists(self) -> None:
        self.assertFalse(pending_edits_has_drive_upload(conn=self.conn))
        row = self.conn.execute(
            "SELECT changes_list_needs_drive_export FROM google_sync_state WHERE id = 1"
        ).fetchone()
        self.assertEqual(int(row["changes_list_needs_drive_export"]), 0)

    def test_ignores_completed_entries_for_upload_prompt(self) -> None:
        self.conn.execute(
            """
            INSERT INTO changes_list_entries (
              sort_order, automatic_date, name_event, changes_text, completed_by
            ) VALUES (2, 'CV, 06/01/2026, 10:00', 'Smith', 'Done', 'ED')
            """
        )
        self.assertFalse(pending_edits_has_drive_upload(conn=self.conn))

    def test_detects_unuploaded_pending_edit(self) -> None:
        self.conn.execute(
            """
            INSERT INTO changes_list_entries (
              sort_order, automatic_date, name_event, changes_text
            ) VALUES (2, 'CV, 06/01/2026, 10:00', 'Smith', 'Phone change')
            """
        )
        self.assertTrue(pending_edits_has_drive_upload(conn=self.conn))

    def test_ignores_shared_drive_export_without_pending_edits_content(self) -> None:
        self.conn.execute(
            "UPDATE google_sync_state SET changes_list_needs_drive_export = 0 WHERE id = 1"
        )
        self.conn.execute(
            """
            INSERT INTO contacts (id, shared_drive_needs_export, photo_needs_export)
            VALUES (1, 1, 0)
            """
        )
        self.assertFalse(pending_edits_has_drive_upload(conn=self.conn))

    @patch("app.services.changes_list_service.pending_photo_path_is_available", return_value=True)
    def test_detects_pending_photo_with_local_file(self, _mock_available) -> None:
        self.conn.execute(
            "UPDATE google_sync_state SET changes_list_needs_drive_export = 0 WHERE id = 1"
        )
        self.conn.execute(
            """
            INSERT INTO contacts (id, photo, photo_needs_export)
            VALUES (5, '/static/uploads/contact_photos/contact_5_photo.jpg', 1)
            """
        )
        self.assertTrue(pending_edits_has_drive_upload(conn=self.conn))


if __name__ == "__main__":
    unittest.main()
