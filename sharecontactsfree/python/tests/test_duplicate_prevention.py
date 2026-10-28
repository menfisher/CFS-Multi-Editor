"""Re-running an import must claim existing contacts, not create second copies.

A create can succeed at Google and be lost before we record it (request killed,
batch error after a partial write). Re-creating those left the recipient's
shared group with hundreds of duplicates.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, patch

from app import db
from app.services.recipient_import import (
    _contact_match_keys,
    adopt_untracked_group_members,
)

OWNER = "owner@example.com"
RECIPIENT = "friend@example.com"
RESOURCE = "contactGroups/g1"
GROUP_ID = "contactGroups/recipient1"
SHARE_ID = "share-dupes"


def _member(idx: int) -> dict:
    return {
        "resourceName": f"people/{idx}",
        "names": [{"displayName": f"Person {idx}"}],
        "emailAddresses": [{"value": f"p{idx}@example.com"}],
        "phoneNumbers": [{"value": f"555-010-{idx:04d}"}],
    }


def _recipient_person(idx: int) -> dict:
    person = _member(idx)
    person["resourceName"] = f"people/r{idx}"
    return person


class DuplicatePreventionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._prev = db._active_database_path
        db._active_database_path = Path(self._tmpdir.name) / "test.db"
        db.init_db()
        self.group = {
            "shareId": SHARE_ID,
            "owner": OWNER,
            "resourceName": RESOURCE,
            "recipientEmail": RECIPIENT,
            "created": GROUP_ID,
        }

    def tearDown(self) -> None:
        db._active_database_path = self._prev
        self._tmpdir.cleanup()

    def _adopt(self, members, group_members, people, *, existing=None):
        # Second call checks whether unmatched records point at live contacts.
        side_effects = [people, existing if existing is not None else {}]

        with (
            patch(
                "app.services.recipient_import.get_group_details",
                return_value={"memberResourceNames": group_members},
            ),
            patch(
                "app.services.recipient_import.get_people_contacts_batch",
                side_effect=side_effects,
            ),
        ):
            return adopt_untracked_group_members(
                object(), self.group, GROUP_ID, members
            )

    def test_existing_contacts_are_claimed_instead_of_recreated(self) -> None:
        members = [_member(i) for i in range(3)]
        group_members = [f"people/r{i}" for i in range(3)]
        people = {f"people/r{i}": _recipient_person(i) for i in range(3)}

        self.assertEqual(self._adopt(members, group_members, people), 3)

        by_owner, _ = db.load_sync_mappings(SHARE_ID, OWNER, RESOURCE, RECIPIENT)
        self.assertEqual(len(by_owner), 3)
        self.assertEqual(
            by_owner["people/1"]["recipient_person_id"], "people/r1"
        )

    def test_already_mapped_contacts_are_left_alone(self) -> None:
        members = [_member(i) for i in range(3)]
        db.upsert_sync_mapping(
            share_id=SHARE_ID,
            owner=OWNER,
            group_resource_name=RESOURCE,
            recipient_email=RECIPIENT,
            owner_person_id="people/0",
            recipient_person_id="people/r0",
            last_hash="h",
            last_photo_hash="",
            owner_etag="",
        )
        group_members = [f"people/r{i}" for i in range(3)]
        people = {f"people/r{i}": _recipient_person(i) for i in range(3)}

        # Only the two unmapped contacts are adopted.
        self.assertEqual(self._adopt(members, group_members, people), 2)

    def test_one_recipient_contact_is_never_claimed_twice(self) -> None:
        # Two staged contacts share a name but only one copy exists in Google.
        members = [_member(0), _member(0)]
        members[1] = dict(members[1], resourceName="people/99")
        people = {"people/r0": _recipient_person(0)}

        self.assertEqual(self._adopt(members, ["people/r0"], people), 1)

    def test_unrelated_contacts_are_not_claimed(self) -> None:
        members = [_member(0)]
        other = _recipient_person(7)
        self.assertEqual(self._adopt(members, ["people/r7"], {"people/r7": other}), 0)

    def test_mapping_to_a_deleted_contact_is_repointed_at_the_surviving_copy(
        self,
    ) -> None:
        # The recipient contact we recorded was deleted, but a copy of that
        # person is still in the group. Claim the copy instead of losing them.
        members = [_member(0)]
        db.upsert_sync_mapping(
            share_id=SHARE_ID,
            owner=OWNER,
            group_resource_name=RESOURCE,
            recipient_email=RECIPIENT,
            owner_person_id="people/0",
            recipient_person_id="people/deleted",
            last_hash="h",
            last_photo_hash="",
            owner_etag="",
        )
        people = {"people/r0": _recipient_person(0)}

        self.assertEqual(self._adopt(members, ["people/r0"], people), 1)

        by_owner, _ = db.load_sync_mappings(SHARE_ID, OWNER, RESOURCE, RECIPIENT)
        self.assertEqual(
            by_owner["people/0"]["recipient_person_id"], "people/r0"
        )

    def test_a_live_mapping_is_not_repointed(self) -> None:
        members = [_member(0)]
        db.upsert_sync_mapping(
            share_id=SHARE_ID,
            owner=OWNER,
            group_resource_name=RESOURCE,
            recipient_email=RECIPIENT,
            owner_person_id="people/0",
            recipient_person_id="people/r0",
            last_hash="h",
            last_photo_hash="",
            owner_etag="",
        )
        # A stray extra copy exists, but the mapping is healthy.
        people = {"people/extra": _recipient_person(0)}
        self.assertEqual(
            self._adopt(members, ["people/r0", "people/extra"], people), 0
        )

    def test_record_for_a_deleted_contact_with_no_copy_is_cleared(self) -> None:
        # Nothing survived, so the record must be dropped or the contact can
        # never be re-created.
        members = [_member(0)]
        db.upsert_sync_mapping(
            share_id=SHARE_ID,
            owner=OWNER,
            group_resource_name=RESOURCE,
            recipient_email=RECIPIENT,
            owner_person_id="people/0",
            recipient_person_id="people/deleted",
            last_hash="h",
            last_photo_hash="",
            owner_etag="",
        )
        # An unrelated contact is in the group; no copy of Person 0 exists.
        other = _recipient_person(9)
        self.assertEqual(
            self._adopt(members, ["people/r9"], {"people/r9": other}, existing={}), 0
        )

        by_owner, _ = db.load_sync_mappings(SHARE_ID, OWNER, RESOURCE, RECIPIENT)
        self.assertNotIn("people/0", by_owner)

    def test_record_is_kept_when_the_contact_still_exists(self) -> None:
        # Contact exists but sits outside the group; keep the record rather
        # than re-creating and making a duplicate.
        members = [_member(0)]
        db.upsert_sync_mapping(
            share_id=SHARE_ID,
            owner=OWNER,
            group_resource_name=RESOURCE,
            recipient_email=RECIPIENT,
            owner_person_id="people/0",
            recipient_person_id="people/outside",
            last_hash="h",
            last_photo_hash="",
            owner_etag="",
        )
        other = _recipient_person(9)
        self.assertEqual(
            self._adopt(
                members,
                ["people/r9"],
                {"people/r9": other},
                existing={"people/outside": _recipient_person(0)},
            ),
            0,
        )

        by_owner, _ = db.load_sync_mappings(SHARE_ID, OWNER, RESOURCE, RECIPIENT)
        self.assertIn("people/0", by_owner)

    def test_match_keys_use_email_phone_and_name(self) -> None:
        keys = _contact_match_keys(_member(5))
        self.assertIn("email:p5@example.com", keys)
        self.assertIn("name:person 5", keys)
        self.assertTrue(any(k.startswith("phone:") for k in keys))
        self.assertEqual(_contact_match_keys(None), set())

    def test_second_shared_group_reuses_existing_recipient_contact(self) -> None:
        from app.services.recipient_import import _existing_recipient_person_id

        db.upsert_sync_mapping(
            share_id="share-almsla",
            owner=OWNER,
            group_resource_name="contactGroups/almsla",
            recipient_email=RECIPIENT,
            owner_person_id="people/42",
            recipient_person_id="people/already-there",
            last_hash="h",
            last_photo_hash="photo-h",
            owner_etag="",
        )
        group = {
            "shareId": "share-meeting",
            "owner": OWNER,
            "resourceName": "contactGroups/meeting",
            "recipientEmail": RECIPIENT,
        }
        self.assertEqual(
            _existing_recipient_person_id(group, "people/42"),
            "people/already-there",
        )
        self.assertEqual(_existing_recipient_person_id(group, "people/missing"), "")
        self.assertEqual(
            _existing_recipient_person_id(
                {
                    "shareId": "share-almsla",
                    "owner": OWNER,
                    "resourceName": "contactGroups/almsla",
                    "recipientEmail": RECIPIENT,
                },
                "people/42",
            ),
            "",
        )

    def test_meeting_group_import_adds_label_instead_of_creating(self) -> None:
        from app.services.recipient_import import (
            _hydrate_photo_targets,
            _import_contacts_batch,
        )

        db.upsert_sync_mapping(
            share_id="share-almsla",
            owner=OWNER,
            group_resource_name="contactGroups/almsla",
            recipient_email=RECIPIENT,
            owner_person_id="people/0",
            recipient_person_id="people/already-there",
            last_hash="h",
            last_photo_hash="photo-h",
            owner_etag="",
        )
        members = [_member(0)]
        members[0]["photos"] = [
            {"url": "https://example.com/p0.jpg", "default": False}
        ]
        meeting = {
            "shareId": "share-meeting",
            "owner": OWNER,
            "resourceName": "contactGroups/meeting",
            "recipientEmail": RECIPIENT,
            "created": GROUP_ID,
        }
        photo_pending: list[int] = []
        photo_targets: dict[str, str] = {}
        with (
            patch(
                "app.services.recipient_import.ensure_contact_in_group",
                return_value=True,
            ) as attach,
            patch(
                "app.services.recipient_import.batch_create_contacts_in_group",
                return_value={},
            ) as create,
        ):
            cursor, used, failed = _import_contacts_batch(
                object(),
                members,
                GROUP_ID,
                0,
                10,
                retry_indexes=[],
                retry_attempts={},
                skipped_indexes=[],
                photo_pending=photo_pending,
                photo_targets=photo_targets,
                over_budget=lambda: False,
                group_snapshot=meeting,
            )
        attach.assert_called_once_with(ANY, "people/already-there", GROUP_ID)
        create.assert_not_called()
        self.assertEqual((cursor, used, failed), (1, 1, 0))
        self.assertEqual(photo_pending, [])
        by_owner, _ = db.load_sync_mappings(
            "share-meeting", OWNER, "contactGroups/meeting", RECIPIENT
        )
        self.assertEqual(
            by_owner["people/0"]["recipient_person_id"], "people/already-there"
        )
        self.assertEqual(by_owner["people/0"]["last_photo_hash"], "photo-h")
        self.assertEqual(_hydrate_photo_targets(meeting, members, {}), {})


if __name__ == "__main__":
    unittest.main()
