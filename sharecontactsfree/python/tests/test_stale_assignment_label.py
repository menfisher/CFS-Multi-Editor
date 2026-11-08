"""Stale assignment label cleanup when a contact changes CFS groups.

When an owner contact moves from one Field+Meeting group to another (e.g.
from 'Deleted - 2026' back to 'Contacts - LA North'), the Share app must
remove the old assignment label from the recipient contact so it does not
accumulate stale '(Shared)' labels.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from app import db
from app.people_recipient import (
    remove_contact_from_group,
    remove_outdated_assignment_labels,
    remove_stale_assignment_label,
)


def _make_service(group_exists: bool = True) -> MagicMock:
    service = MagicMock()
    if group_exists:
        service.contactGroups.return_value.list.return_value.execute.return_value = {
            "contactGroups": [
                {"name": "Deleted - 2026 (Shared)", "resourceName": "contactGroups/old123"}
            ]
        }
    else:
        service.contactGroups.return_value.list.return_value.execute.return_value = {
            "contactGroups": []
        }
    service.contactGroups.return_value.members.return_value.modify.return_value.execute.return_value = {}
    return service


class RemoveContactFromGroupTests(unittest.TestCase):
    def test_calls_modify_with_remove(self) -> None:
        service = MagicMock()
        service.contactGroups.return_value.members.return_value.modify.return_value.execute.return_value = {}
        creds = MagicMock()
        with patch("app.people_recipient.people_service", return_value=service):
            result = remove_contact_from_group(creds, "people/abc", "contactGroups/grp1")
        self.assertTrue(result)
        service.contactGroups.return_value.members.return_value.modify.assert_called_once_with(
            resourceName="contactGroups/grp1",
            body={"resourceNamesToRemove": ["people/abc"]},
        )

    def test_returns_false_on_missing_args(self) -> None:
        creds = MagicMock()
        self.assertFalse(remove_contact_from_group(creds, "", "contactGroups/grp1"))
        self.assertFalse(remove_contact_from_group(creds, "people/abc", ""))


class RemoveStaleAssignmentLabelTests(unittest.TestCase):
    def test_removes_old_label_when_changed(self) -> None:
        service = _make_service(group_exists=True)
        creds = MagicMock()
        with patch("app.people_recipient.people_service", return_value=service):
            result = remove_stale_assignment_label(
                creds,
                "people/carol",
                "Deleted - 2026 (Shared)",
                "Contacts - LA North (Shared)",
            )
        self.assertTrue(result)
        service.contactGroups.return_value.members.return_value.modify.assert_called_once_with(
            resourceName="contactGroups/old123",
            body={"resourceNamesToRemove": ["people/carol"]},
        )

    def test_noop_when_labels_are_equal(self) -> None:
        service = _make_service()
        creds = MagicMock()
        with patch("app.people_recipient.people_service", return_value=service):
            result = remove_stale_assignment_label(
                creds,
                "people/carol",
                "Contacts - LA North (Shared)",
                "Contacts - LA North (Shared)",
            )
        self.assertTrue(result)
        service.contactGroups.return_value.members.return_value.modify.assert_not_called()

    def test_noop_when_old_label_is_empty(self) -> None:
        service = _make_service()
        creds = MagicMock()
        with patch("app.people_recipient.people_service", return_value=service):
            result = remove_stale_assignment_label(
                creds, "people/carol", "", "Contacts - LA North (Shared)"
            )
        self.assertTrue(result)
        service.contactGroups.return_value.members.return_value.modify.assert_not_called()

    def test_returns_true_when_old_group_not_found(self) -> None:
        service = _make_service(group_exists=False)
        creds = MagicMock()
        with patch("app.people_recipient.people_service", return_value=service):
            result = remove_stale_assignment_label(
                creds,
                "people/carol",
                "Deleted - 2026 (Shared)",
                "Contacts - LA North (Shared)",
            )
        self.assertTrue(result)
        service.contactGroups.return_value.members.return_value.modify.assert_not_called()


class RemoveOutdatedAssignmentLabelsTests(unittest.TestCase):
    def _service(self, memberships: list[str]) -> MagicMock:
        service = MagicMock()
        service.people.return_value.get.return_value.execute.return_value = {
            "memberships": [
                {"contactGroupMembership": {"contactGroupResourceName": gid}}
                for gid in memberships
            ]
        }
        return service

    def test_removes_deleted_label_but_keeps_current_and_import_groups(self) -> None:
        names = {
            "contactGroups/import": "AlMsLa (Shared)",
            "contactGroups/current": "Contacts - LA North (Shared)",
            "contactGroups/deleted": "Deleted - 2026 (Shared)",
            "contactGroups/myContacts": "myContacts",
        }
        service = self._service(list(names))
        with patch("app.people_recipient.people_service", return_value=service):
            removed = remove_outdated_assignment_labels(
                MagicMock(),
                "people/carol",
                "Contacts - LA North (Shared)",
                {"contactGroups/import"},
                names,
            )
        self.assertEqual(removed, 1)
        modify = service.contactGroups.return_value.members.return_value.modify
        modify.assert_called_once_with(
            resourceName="contactGroups/deleted",
            body={"resourceNamesToRemove": ["people/carol"]},
        )

    def test_protected_import_group_with_dash_in_name_is_kept(self) -> None:
        names = {"contactGroups/import": "AL South - Birmingham (GRAHAM) (shared)"}
        service = self._service(list(names))
        with patch("app.people_recipient.people_service", return_value=service):
            removed = remove_outdated_assignment_labels(
                MagicMock(), "people/x", "", {"contactGroups/import"}, names
            )
        self.assertEqual(removed, 0)


class StartCardRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        db._active_database_path = Path(self._tmpdir.name) / "test.db"
        db.init_db()

    def tearDown(self) -> None:
        db._active_database_path = None
        self._tmpdir.cleanup()

    def test_done_state_blocks_restart_unless_requested(self) -> None:
        from app.services import card_repair

        db.upsert_sync_mapping(
            share_id="s1",
            owner="o@example.com",
            group_resource_name="contactGroups/g1",
            recipient_email="r@example.com",
            owner_person_id="people/p1",
            recipient_person_id="people/r1",
        )
        card_repair.save_card_repair_status({**card_repair._empty_status(), "state": "done"})
        with patch("app.services.push_worker.kick_push_queue"):
            self.assertEqual(card_repair.start_card_repair()["state"], "done")
            self.assertEqual(
                card_repair.start_card_repair(restart=True)["state"], "queued"
            )


class DbMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        db._active_database_path = Path(self._tmpdir.name) / "test.db"

    def tearDown(self) -> None:
        db._active_database_path = None
        self._tmpdir.cleanup()

    def test_upsert_stores_and_retrieves_last_assignment_label(self) -> None:
        with db.connect() as conn:
            conn.executescript(db.SCHEMA)
            db._migrate_sync_mappings(conn)

        db.upsert_sync_mapping(
            share_id="s1",
            owner="owner@example.com",
            group_resource_name="contactGroups/g1",
            recipient_email="recipient@example.com",
            owner_person_id="people/p1",
            recipient_person_id="people/r1",
            last_hash="hash1",
            last_assignment_label="Deleted - 2026 (Shared)",
        )
        mappings = db.find_mappings_for_owner_person("owner@example.com", "people/p1")
        self.assertEqual(len(mappings), 1)
        self.assertEqual(mappings[0]["last_assignment_label"], "Deleted - 2026 (Shared)")

    def test_upsert_updates_last_assignment_label(self) -> None:
        with db.connect() as conn:
            conn.executescript(db.SCHEMA)
            db._migrate_sync_mappings(conn)

        db.upsert_sync_mapping(
            share_id="s1",
            owner="owner@example.com",
            group_resource_name="contactGroups/g1",
            recipient_email="recipient@example.com",
            owner_person_id="people/p1",
            recipient_person_id="people/r1",
            last_hash="hash1",
            last_assignment_label="Deleted - 2026 (Shared)",
        )
        db.upsert_sync_mapping(
            share_id="s1",
            owner="owner@example.com",
            group_resource_name="contactGroups/g1",
            recipient_email="recipient@example.com",
            owner_person_id="people/p1",
            recipient_person_id="people/r1",
            last_hash="hash2",
            last_assignment_label="Contacts - LA North (Shared)",
        )
        mappings = db.find_mappings_for_owner_person("owner@example.com", "people/p1")
        self.assertEqual(len(mappings), 1)
        self.assertEqual(mappings[0]["last_assignment_label"], "Contacts - LA North (Shared)")

    def test_migration_adds_column_to_existing_db(self) -> None:
        """Simulates an existing database that was created before this column existed."""
        with db.connect() as conn:
            # Create the table without last_assignment_label (old schema)
            conn.execute("""
                CREATE TABLE sync_mappings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    share_id TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    group_resource_name TEXT NOT NULL,
                    recipient_email TEXT NOT NULL,
                    owner_person_id TEXT NOT NULL,
                    recipient_person_id TEXT,
                    last_hash TEXT,
                    last_photo_hash TEXT,
                    last_sync TEXT,
                    owner_etag TEXT
                )
            """)
            conn.commit()

        with db.connect() as conn:
            db._migrate_sync_mappings(conn)

        with db.connect() as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(sync_mappings)").fetchall()}
        self.assertIn("last_assignment_label", columns)


if __name__ == "__main__":
    unittest.main()
