"""Relink recipient cards on Google ID churn; prune leftovers that left the share."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db
from app.services import contact_sync
from app.services.contact_hash import compute_contact_hash, compute_photo_hash


OWNER = "owner@example.com"
R1 = "r1@example.com"
RESOURCE = "contactGroups/abc123"
SHARE_ID = "share-abc"
OLD_PERSON = "people/c-old"
NEW_PERSON = "people/c-new"
RECIPIENT_PERSON = "people/r-existing"
EXTRA_PERSON = "people/r-extra"


class FakeCreds:
    token = "fake-token"


def _person(resource_name: str, given: str, family: str, phone: str = "5551112222") -> dict:
    return {
        "resourceName": resource_name,
        "etag": f"etag-{resource_name}",
        "names": [{"givenName": given, "familyName": family, "displayName": f"{given} {family}"}],
        "phoneNumbers": [{"value": phone}],
        "memberships": [
            {"contactGroupMembership": {"contactGroupResourceName": RESOURCE}}
        ],
    }


class RelinkPruneTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._prev_path = db._active_database_path
        db._active_database_path = Path(self._tmpdir.name) / "test.db"
        db.init_db()
        db.save_app_data(
            OWNER,
            {
                "groups": {
                    RESOURCE: {
                        "shared": [R1],
                        "shareId": SHARE_ID,
                        "name": "AlMsLa",
                    }
                }
            },
        )
        db.save_app_data(
            R1,
            {
                "sharedGroups": [
                    {
                        "owner": OWNER,
                        "resourceName": RESOURCE,
                        "shareId": SHARE_ID,
                        "name": "AlMsLa",
                        "googleGroupId": "contactGroups/recipient-group",
                    }
                ]
            },
        )
        db.write_shared_contacts(
            SHARE_ID,
            OWNER,
            "AlMsLa",
            RESOURCE,
            [_person(OLD_PERSON, "Cindy", "Douglas")],
        )
        db.upsert_sync_mapping(
            share_id=SHARE_ID,
            owner=OWNER,
            group_resource_name=RESOURCE,
            recipient_email=R1,
            owner_person_id=OLD_PERSON,
            recipient_person_id=RECIPIENT_PERSON,
            last_hash="old-hash",
            last_photo_hash="old-photo",
            owner_etag="old-etag",
        )

    def tearDown(self) -> None:
        db._active_database_path = self._prev_path
        self._tmpdir.cleanup()

    def test_id_churn_relinks_existing_recipient_card(self) -> None:
        member_data = _person(NEW_PERSON, "Cindy", "Douglas")
        with (
            patch("app.services.contact_sync.owner_credentials", return_value=FakeCreds()),
            patch(
                "app.services.contact_sync.get_people_batch",
                return_value={NEW_PERSON: member_data},
            ),
            patch(
                "app.services.contact_sync.recipient_credentials",
                return_value=FakeCreds(),
            ),
            patch(
                "app.services.contact_sync.get_person_etags_batch",
                return_value={RECIPIENT_PERSON: "etag-r"},
            ),
            patch("app.services.contact_sync.update_contact", return_value=True) as update_mock,
            patch("app.services.contact_sync.create_contact_in_group") as create_mock,
            patch("app.services.contact_sync.ensure_assignment_contact_label"),
            patch("app.services.contact_sync.ensure_contact_in_group"),
            patch("app.services.contact_sync.set_contact_photo", return_value=True),
            patch("app.services.contact_sync.delete_contact_photo", return_value=True),
        ):
            summary = contact_sync.sync_contact_changes(OWNER, [NEW_PERSON])

        self.assertEqual(summary["created"], 0)
        self.assertEqual(summary["updated"], 1)
        self.assertEqual(summary["relinked"], 1)
        self.assertEqual(summary["pruned"], 0)
        create_mock.assert_not_called()
        update_mock.assert_called_once()
        self.assertEqual(db.find_mappings_for_owner_person(OWNER, OLD_PERSON), [])
        new_mappings = db.find_mappings_for_owner_person(OWNER, NEW_PERSON)
        self.assertEqual(len(new_mappings), 1)
        self.assertEqual(new_mappings[0]["recipient_person_id"], RECIPIENT_PERSON)
        staged = db.read_shared_contacts(SHARE_ID)
        self.assertEqual(len(staged), 1)
        self.assertEqual(staged[0]["resourceName"], NEW_PERSON)

    def test_duplicate_new_card_is_pruned_when_relinking(self) -> None:
        db.upsert_sync_mapping(
            share_id=SHARE_ID,
            owner=OWNER,
            group_resource_name=RESOURCE,
            recipient_email=R1,
            owner_person_id=NEW_PERSON,
            recipient_person_id=EXTRA_PERSON,
            last_hash="new-hash",
            last_photo_hash="new-photo",
            owner_etag="new-etag",
        )
        member_data = _person(NEW_PERSON, "Cindy", "Douglas")
        with (
            patch("app.services.contact_sync.owner_credentials", return_value=FakeCreds()),
            patch(
                "app.services.contact_sync.get_people_batch",
                return_value={NEW_PERSON: member_data},
            ),
            patch(
                "app.services.contact_sync.recipient_credentials",
                return_value=FakeCreds(),
            ),
            patch(
                "app.services.contact_sync.get_person_etags_batch",
                return_value={RECIPIENT_PERSON: "etag-r"},
            ),
            patch("app.services.contact_sync.update_contact", return_value=True),
            patch("app.services.contact_sync.delete_contact", return_value=True) as delete_mock,
            patch("app.services.contact_sync.create_contact_in_group") as create_mock,
            patch("app.services.contact_sync.ensure_assignment_contact_label"),
            patch("app.services.contact_sync.set_contact_photo", return_value=True),
            patch("app.services.contact_sync.delete_contact_photo", return_value=True),
        ):
            summary = contact_sync.sync_contact_changes(OWNER, [NEW_PERSON])

        self.assertEqual(summary["created"], 0)
        self.assertEqual(summary["relinked"], 1)
        create_mock.assert_not_called()
        delete_mock.assert_called_with(unittest.mock.ANY, EXTRA_PERSON)
        new_mappings = db.find_mappings_for_owner_person(OWNER, NEW_PERSON)
        self.assertEqual(len(new_mappings), 1)
        self.assertEqual(new_mappings[0]["recipient_person_id"], RECIPIENT_PERSON)

    def test_deleted_assignment_updates_existing_card(self) -> None:
        member_data = _person(NEW_PERSON, "Cindy", "Douglas")
        member_data["organizations"] = [
            {"title": "Deleted", "name": "2026", "metadata": {"primary": True}}
        ]
        with (
            patch("app.services.contact_sync.owner_credentials", return_value=FakeCreds()),
            patch(
                "app.services.contact_sync.get_people_batch",
                return_value={NEW_PERSON: member_data},
            ),
            patch(
                "app.services.contact_sync.recipient_credentials",
                return_value=FakeCreds(),
            ),
            patch(
                "app.services.contact_sync.get_person_etags_batch",
                return_value={RECIPIENT_PERSON: "etag-r"},
            ),
            patch("app.services.contact_sync.update_contact", return_value=True) as update_mock,
            patch("app.services.contact_sync.delete_contact") as delete_mock,
            patch("app.services.contact_sync.create_contact_in_group") as create_mock,
            patch("app.services.contact_sync.ensure_assignment_contact_label"),
            patch("app.services.contact_sync.set_contact_photo", return_value=True),
            patch("app.services.contact_sync.delete_contact_photo", return_value=True),
        ):
            summary = contact_sync.sync_contact_changes(OWNER, [NEW_PERSON])

        self.assertEqual(summary["created"], 0)
        self.assertEqual(summary["pruned"], 0)
        self.assertEqual(summary["updated"], 1)
        self.assertEqual(summary["relinked"], 1)
        create_mock.assert_not_called()
        delete_mock.assert_not_called()
        update_mock.assert_called_once()
        new_mappings = db.find_mappings_for_owner_person(OWNER, NEW_PERSON)
        self.assertEqual(len(new_mappings), 1)
        self.assertEqual(new_mappings[0]["recipient_person_id"], RECIPIENT_PERSON)

    def test_same_name_different_phone_does_not_relink(self) -> None:
        member_data = _person(NEW_PERSON, "Cindy", "Douglas", phone="5559998888")
        with (
            patch("app.services.contact_sync.owner_credentials", return_value=FakeCreds()),
            patch(
                "app.services.contact_sync.get_people_batch",
                return_value={NEW_PERSON: member_data},
            ),
            patch(
                "app.services.contact_sync.recipient_credentials",
                return_value=FakeCreds(),
            ),
            patch(
                "app.services.contact_sync._ensure_recipient_group",
                return_value="contactGroups/recipient-group",
            ),
            patch(
                "app.services.contact_sync.create_contact_in_group",
                return_value="people/r-new",
            ) as create_mock,
            patch("app.services.contact_sync.ensure_assignment_contact_label"),
            patch("app.services.contact_sync.ensure_contact_in_group"),
            patch("app.services.contact_sync.set_contact_photo", return_value=True),
            patch("app.services.contact_sync.delete_contact_photo", return_value=True),
        ):
            summary = contact_sync.sync_contact_changes(OWNER, [NEW_PERSON])

        self.assertEqual(summary["created"], 1)
        self.assertEqual(summary["relinked"], 0)
        create_mock.assert_called_once()
        self.assertEqual(len(db.find_mappings_for_owner_person(OWNER, OLD_PERSON)), 1)
        self.assertEqual(len(db.find_mappings_for_owner_person(OWNER, NEW_PERSON)), 1)

    def test_missing_owner_person_prunes_recipient_card(self) -> None:
        with (
            patch("app.services.contact_sync.owner_credentials", return_value=FakeCreds()),
            patch("app.services.contact_sync.get_people_batch", return_value={}),
            patch(
                "app.services.contact_sync.recipient_credentials",
                return_value=FakeCreds(),
            ),
            patch("app.services.contact_sync.delete_contact", return_value=True) as delete_mock,
        ):
            summary = contact_sync.sync_contact_changes(OWNER, [OLD_PERSON])

        self.assertEqual(summary["pruned"], 1)
        self.assertEqual(summary["errors"], 0)
        delete_mock.assert_called_once_with(unittest.mock.ANY, RECIPIENT_PERSON)
        self.assertEqual(db.find_mappings_for_owner_person(OWNER, OLD_PERSON), [])
        self.assertEqual(db.read_shared_contacts(SHARE_ID), [])

    def test_missing_recipient_photo_is_pushed_even_when_hash_matches(self) -> None:
        member_data = _person(OLD_PERSON, "Betty", "Miller")
        member_data["photos"] = [
            {
                "url": "https://example.com/betty.jpg",
                "metadata": {"primary": True, "source": {"type": "CONTACT"}},
            }
        ]
        photo_hash = compute_photo_hash(member_data)
        contact_hash = compute_contact_hash(member_data)
        db.upsert_sync_mapping(
            share_id=SHARE_ID,
            owner=OWNER,
            group_resource_name=RESOURCE,
            recipient_email=R1,
            owner_person_id=OLD_PERSON,
            recipient_person_id=RECIPIENT_PERSON,
            last_hash=contact_hash,
            last_photo_hash=photo_hash,
            owner_etag=member_data["etag"],
        )
        with (
            patch("app.services.contact_sync.owner_credentials", return_value=FakeCreds()),
            patch(
                "app.services.contact_sync.get_people_batch",
                return_value={OLD_PERSON: member_data},
            ),
            patch(
                "app.services.contact_sync.recipient_credentials",
                return_value=FakeCreds(),
            ),
            patch(
                "app.services.contact_sync._load_recipient_person",
                return_value={
                    "etag": "etag-r",
                    "photos": [
                        {
                            "url": "https://lh3.googleusercontent.com/a/default-user",
                            "metadata": {"default": True, "primary": True},
                        }
                    ],
                },
            ),
            patch("app.services.contact_sync.update_contact") as update_mock,
            patch("app.services.contact_sync.set_contact_photo", return_value=True) as photo_mock,
            patch("app.services.contact_sync.ensure_assignment_contact_label"),
            patch("app.services.contact_sync.remove_stale_assignment_label"),
        ):
            summary = contact_sync.sync_contact_changes(OWNER, [OLD_PERSON])

        self.assertEqual(summary["updated"], 1)
        update_mock.assert_not_called()
        photo_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
