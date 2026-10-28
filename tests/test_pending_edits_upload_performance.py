import sqlite3
import unittest

from app.services.google_sync_service import _patch_shared_contacts_manifest


class PendingEditsUploadPerformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE contacts (
              id INTEGER PRIMARY KEY,
              shared_drive_revision INTEGER NOT NULL DEFAULT 0,
              status TEXT,
              family_name TEXT,
              given_name TEXT
            );
            INSERT INTO contacts (id, shared_drive_revision, family_name, given_name)
            VALUES (1, 3, 'Smith', 'John'), (2, 1, 'Doe', 'Jane');
            """
        )

    def tearDown(self) -> None:
        self.conn.close()

    def test_patch_shared_contacts_manifest_updates_only_changed_contacts(self) -> None:
        existing = {
            "format": "contactsfreeshare.shared_contacts.v2",
            "contacts": [
                {
                    "id": 1,
                    "shared_drive_revision": 2,
                    "status": "",
                    "family_name": "Smith",
                    "given_name": "John",
                    "file_name": "contact_1.json",
                },
                {
                    "id": 2,
                    "shared_drive_revision": 1,
                    "status": "",
                    "family_name": "Doe",
                    "given_name": "Jane",
                    "file_name": "contact_2.json",
                },
            ],
        }

        patched = _patch_shared_contacts_manifest(existing, self.conn, [1])

        self.assertEqual(len(patched["contacts"]), 2)
        self.assertEqual(patched["contacts"][0]["shared_drive_revision"], 3)
        self.assertEqual(patched["contacts"][1]["shared_drive_revision"], 1)


if __name__ == "__main__":
    unittest.main()
