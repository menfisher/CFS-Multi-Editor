import sqlite3
import unittest
from unittest.mock import patch

from app.services.google_sync_service import (
    _merge_shared_contact_record_from_drive,
    _peek_shared_contact_import_action,
    _resolve_shared_contact_import_action,
    _shared_contact_payload_digest,
    _shared_contact_payload_matches,
    _shared_contact_remote_is_ahead,
    _sync_shared_contact_revision_only,
)


class SharedContactDriveImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE contacts (
              id INTEGER PRIMARY KEY,
              family_name TEXT NOT NULL DEFAULT '',
              given_name TEXT NOT NULL DEFAULT '',
              google_contact_id TEXT NOT NULL DEFAULT '',
              etag TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT '',
              shared_drive_revision INTEGER NOT NULL DEFAULT 0,
              shared_drive_needs_export INTEGER NOT NULL DEFAULT 0,
              shared_drive_bootstrapped INTEGER NOT NULL DEFAULT 0,
              photo_needs_export INTEGER NOT NULL DEFAULT 0,
              mtg_home_elder_flag TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE phones (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              contact_id INTEGER NOT NULL,
              position INTEGER NOT NULL DEFAULT 1,
              phone_type TEXT NOT NULL DEFAULT '',
              phone_number TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE emails (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              contact_id INTEGER NOT NULL,
              position INTEGER NOT NULL DEFAULT 1,
              email_type TEXT NOT NULL DEFAULT '',
              email_address TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE relationships (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              contact_id INTEGER NOT NULL,
              position INTEGER NOT NULL DEFAULT 1,
              relationship_type TEXT NOT NULL DEFAULT '',
              relationship_name TEXT NOT NULL DEFAULT ''
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
            """
        )
        self.conn.execute(
            """
            INSERT INTO contacts (
              id, family_name, given_name, google_contact_id, etag, status,
              shared_drive_revision, shared_drive_bootstrapped, mtg_home_elder_flag
            ) VALUES (1, 'Smith', 'Jane', 'people/c1', 'etag-local', 'synced', 0, 1, '1')
            """
        )
        self.conn.execute(
            """
            INSERT INTO phones (contact_id, position, phone_type, phone_number)
            VALUES (1, 1, 'mobile', '555-1111')
            """
        )
        self.conn.execute(
            """
            INSERT INTO contact_edit_log_entries (
              batch_id, batch_created_at, batch_row_index, editor_initials,
              contact_id, contact_name, label_name, original_text, edited_text
            ) VALUES ('local-batch', '2026-01-01T12:00:00', 1, 'CV', 1, 'Smith, Jane', 'Phone', 'old', 'new')
            """
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def _remote_payload(self, *, revision: int = 1, phone_number: str = "555-1111") -> dict:
        return {
            "format": "contactsfreeshare.shared_contact.v1",
            "exported_at": "2026-06-01T12:00:00",
            "contact": {
                "id": 1,
                "family_name": "Smith",
                "given_name": "Jane",
                "google_contact_id": "",
                "etag": "",
                "status": "",
                "shared_drive_revision": revision,
                "shared_drive_needs_export": 0,
                "shared_drive_bootstrapped": 1,
                "photo_needs_export": 0,
                "mtg_home_elder_flag": "",
            },
            "phones": [
                {
                    "id": 99,
                    "contact_id": 1,
                    "position": 1,
                    "phone_type": "mobile",
                    "phone_number": phone_number,
                }
            ],
            "emails": [],
            "relationships": [],
            "addresses": [],
            "custom_fields": [],
            "contact_edit_log_entries": [
                {
                    "id": 100,
                    "batch_id": "remote-batch",
                    "batch_created_at": "2026-06-01T12:00:00",
                    "batch_row_index": 1,
                    "editor_initials": "ME",
                    "contact_id": 1,
                    "contact_name": "Smith, Jane",
                    "label_name": "Phone",
                    "original_text": "555-1111",
                    "edited_text": "555-2222",
                }
            ],
        }

    def test_payload_matches_ignores_revision_only_differences(self) -> None:
        local_payload = self._remote_payload(revision=0)
        remote_payload = self._remote_payload(revision=1)
        self.assertTrue(
            _shared_contact_payload_matches(
                local_payload,
                remote_payload,
                include_edit_logs=False,
            )
        )

    def test_payload_digest_ignores_revision_only_differences(self) -> None:
        local_payload = self._remote_payload(revision=0)
        remote_payload = self._remote_payload(revision=1)
        self.assertEqual(
            _shared_contact_payload_digest(local_payload, include_edit_logs=False),
            _shared_contact_payload_digest(remote_payload, include_edit_logs=False),
        )

    def test_remote_ahead_filter_ignores_local_ahead_mismatches(self) -> None:
        """Local-ahead contacts must not keep reappearing as sign-in reconcile work."""
        remote_contacts = [
            {"id": 1, "shared_drive_revision": 2},
            {"id": 2, "shared_drive_revision": 5},
            {"id": 3, "shared_drive_revision": 1},
        ]
        local_revision_by_id = {1: 5, 2: 5, 3: 0}
        changed = [
            item
            for item in remote_contacts
            if _shared_contact_remote_is_ahead(
                local_revision_by_id.get(int(item["id"])),
                item.get("shared_drive_revision"),
            )
        ]
        self.assertEqual([item["id"] for item in changed], [3])
        self.assertFalse(_shared_contact_remote_is_ahead(5, 2))
        self.assertFalse(_shared_contact_remote_is_ahead(5, 5))
        self.assertTrue(_shared_contact_remote_is_ahead(0, 1))

    def test_peek_skip_when_remote_not_ahead(self) -> None:
        self.conn.execute("UPDATE contacts SET shared_drive_revision = 3 WHERE id = 1")
        self.conn.commit()
        action = _peek_shared_contact_import_action(
            self.conn,
            1,
            2,
            {"content_digest": "unused"},
        )
        self.assertEqual(action, "skip")

    def test_peek_revision_only_when_manifest_digest_matches(self) -> None:
        with patch(
            "app.services.google_sync_service._build_shared_contact_record_payload",
            return_value=self._remote_payload(revision=0),
        ):
            local_payload = self._remote_payload(revision=0)
            digest = _shared_contact_payload_digest(local_payload, include_edit_logs=False)
            action = _peek_shared_contact_import_action(
                self.conn,
                1,
                2,
                {"content_digest": digest},
            )
        self.assertEqual(action, "revision_only")

    def test_peek_requires_download_without_manifest_digest(self) -> None:
        action = _peek_shared_contact_import_action(self.conn, 1, 2, {})
        self.assertIsNone(action)

    def test_peek_import_when_contact_missing_locally(self) -> None:
        action = _peek_shared_contact_import_action(
            self.conn,
            99,
            1,
            {"content_digest": "abc123"},
        )
        self.assertEqual(action, "import")

    def test_resolve_revision_only_when_content_matches(self) -> None:
        remote_payload = self._remote_payload(revision=1)
        with patch(
            "app.services.google_sync_service._build_shared_contact_record_payload",
            return_value=self._remote_payload(revision=0),
        ):
            action = _resolve_shared_contact_import_action(self.conn, 1, 1, remote_payload)
        self.assertEqual(action, "revision_only")

    def test_resolve_import_when_remote_content_changed(self) -> None:
        remote_payload = self._remote_payload(revision=2, phone_number="555-2222")
        with patch(
            "app.services.google_sync_service._build_shared_contact_record_payload",
            return_value=self._remote_payload(revision=0),
        ):
            action = _resolve_shared_contact_import_action(self.conn, 1, 2, remote_payload)
        self.assertEqual(action, "import")

    def test_resolve_skip_when_remote_not_ahead(self) -> None:
        self.conn.execute("UPDATE contacts SET shared_drive_revision = 3 WHERE id = 1")
        self.conn.commit()
        action = _resolve_shared_contact_import_action(
            self.conn,
            1,
            2,
            self._remote_payload(revision=2),
        )
        self.assertEqual(action, "skip")

    def test_revision_only_updates_counter_without_touching_edit_logs(self) -> None:
        _sync_shared_contact_revision_only(self.conn, 1, 4)
        self.conn.commit()
        revision = self.conn.execute(
            "SELECT shared_drive_revision FROM contacts WHERE id = 1"
        ).fetchone()["shared_drive_revision"]
        edit_log_count = self.conn.execute(
            "SELECT COUNT(*) AS count FROM contact_edit_log_entries WHERE contact_id = 1"
        ).fetchone()["count"]
        self.assertEqual(int(revision), 4)
        self.assertEqual(int(edit_log_count), 1)

    def test_merge_preserves_local_edit_logs_and_google_link(self) -> None:
        remote_payload = self._remote_payload(revision=2, phone_number="555-2222")
        _merge_shared_contact_record_from_drive(self.conn, remote_payload)
        self.conn.commit()

        contact = dict(self.conn.execute("SELECT * FROM contacts WHERE id = 1").fetchone())
        phone = dict(self.conn.execute("SELECT phone_number FROM phones WHERE contact_id = 1").fetchone())
        batch_ids = [
            str(row["batch_id"])
            for row in self.conn.execute(
                "SELECT batch_id FROM contact_edit_log_entries WHERE contact_id = 1 ORDER BY batch_id"
            ).fetchall()
        ]

        self.assertEqual(contact["google_contact_id"], "people/c1")
        self.assertEqual(contact["etag"], "etag-local")
        self.assertEqual(contact["mtg_home_elder_flag"], "1")
        self.assertEqual(phone["phone_number"], "555-2222")
        self.assertEqual(batch_ids, ["local-batch", "remote-batch"])

    def test_merge_ignores_remote_edit_log_row_ids(self) -> None:
        self.conn.execute(
            """
            UPDATE contact_edit_log_entries
            SET id = 100
            WHERE batch_id = 'local-batch'
            """
        )
        self.conn.commit()
        remote_payload = self._remote_payload(revision=2, phone_number="555-2222")
        remote_payload["contact_edit_log_entries"][0]["id"] = 100

        _merge_shared_contact_record_from_drive(self.conn, remote_payload)
        self.conn.commit()

        rows = [
            dict(row)
            for row in self.conn.execute(
                """
                SELECT id, batch_id
                FROM contact_edit_log_entries
                WHERE contact_id = 1
                ORDER BY batch_id
                """
            ).fetchall()
        ]
        self.assertEqual([row["batch_id"] for row in rows], ["local-batch", "remote-batch"])
        self.assertEqual(len({int(row["id"]) for row in rows}), 2)

    def test_merge_collapses_duplicate_google_contact_ids(self) -> None:
        self.conn.execute(
            """
            INSERT INTO contacts (
              id, family_name, given_name, google_contact_id, etag, status,
              shared_drive_revision, shared_drive_bootstrapped
            ) VALUES (99, 'Smith', 'Jane', 'people/c1', 'etag-dup', 'synced', 0, 1)
            """
        )
        self.conn.execute(
            """
            INSERT INTO phones (contact_id, position, phone_type, phone_number)
            VALUES (99, 1, 'mobile', '555-9999')
            """
        )
        self.conn.commit()

        remote_payload = self._remote_payload(revision=2, phone_number="555-2222")
        remote_payload["contact"]["google_contact_id"] = "people/c1"
        merged_id = _merge_shared_contact_record_from_drive(self.conn, remote_payload)
        self.conn.commit()

        ids = [
            int(row["id"])
            for row in self.conn.execute(
                "SELECT id FROM contacts WHERE google_contact_id = 'people/c1' ORDER BY id"
            ).fetchall()
        ]
        phone = self.conn.execute(
            "SELECT phone_number FROM phones WHERE contact_id = ?",
            (merged_id,),
        ).fetchone()["phone_number"]
        orphan_phones = self.conn.execute(
            "SELECT COUNT(*) AS count FROM phones WHERE contact_id = 99"
        ).fetchone()["count"]

        self.assertEqual(merged_id, 1)
        self.assertEqual(ids, [1])
        self.assertEqual(phone, "555-2222")
        self.assertEqual(int(orphan_phones), 0)

    def test_merge_remaps_drive_id_onto_existing_google_person(self) -> None:
        remote_payload = self._remote_payload(revision=3, phone_number="555-3333")
        remote_payload["contact"]["id"] = 50
        remote_payload["contact"]["google_contact_id"] = "people/c1"
        remote_payload["phones"][0]["contact_id"] = 50
        remote_payload["contact_edit_log_entries"][0]["contact_id"] = 50

        merged_id = _merge_shared_contact_record_from_drive(self.conn, remote_payload)
        self.conn.commit()

        ids = [
            int(row["id"])
            for row in self.conn.execute("SELECT id FROM contacts ORDER BY id").fetchall()
        ]
        phone = self.conn.execute(
            "SELECT phone_number FROM phones WHERE contact_id = ?",
            (merged_id,),
        ).fetchone()["phone_number"]
        revision = self.conn.execute(
            "SELECT shared_drive_revision FROM contacts WHERE id = ?",
            (merged_id,),
        ).fetchone()["shared_drive_revision"]

        self.assertEqual(merged_id, 1)
        self.assertEqual(ids, [1])
        self.assertEqual(phone, "555-3333")
        self.assertEqual(int(revision), 3)

    def test_merge_remaps_new_google_id_onto_same_name_and_phone(self) -> None:
        self.conn.execute(
            "UPDATE contacts SET google_contact_id = 'people/c-old' WHERE id = 1"
        )
        self.conn.commit()
        remote_payload = self._remote_payload(revision=4, phone_number="555-1111")
        remote_payload["contact"]["id"] = 50
        remote_payload["contact"]["google_contact_id"] = "people/c-new"
        remote_payload["phones"][0]["contact_id"] = 50
        remote_payload["contact_edit_log_entries"][0]["contact_id"] = 50

        merged_id = _merge_shared_contact_record_from_drive(self.conn, remote_payload)
        self.conn.commit()

        rows = [
            dict(row)
            for row in self.conn.execute(
                "SELECT id, google_contact_id FROM contacts ORDER BY id"
            ).fetchall()
        ]
        self.assertEqual(merged_id, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], 1)
        self.assertEqual(rows[0]["google_contact_id"], "people/c-new")

    def test_merge_keeps_local_meeting_home_flag_when_drive_is_blank(self) -> None:
        remote_payload = self._remote_payload(revision=2, phone_number="555-2222")
        remote_payload["contact"]["mtg_home_elder_flag"] = ""
        _merge_shared_contact_record_from_drive(self.conn, remote_payload)
        self.conn.commit()
        flag = self.conn.execute(
            "SELECT mtg_home_elder_flag FROM contacts WHERE id = 1"
        ).fetchone()["mtg_home_elder_flag"]
        self.assertEqual(flag, "1")

    def test_merge_takes_nonblank_drive_meeting_home_flag(self) -> None:
        remote_payload = self._remote_payload(revision=2, phone_number="555-2222")
        remote_payload["contact"]["mtg_home_elder_flag"] = "2"
        _merge_shared_contact_record_from_drive(self.conn, remote_payload)
        self.conn.commit()
        flag = self.conn.execute(
            "SELECT mtg_home_elder_flag FROM contacts WHERE id = 1"
        ).fetchone()["mtg_home_elder_flag"]
        self.assertEqual(flag, "2")


if __name__ == "__main__":
    unittest.main()
