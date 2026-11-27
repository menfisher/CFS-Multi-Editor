import sqlite3
import unittest
from unittest.mock import patch

from app.services.google_sync_service import (
    _drive_manifest_membership_delta,
    _patch_shared_contacts_manifest,
    _replace_shared_contacts_manifest_from_local,
)


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
            CREATE TABLE contact_assignment_options (
              id INTEGER PRIMARY KEY,
              kind TEXT,
              value TEXT
            );
            INSERT INTO contacts (id, shared_drive_revision, family_name, given_name, status)
            VALUES (1, 3, 'Smith', 'John', ''), (2, 1, 'Doe', 'Jane', '');
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

        with patch(
            "app.services.google_sync_service._build_shared_contact_record_payload",
            return_value=None,
        ):
            patched = _patch_shared_contacts_manifest(existing, self.conn, [1])

        self.assertEqual(len(patched["contacts"]), 2)
        self.assertEqual(patched["contacts"][0]["shared_drive_revision"], 3)
        self.assertEqual(patched["contacts"][1]["shared_drive_revision"], 1)

    def test_replace_drops_drive_ids_that_are_not_in_cfs(self) -> None:
        existing = {
            "format": "contactsfreeshare.shared_contacts.v2",
            "contacts": [
                {
                    "id": 1,
                    "shared_drive_revision": 3,
                    "status": "",
                    "family_name": "Smith",
                    "given_name": "John",
                    "file_name": "contact_1.json",
                },
                {
                    "id": 9000,
                    "shared_drive_revision": 6,
                    "status": "",
                    "family_name": "Leftover",
                    "given_name": "Card",
                    "file_name": "contact_9000.json",
                },
            ],
        }

        with patch(
            "app.services.google_sync_service._build_shared_contact_record_payload",
            return_value=None,
        ):
            replaced = _replace_shared_contacts_manifest_from_local(
                self.conn,
                existing_manifest=existing,
                refreshed_contact_ids=[1],
            )

        ids = [item["id"] for item in replaced["contacts"]]
        self.assertEqual(ids, [1, 2])
        self.assertNotIn(9000, ids)

    def test_replace_keeps_drive_ids_this_copy_does_not_have(self) -> None:
        existing = {
            "format": "contactsfreeshare.shared_contacts.v2",
            "contacts": [
                {
                    "id": 1,
                    "shared_drive_revision": 3,
                    "status": "",
                    "family_name": "Smith",
                    "given_name": "John",
                    "file_name": "contact_1.json",
                },
                {
                    "id": 9000,
                    "shared_drive_revision": 6,
                    "status": "",
                    "family_name": "Leftover",
                    "given_name": "Card",
                    "file_name": "contact_9000.json",
                },
            ],
        }

        with patch(
            "app.services.google_sync_service._build_shared_contact_record_payload",
            return_value=None,
        ):
            replaced = _replace_shared_contacts_manifest_from_local(
                self.conn,
                existing_manifest=existing,
                refreshed_contact_ids=[1],
                keep_drive_ids_not_in_local=True,
            )

        ids = [item["id"] for item in replaced["contacts"]]
        self.assertEqual(ids, [1, 2, 9000])

    def test_membership_delta_finds_missing_and_leftover_ids(self) -> None:
        existing = {
            "format": "contactsfreeshare.shared_contacts.v2",
            "contacts": [
                {"id": 9000, "file_name": "contact_9000.json"},
                {"id": 1, "file_name": "contact_1.json"},
            ],
        }
        missing, leftovers = _drive_manifest_membership_delta(self.conn, existing)
        self.assertEqual(missing, [2])
        self.assertEqual(leftovers, [9000])


if __name__ == "__main__":
    unittest.main()
