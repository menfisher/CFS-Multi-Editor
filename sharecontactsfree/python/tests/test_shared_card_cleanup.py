"""Deleting a recipient card must not leave other groups pointing at it.

One recipient card can be mapped from several groups. When a cleanup deleted it, the
other groups kept a mapping row for a card Google no longer had, and the roster check
reported "cards that Share has lost track of".
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db
from app.services import contact_sync

OWNER = "owner@example.com"
RECIPIENT = "r1@example.com"
ALMSLA = ("share-almsla", "contactGroups/almsla")
MEETING_A = ("share-a", "contactGroups/meeting-a")
CARD = "people/r-card"
OWNER_PERSON = "people/c-owner"


class FakeCreds:
    token = "fake"


class SharedCardCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._prev_path = db._active_database_path
        db._active_database_path = Path(self._tmpdir.name) / "test.db"
        db.init_db()
        db.save_app_data(
            RECIPIENT,
            {
                "sharedGroups": [
                    {
                        "owner": OWNER,
                        "resourceName": MEETING_A[1],
                        "shareId": MEETING_A[0],
                        "created": "contactGroups/recipient-meeting-a",
                    },
                    {
                        "owner": OWNER,
                        "resourceName": ALMSLA[1],
                        "shareId": ALMSLA[0],
                        "created": "contactGroups/recipient-almsla",
                    },
                ]
            },
        )
        for share_id, resource in (ALMSLA, MEETING_A):
            db.upsert_sync_mapping(
                share_id=share_id,
                owner=OWNER,
                group_resource_name=resource,
                recipient_email=RECIPIENT,
                owner_person_id=OWNER_PERSON,
                recipient_person_id=CARD,
                last_hash="h",
                last_photo_hash="p",
                owner_etag="e",
                last_assignment_label="",
            )

    def tearDown(self) -> None:
        db._active_database_path = self._prev_path
        self._tmpdir.cleanup()

    def _rows(self) -> list[dict]:
        return db.find_mappings_for_recipient_card(RECIPIENT, CARD)

    def test_deleting_a_card_drops_the_rows_of_every_group(self) -> None:
        self.assertEqual(len(self._rows()), 2)
        with (
            patch.object(contact_sync, "recipient_credentials", return_value=FakeCreds()),
            patch.object(contact_sync, "delete_contact", return_value=True),
        ):
            self.assertTrue(contact_sync._delete_recipient_person(RECIPIENT, CARD))
        self.assertEqual(self._rows(), [])

    def test_failed_delete_keeps_the_rows(self) -> None:
        with (
            patch.object(contact_sync, "recipient_credentials", return_value=FakeCreds()),
            patch.object(contact_sync, "delete_contact", return_value=False),
        ):
            self.assertFalse(contact_sync._delete_recipient_person(RECIPIENT, CARD))
        self.assertEqual(len(self._rows()), 2)

    def test_orphan_prune_keeps_a_card_another_group_still_uses(self) -> None:
        removed_from: list[tuple[str, str]] = []
        with (
            patch.object(contact_sync, "recipient_credentials", return_value=FakeCreds()),
            patch.object(contact_sync, "delete_contact") as delete,
            patch.object(
                contact_sync,
                "remove_contact_from_group",
                side_effect=lambda creds, rid, gid: removed_from.append((rid, gid)) or True,
            ),
        ):
            result = contact_sync.prune_orphan_recipient_contacts(
                share_id=MEETING_A[0],
                owner=OWNER,
                group_resource_name=MEETING_A[1],
                recipient_email=RECIPIENT,
                current_owner_person_ids=set(),
            )
        delete.assert_not_called()
        self.assertEqual(removed_from, [(CARD, "contactGroups/recipient-meeting-a")])
        self.assertEqual(result["deleted"], 1)
        remaining = self._rows()
        self.assertEqual([(r["share_id"]) for r in remaining], [ALMSLA[0]])

    def test_orphan_prune_deletes_a_card_nobody_else_uses(self) -> None:
        db.delete_sync_mapping_rows(ALMSLA[0], OWNER, ALMSLA[1], RECIPIENT)
        with (
            patch.object(contact_sync, "recipient_credentials", return_value=FakeCreds()),
            patch.object(contact_sync, "delete_contact", return_value=True) as delete,
            patch.object(contact_sync, "remove_contact_from_group") as remove,
        ):
            result = contact_sync.prune_orphan_recipient_contacts(
                share_id=MEETING_A[0],
                owner=OWNER,
                group_resource_name=MEETING_A[1],
                recipient_email=RECIPIENT,
                current_owner_person_ids=set(),
            )
        delete.assert_called_once()
        remove.assert_not_called()
        self.assertEqual(result["deleted"], 1)
        self.assertEqual(self._rows(), [])

    def test_orphan_prune_does_not_delete_when_group_label_is_unknown(self) -> None:
        db.save_app_data(RECIPIENT, {"sharedGroups": []})
        with (
            patch.object(contact_sync, "recipient_credentials", return_value=FakeCreds()),
            patch.object(contact_sync, "delete_contact") as delete,
            patch.object(contact_sync, "remove_contact_from_group") as remove,
        ):
            result = contact_sync.prune_orphan_recipient_contacts(
                share_id=MEETING_A[0],
                owner=OWNER,
                group_resource_name=MEETING_A[1],
                recipient_email=RECIPIENT,
                current_owner_person_ids=set(),
            )
        delete.assert_not_called()
        remove.assert_not_called()
        self.assertEqual(result["failed"], 1)
        self.assertEqual(len(self._rows()), 2)


if __name__ == "__main__":
    unittest.main()
