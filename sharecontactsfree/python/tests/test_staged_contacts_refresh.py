"""Staged invite snapshots must include owner edits made before recipient connects."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db
from app.services import app_data as app_data_svc


OWNER = "owner@example.com"
R1 = "r1@example.com"
RESOURCE = "contactGroups/g1"
SHARE_ID = "share-1"


class StagedContactsRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._prev_path = db._active_database_path
        db._active_database_path = Path(self._tmpdir.name) / "test.db"
        db.init_db()

    def tearDown(self) -> None:
        db._active_database_path = self._prev_path
        self._tmpdir.cleanup()

    def test_patch_updates_staged_member(self) -> None:
        db.write_shared_contacts(
            SHARE_ID,
            OWNER,
            "Group",
            RESOURCE,
            [
                {"resourceName": "people/1", "names": [{"familyName": "Old"}]},
                {"resourceName": "people/2", "names": [{"familyName": "Two"}]},
            ],
        )
        self.assertTrue(
            db.patch_shared_contact_member(
                SHARE_ID,
                "people/1",
                {"resourceName": "people/1", "names": [{"familyName": "New"}]},
            )
        )
        members = db.read_shared_contacts(SHARE_ID)
        self.assertEqual(members[0]["names"][0]["familyName"], "New")
        self.assertEqual(members[1]["names"][0]["familyName"], "Two")

    def test_refresh_before_first_import_replaces_stale_snapshot(self) -> None:
        db.write_shared_contacts(
            SHARE_ID,
            OWNER,
            "Group",
            RESOURCE,
            [{"resourceName": "people/1", "names": [{"familyName": "Old"}]}],
        )
        db.save_app_data(
            R1,
            {
                "sharedGroups": [
                    {
                        "owner": OWNER,
                        "resourceName": RESOURCE,
                        "shareId": SHARE_ID,
                        "name": "Group",
                        "status": "Ready",
                        "importCursor": 0,
                        "memberCount": 1,
                    }
                ]
            },
        )
        live = [
            {"resourceName": "people/1", "names": [{"familyName": "Live"}]},
            {"resourceName": "people/2", "names": [{"familyName": "Added"}]},
        ]

        class FakeCreds:
            token = "t"

        with patch("app.services.app_data.owner_credentials", return_value=FakeCreds()), patch(
            "app.services.app_data.get_group_member_ids",
            return_value=["people/1", "people/2"],
        ), patch(
            "app.services.app_data.get_people_batch",
            return_value={item["resourceName"]: item for item in live},
        ):
            refreshed = app_data_svc.maybe_refresh_staged_shared_contacts_before_import(
                recipient_email=R1,
                owner_email=OWNER,
                resource_name=RESOURCE,
            )
        self.assertTrue(refreshed)
        members = db.read_shared_contacts(SHARE_ID)
        self.assertEqual(len(members), 2)
        self.assertEqual(members[0]["names"][0]["familyName"], "Live")

    def test_refresh_skipped_after_import_mappings_exist(self) -> None:
        db.write_shared_contacts(
            SHARE_ID,
            OWNER,
            "Group",
            RESOURCE,
            [{"resourceName": "people/1", "names": [{"familyName": "Old"}]}],
        )
        db.save_app_data(
            R1,
            {
                "sharedGroups": [
                    {
                        "owner": OWNER,
                        "resourceName": RESOURCE,
                        "shareId": SHARE_ID,
                        "name": "Group",
                        "status": "Ready",
                        "importCursor": 0,
                        "memberCount": 1,
                    }
                ]
            },
        )
        db.upsert_sync_mapping(
            share_id=SHARE_ID,
            owner=OWNER,
            group_resource_name=RESOURCE,
            recipient_email=R1,
            owner_person_id="people/1",
            recipient_person_id="people/r1",
        )
        with patch("app.services.app_data.owner_credentials") as mock_creds:
            refreshed = app_data_svc.maybe_refresh_staged_shared_contacts_before_import(
                recipient_email=R1,
                owner_email=OWNER,
                resource_name=RESOURCE,
            )
        self.assertFalse(refreshed)
        mock_creds.assert_not_called()


if __name__ == "__main__":
    unittest.main()
