import sqlite3
import unittest

from app.services.google_sync_service import (
    _shared_contact_app_only_drive_export_needed,
    _shared_contact_drive_export_needed,
    _update_contact_from_google_person,
)


class GoogleSyncDriveExportReconcileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE contacts (
              id INTEGER PRIMARY KEY,
              google_contact_id TEXT NOT NULL DEFAULT '',
              etag TEXT NOT NULL DEFAULT '',
              last_updated TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT '',
              fields_text TEXT NOT NULL DEFAULT '',
              meetings_text TEXT NOT NULL DEFAULT '',
              group_membership TEXT NOT NULL DEFAULT '',
              family_name TEXT NOT NULL DEFAULT '',
              given_name TEXT NOT NULL DEFAULT '',
              photo TEXT NOT NULL DEFAULT '',
              photo_drive_file_id TEXT NOT NULL DEFAULT '',
              birthday TEXT NOT NULL DEFAULT '',
              notes TEXT NOT NULL DEFAULT '',
              shared_drive_bootstrapped INTEGER NOT NULL DEFAULT 1,
              shared_drive_needs_export INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE phones (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              contact_id INTEGER NOT NULL,
              position INTEGER NOT NULL DEFAULT 1,
              phone_type TEXT NOT NULL DEFAULT '',
              phone_value TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE emails (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              contact_id INTEGER NOT NULL,
              position INTEGER NOT NULL DEFAULT 1,
              email_type TEXT NOT NULL DEFAULT '',
              email_value TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE relationships (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              contact_id INTEGER NOT NULL,
              position INTEGER NOT NULL DEFAULT 1,
              relation_type TEXT NOT NULL DEFAULT '',
              relation_value TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE addresses (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              contact_id INTEGER NOT NULL,
              position INTEGER NOT NULL DEFAULT 1,
              address_type TEXT NOT NULL DEFAULT '',
              formatted_address TEXT NOT NULL DEFAULT '',
              coordinates TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE custom_fields (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              contact_id INTEGER NOT NULL,
              position INTEGER NOT NULL DEFAULT 1,
              field_type TEXT NOT NULL DEFAULT '',
              field_value TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE contact_edit_log_entries (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              batch_id TEXT NOT NULL,
              batch_created_at TEXT NOT NULL DEFAULT '',
              batch_row_index INTEGER NOT NULL DEFAULT 1,
              editor_initials TEXT NOT NULL DEFAULT '',
              contact_id INTEGER NOT NULL,
              contact_name TEXT NOT NULL DEFAULT '',
              label_name TEXT NOT NULL DEFAULT '',
              original_text TEXT NOT NULL DEFAULT '',
              edited_text TEXT NOT NULL DEFAULT ''
            );
            INSERT INTO contacts (
              id, family_name, given_name, photo, photo_drive_file_id,
              shared_drive_bootstrapped, shared_drive_needs_export
            ) VALUES (
              1, 'Smith', 'Jane',
              '/static/uploads/contact_photos/contact_1_photo.jpg',
              'drive-photo-1', 1, 1
            );
            """
        )

    def tearDown(self) -> None:
        self.conn.close()

    def test_google_pull_preserves_custom_drive_photo(self) -> None:
        _update_contact_from_google_person(
            self.conn,
            1,
            {
                "resource_name": "people/c1",
                "etag": "etag-new",
                "fields_text": "",
                "meetings_text": "",
                "group_names": [],
                "family_name": "Smith",
                "given_name": "Jane",
                "photo": "https://lh3.googleusercontent.com/new-photo",
                "birthday": "",
                "notes": "",
                "relations": [],
                "phones": [],
                "emails": [],
                "addresses": [],
                "user_defined": [],
                "user_defined_coordinates": {},
                "url_coordinate_candidates": [],
            },
        )
        row = dict(self.conn.execute("SELECT photo FROM contacts WHERE id = 1").fetchone())
        self.assertEqual(row["photo"], "/static/uploads/contact_photos/contact_1_photo.jpg")

    def test_shared_contact_drive_export_not_needed_when_drive_matches(self) -> None:
        payload = {
            "format": "contactsfreeshare.shared_contact.v1",
            "contact": {"id": 1, "family_name": "Smith", "given_name": "Jane"},
            "relationships": [],
            "phones": [],
            "emails": [],
            "addresses": [],
            "custom_fields": [],
        }
        self.assertFalse(
            _shared_contact_drive_export_needed(
                bootstrapped=True,
                local_payload=payload,
                remote_payload=payload,
            )
        )

    def test_google_pull_does_not_require_drive_export_when_only_google_fields_differ(self) -> None:
        local_payload = {
            "format": "contactsfreeshare.shared_contact.v1",
            "contact": {
                "id": 1,
                "family_name": "Smith",
                "given_name": "Jane",
                "mtg_home_elder_flag": "Home",
            },
            "phones": [{"phone_type": "mobile", "phone_value": "555-1111"}],
            "emails": [],
            "addresses": [],
            "custom_fields": [],
            "contact_edit_log_entries": [],
        }
        remote_payload = {
            "format": "contactsfreeshare.shared_contact.v1",
            "contact": {
                "id": 1,
                "family_name": "Smith",
                "given_name": "Jane",
                "mtg_home_elder_flag": "Home",
            },
            "phones": [{"phone_type": "mobile", "phone_value": "555-2222"}],
            "emails": [],
            "addresses": [],
            "custom_fields": [],
            "contact_edit_log_entries": [],
        }
        self.assertTrue(
            _shared_contact_drive_export_needed(
                bootstrapped=True,
                local_payload=local_payload,
                remote_payload=remote_payload,
            )
        )
        self.assertFalse(
            _shared_contact_app_only_drive_export_needed(
                bootstrapped=True,
                local_payload=local_payload,
                remote_payload=remote_payload,
            )
        )


if __name__ == "__main__":
    unittest.main()
