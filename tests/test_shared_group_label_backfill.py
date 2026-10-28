import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.database import get_connection, initialize_database, set_request_db_path
from app.services import google_sync_service as gss
from app.services.google_sync_service import (
    _desired_group_names,
    ensure_google_sync_records,
    queue_assigned_contacts_for_shared_group_label,
)
from app.routes.presets import _save_shared_group_name_only
from app.services.preset_service import get_shared_contacts_group_name, save_book_layout_preset


class SharedGroupLabelBackfillTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        set_request_db_path(Path(self._tempdir.name) / "app.sqlite3")
        initialize_database()
        ensure_google_sync_records()
        with get_connection() as conn:
            conn.execute("UPDATE google_sync_state SET multi_editor_enabled = 1 WHERE id = 1")
            conn.commit()

    def tearDown(self) -> None:
        set_request_db_path(None)
        self._tempdir.cleanup()

    def _set_shared_group_name(self, name: str) -> None:
        save_book_layout_preset(
            None,
            {
                "name": "Pocket",
                "shared_contacts_group_name": name,
                "is_default": "1",
            },
        )

    def _insert_contact(self, **values) -> int:
        with get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO contacts (family_name, given_name, fields_text, meetings_text, google_contact_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    values.get("family_name", "Doe"),
                    values.get("given_name", "Jane"),
                    values.get("fields_text", ""),
                    values.get("meetings_text", ""),
                    values.get("google_contact_id", "people/1"),
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def test_upload_uses_the_settings_box_name_not_a_hardcoded_label(self) -> None:
        self._set_shared_group_name("West Texas (Shared)")
        names = _desired_group_names(
            {
                "fields_text": "Amarillo",
                "meetings_text": "Northside",
                "group_membership": "",
            }
        )
        self.assertEqual(get_shared_contacts_group_name(), "West Texas (Shared)")
        self.assertIn("West Texas (Shared)", names)
        self.assertIn("Amarillo - Northside (Shared)", names)
        self.assertNotIn("AlMsLa (Shared)", names)

    def test_apply_queues_field_meeting_contacts_and_skips_deleted(self) -> None:
        self._set_shared_group_name("Custom Label (Shared)")
        assigned_id = self._insert_contact(
            fields_text="Field A",
            meetings_text="Meeting 1",
            google_contact_id="people/assigned",
        )
        self._insert_contact(fields_text="", meetings_text="", google_contact_id="people/unassigned")
        self._insert_contact(
            fields_text="Field A",
            meetings_text="Deleted",
            google_contact_id="people/deleted",
        )

        result = queue_assigned_contacts_for_shared_group_label()
        self.assertTrue(result["sync_enabled"])
        self.assertEqual(result["group_name"], "Custom Label (Shared)")
        self.assertEqual(result["queued"], 1)
        self.assertEqual(result["assigned"], 1)

        with get_connection() as conn:
            rows = conn.execute(
                "SELECT contact_id FROM google_sync_queue WHERE status = 'pending'"
            ).fetchall()
        self.assertEqual([int(row["contact_id"]) for row in rows], [assigned_id])

    def test_apply_saves_the_name_from_the_settings_box(self) -> None:
        self._set_shared_group_name("Old Name (Shared)")
        self.assertTrue(_save_shared_group_name_only("Whatever Is In The Box (Shared)"))
        self.assertEqual(get_shared_contacts_group_name(), "Whatever Is In The Box (Shared)")

    def test_shared_group_label_upload_only_queues_google_memberships(self) -> None:
        self._set_shared_group_name("AlMsLa (Shared)")
        contact_id = self._insert_contact(
            fields_text="AL Central",
            meetings_text="Birmingham",
            google_contact_id="people/abc",
        )
        batch: dict = {}
        name_to_resource: dict[str, str] = {}
        resource_to_name: dict[str, str] = {}

        def fake_ensure(_token, group_name, n2r, r2n):
            resource = n2r.get(group_name) or f"contactGroups/{group_name}"
            n2r[group_name] = resource
            r2n[resource] = group_name
            return resource

        with get_connection() as conn:
            with (
                patch.object(gss, "_fetch_google_person") as fetch_person,
                patch.object(gss, "_api_json_request") as api_request,
                patch.object(gss, "_ensure_contact_group_resource", side_effect=fake_ensure),
            ):
                gss._process_upsert_queue_entry(
                    conn,
                    "token",
                    {
                        "contact_id": contact_id,
                        "payload_json": json.dumps({"sync_action": "Shared group label"}),
                    },
                    resource_to_name,
                    name_to_resource,
                    group_sync_batch=batch,
                )
        fetch_person.assert_not_called()
        api_request.assert_not_called()
        added = {member for changes in batch.values() for member in changes.get("add") or set()}
        self.assertIn("people/abc", added)
        self.assertTrue(any("AlMsLa (Shared)" in resource for resource in batch))

    def test_orphaned_upload_progress_keeps_a_continue_error(self) -> None:
        contact_id = self._insert_contact(fields_text="Field A", meetings_text="Meeting 1")
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO google_sync_queue (contact_id, google_contact_id, operation, payload_json, status)
                VALUES (?, 'people/1', 'update', '{}', 'pending')
                """,
                (contact_id,),
            )
            conn.execute(
                """
                UPDATE google_sync_state
                SET upload_in_progress = 1, pending_upload_count = 1, last_sync_error = ''
                WHERE id = 1
                """
            )
            conn.commit()
            cleared = gss._clear_orphaned_upload_progress_if_needed(conn)
            conn.commit()
            error = conn.execute("SELECT last_sync_error FROM google_sync_state WHERE id = 1").fetchone()[
                "last_sync_error"
            ]
        self.assertTrue(cleared)
        self.assertIn("Click Upload to continue", error)


if __name__ == "__main__":
    unittest.main()
