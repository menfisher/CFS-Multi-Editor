import sqlite3
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from app.services.changes_list_service import (
    complete_changes_entry,
    find_pending_photo_location_for_contact,
    record_contact_edits_to_pending_list,
)


class PendingPhotoEditsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE changes_list_entries (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              sort_order INTEGER NOT NULL,
              automatic_date TEXT NOT NULL DEFAULT '',
              name_event TEXT NOT NULL DEFAULT '',
              changes_text TEXT NOT NULL DEFAULT '',
              completed_by TEXT NOT NULL DEFAULT '',
              completed_at TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE google_sync_state (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              changes_list_needs_drive_export INTEGER NOT NULL DEFAULT 0,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO google_sync_state (id) VALUES (1);
            """
        )
        self.conn.commit()

        @contextmanager
        def fake_get_connection():
            try:
                yield self.conn
            finally:
                pass

        self.connection_patcher = patch("app.services.changes_list_service.get_connection", fake_get_connection)
        self.connection_patcher.start()

    def tearDown(self) -> None:
        self.connection_patcher.stop()
        self.conn.close()

    def test_find_pending_photo_location_for_contact_matches_name_and_field(self) -> None:
        record_contact_edits_to_pending_list(
            contact_name="Smith, John",
            edit_entries=[
                {
                    "label_name": "Photo Location",
                    "original_text": "https://lh3.googleusercontent.com/old-photo",
                    "edited_text": "/static/uploads/contact_photos/example.jpg",
                }
            ],
            initials="AB",
        )

        pending = find_pending_photo_location_for_contact("Smith, John")

        self.assertIsNotNone(pending)
        self.assertEqual(pending["edited"], "/static/uploads/contact_photos/example.jpg")

    def test_find_pending_photo_location_for_contact_ignores_completed_entries(self) -> None:
        entry = record_contact_edits_to_pending_list(
            contact_name="Smith, John",
            edit_entries=[
                {
                    "label_name": "Photo Location",
                    "original_text": "https://lh3.googleusercontent.com/old-photo",
                    "edited_text": "/static/uploads/contact_photos/example.jpg",
                }
            ],
            initials="AB",
        )
        complete_changes_entry(int(entry["id"]), initials="ED")

        self.assertIsNone(find_pending_photo_location_for_contact("Smith, John"))

    def test_get_pending_photo_change_for_contact_hides_already_applied(self) -> None:
        from app.services.contact_service import get_pending_photo_change_for_contact

        record_contact_edits_to_pending_list(
            contact_name="Smith, John",
            edit_entries=[
                {
                    "label_name": "Photo Location",
                    "original_text": "https://lh3.googleusercontent.com/old-photo",
                    "edited_text": "/static/uploads/contact_photos/example.jpg",
                }
            ],
            initials="AB",
        )
        contact = {
            "family_name": "Smith",
            "given_name": "John",
            "photo": "/static/uploads/contact_photos/example.jpg",
        }

        self.assertIsNone(get_pending_photo_change_for_contact(contact))

    def test_enrich_changes_entry_adds_contact_edit_url(self) -> None:
        from app.services.changes_list_service import enrich_changes_entry_for_display

        enriched = enrich_changes_entry_for_display(
            {"id": 1, "name_event": "Smith, John", "changes_text": "", "completed_by": ""},
            contact_id_lookup={"smith, john": 42},
        )

        self.assertEqual(enriched["contact_id"], 42)
        self.assertEqual(enriched["contact_edit_url"], "/contacts/42/edit?return_to=%2Fchanges-list")


if __name__ == "__main__":
    unittest.main()
