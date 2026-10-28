import sqlite3
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from app.services import google_sync_service as gss
from app.services.google_sync_service import _drive_manifest_should_preserve_local_contacts


class DriveManifestPruneGuardTests(unittest.TestCase):
    def test_keeps_local_when_drive_is_empty(self) -> None:
        self.assertTrue(_drive_manifest_should_preserve_local_contacts(730, 0))

    def test_keeps_local_when_drive_is_much_smaller(self) -> None:
        self.assertTrue(_drive_manifest_should_preserve_local_contacts(730, 10))

    def test_allows_normal_deletes_on_similar_sized_lists(self) -> None:
        self.assertFalse(_drive_manifest_should_preserve_local_contacts(730, 720))

    def test_does_not_guard_tiny_local_sets(self) -> None:
        self.assertFalse(_drive_manifest_should_preserve_local_contacts(10, 4))

    def test_does_not_guard_when_local_is_empty(self) -> None:
        self.assertFalse(_drive_manifest_should_preserve_local_contacts(0, 10))


class SmallerDriveManifestImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE contacts (
              id INTEGER PRIMARY KEY,
              family_name TEXT NOT NULL DEFAULT '',
              given_name TEXT NOT NULL DEFAULT '',
              photo TEXT NOT NULL DEFAULT '',
              photo_drive_file_id TEXT NOT NULL DEFAULT '',
              photo_sync_revision INTEGER NOT NULL DEFAULT 0,
              shared_drive_revision INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE google_sync_state (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              contacts_sync_revision INTEGER NOT NULL DEFAULT 0,
              pending_upload_count INTEGER NOT NULL DEFAULT 0,
              needs_upload_reminder INTEGER NOT NULL DEFAULT 0,
              needs_drive_export INTEGER NOT NULL DEFAULT 0,
              last_sync_error TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO google_sync_state (id, contacts_sync_revision) VALUES (1, 4);
            """
        )
        for contact_id in range(1, 31):
            self.conn.execute(
                """
                INSERT INTO contacts (id, family_name, given_name, shared_drive_revision)
                VALUES (?, ?, ?, 1)
                """,
                (contact_id, f"Family{contact_id}", f"Given{contact_id}"),
            )
        self.conn.commit()

        @contextmanager
        def fake_get_connection():
            yield self.conn

        self.connection_patcher = patch.object(gss, "get_connection", fake_get_connection)
        self.connection_patcher.start()

    def tearDown(self) -> None:
        self.connection_patcher.stop()
        self.conn.close()

    def test_import_does_not_delete_local_contacts_when_drive_list_is_tiny(self) -> None:
        manifest = {
            "format": "contactsfreeshare.shared_contacts.v2",
            "contacts": [
                {
                    "id": contact_id,
                    "family_name": f"Family{contact_id}",
                    "given_name": f"Given{contact_id}",
                    "shared_drive_revision": 1,
                    "file_name": f"contact_{contact_id}.json",
                }
                for contact_id in range(1, 6)
            ],
            "assignment_options": [],
        }

        with (
            patch.object(
                gss,
                "get_google_sync_summary",
                return_value={"state": {"contacts_sync_revision": 4}},
            ),
            patch.object(gss, "_drive_load_json_file_content", return_value=manifest),
            patch.object(gss, "_delete_local_contact_record") as delete_local,
            patch.object(gss, "_store_local_sync_revisions"),
            patch.object(gss, "_replace_contact_assignment_options"),
            patch.object(gss, "_import_changed_contact_photos_from_google_drive"),
        ):
            result = gss.import_shared_contacts_from_google_drive(
                access_token="token",
                storage={"contacts_current_file_id": "current"},
                remote_sync_state={"contacts_sync_revision": 5},
                backfill_manifest_digests=False,
            )

        self.assertTrue(result)
        delete_local.assert_not_called()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0], 30)

    def test_import_skips_tiny_backup_restore(self) -> None:
        payload = {
            "tables": {
                "contacts": [{"id": 1, "family_name": "Only", "given_name": "Ten"}],
            }
        }
        with (
            patch.object(
                gss,
                "get_google_sync_summary",
                return_value={"state": {"contacts_sync_revision": 4}},
            ),
            patch.object(gss, "_drive_load_json_file_content", return_value=payload),
            patch(
                "app.services.backup_service.restore_contacts_backup_payload",
                return_value={},
            ) as restore,
        ):
            result = gss.import_shared_contacts_from_google_drive(
                access_token="token",
                storage={"contacts_current_file_id": "current"},
                remote_sync_state={"contacts_sync_revision": 5},
            )

        self.assertTrue(result)
        restore.assert_not_called()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0], 30)


if __name__ == "__main__":
    unittest.main()
