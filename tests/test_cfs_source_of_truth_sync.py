"""CFS is the source of truth: Google must not erase local contacts."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.database import get_connection, initialize_database, set_request_db_path
from app.services import google_sync_service as gss


class CfsSourceOfTruthSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        set_request_db_path(Path(self._tempdir.name) / "app.sqlite3")
        initialize_database()
        gss.ensure_google_sync_records()
        with get_connection() as conn:
            conn.execute("UPDATE google_sync_state SET multi_editor_enabled = 1 WHERE id = 1")
            conn.commit()

    def tearDown(self) -> None:
        set_request_db_path(None)
        self._tempdir.cleanup()

    def _insert_contact(self, *, google_contact_id: str, family_name: str, given_name: str) -> int:
        with get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO contacts (family_name, given_name, google_contact_id)
                VALUES (?, ?, ?)
                """,
                (family_name, given_name, google_contact_id),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def test_google_deleted_flag_keeps_cfs_contact_and_queues_restore(self) -> None:
        contact_id = self._insert_contact(
            google_contact_id="people/dead",
            family_name="Bible",
            given_name="Keith",
        )
        tombstone = {"resourceName": "people/dead", "metadata": {"deleted": True}}
        with (
            patch.object(gss, "_get_valid_access_token", return_value=("tok", "owner@example.com")),
            patch.object(gss, "_contact_groups_map", return_value={}),
            patch.object(
                gss,
                "_fetch_google_connections_for_sync",
                return_value=([tombstone], "sync-token", False),
            ),
        ):
            result = gss.sync_google_contact_changes(skip_drive_reconcile=True)
        with get_connection() as conn:
            remaining = conn.execute("SELECT id FROM contacts WHERE id = ?", (contact_id,)).fetchone()
            queued = conn.execute(
                """
                SELECT contact_id, operation, payload_json
                FROM google_sync_queue
                WHERE status = 'pending' AND contact_id = ?
                """,
                (contact_id,),
            ).fetchone()
        self.assertIsNotNone(remaining)
        self.assertEqual(result["deleted_count"], 0)
        self.assertIsNotNone(queued)
        self.assertEqual(queued["operation"], "update")
        self.assertIn("Restore", queued["payload_json"] or "")

    def test_full_fetch_does_not_delete_contacts_missing_from_google(self) -> None:
        kept_id = self._insert_contact(
            google_contact_id="people/live",
            family_name="Live",
            given_name="Person",
        )
        missing_id = self._insert_contact(
            google_contact_id="people/gone",
            family_name="Scarborough",
            given_name="Matt",
        )
        live = {
            "resourceName": "people/live",
            "metadata": {},
            "names": [{"familyName": "Live", "givenName": "Person"}],
            "etag": "etag-live",
        }
        with (
            patch.object(gss, "_get_valid_access_token", return_value=("tok", "owner@example.com")),
            patch.object(gss, "_contact_groups_map", return_value={}),
            patch.object(gss, "_google_person_import_fields", return_value=None),
            patch.object(
                gss,
                "_fetch_google_connections_for_sync",
                return_value=([live], "sync-token", False),
            ),
        ):
            gss.sync_google_contact_changes(force_full_fetch=True, skip_drive_reconcile=True)
        with get_connection() as conn:
            ids = [
                int(row["id"])
                for row in conn.execute("SELECT id FROM contacts ORDER BY id")
            ]
            queued = {
                int(row["contact_id"])
                for row in conn.execute(
                    "SELECT contact_id FROM google_sync_queue WHERE status = 'pending'"
                )
            }
        self.assertEqual(ids, [kept_id, missing_id])
        self.assertIn(missing_id, queued)

    def test_google_only_card_is_not_inserted_into_cfs(self) -> None:
        fields = {
            "resource_name": "people/new",
            "etag": "etag-new",
            "family_name": "Stray",
            "given_name": "Sam",
            "phones": [],
            "emails": [],
            "group_names": [],
            "fields_text": "",
            "meetings_text": "",
        }
        with (
            patch.object(gss, "_get_valid_access_token", return_value=("tok", "owner@example.com")),
            patch.object(gss, "_contact_groups_map", return_value={}),
            patch.object(gss, "_google_person_import_fields", return_value=fields),
            patch.object(
                gss,
                "_fetch_google_connections_for_sync",
                return_value=(
                    [{"resourceName": "people/new", "metadata": {}}],
                    "sync-token",
                    False,
                ),
            ),
        ):
            result = gss.sync_google_contact_changes(skip_drive_reconcile=True)
        with get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) AS count FROM contacts").fetchone()["count"]
        self.assertEqual(int(count), 0)
        self.assertEqual(result["created_count"], 0)


if __name__ == "__main__":
    unittest.main()
