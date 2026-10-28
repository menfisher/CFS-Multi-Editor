"""Owner Sync must push again when the shared group gains new members."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db
from app.services import app_data as app_data_svc


OWNER = "owner@example.com"
R1 = "r1@example.com"
RESOURCE = "contactGroups/tristate"
SHARE_ID = "share-tristate"


class RecipientSkipOwnerPushTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._prev_path = db._active_database_path
        db._active_database_path = Path(self._tmpdir.name) / "test.db"
        db.init_db()

    def tearDown(self) -> None:
        db._active_database_path = self._prev_path
        self._tmpdir.cleanup()

    def _seed_mappings(self, count: int) -> None:
        for i in range(count):
            db.upsert_sync_mapping(
                share_id=SHARE_ID,
                owner=OWNER,
                group_resource_name=RESOURCE,
                recipient_email=R1,
                owner_person_id=f"people/owner-{i}",
                recipient_person_id=f"people/recipient-{i}",
            )

    def test_skips_when_fully_mapped_and_shared(self) -> None:
        self._seed_mappings(725)
        target = {
            "status": "Shared",
            "memberCount": 725,
            "importCursor": 725,
            "actualCount": 725,
        }
        self.assertTrue(
            app_data_svc._recipient_skip_owner_push(
                target,
                share_id=SHARE_ID,
                owner=OWNER,
                resource_name=RESOURCE,
                recipient_email=R1,
                reset=False,
                owner_member_count=725,
            )
        )

    def test_does_not_skip_shared_when_owner_group_grew(self) -> None:
        self._seed_mappings(725)
        target = {
            "status": "Shared",
            "memberCount": 725,
            "importCursor": 725,
            "actualCount": 725,
        }
        self.assertFalse(
            app_data_svc._recipient_skip_owner_push(
                target,
                share_id=SHARE_ID,
                owner=OWNER,
                resource_name=RESOURCE,
                recipient_email=R1,
                reset=False,
                owner_member_count=733,
            )
        )

    def test_sync_shared_group_enqueues_when_group_grew(self) -> None:
        self._seed_mappings(725)
        db.save_app_data(
            OWNER,
            {"groups": {RESOURCE: {"shared": [R1], "shareId": SHARE_ID, "name": "TriState"}}},
        )
        db.save_app_data(
            R1,
            {
                "sharedGroups": [
                    {
                        "owner": OWNER,
                        "resourceName": RESOURCE,
                        "shareId": SHARE_ID,
                        "name": "TriState",
                        "status": "Shared",
                        "memberCount": 725,
                        "importCursor": 725,
                        "actualCount": 725,
                        "recipientEmail": R1,
                    }
                ]
            },
        )
        # Stage 733 contacts for the share_id.
        members = [
            {"resourceName": f"people/owner-{i}", "names": [{"givenName": str(i)}]}
            for i in range(733)
        ]
        db.write_shared_contacts(SHARE_ID, OWNER, "TriState", RESOURCE, members)

        member_ids = [f"people/owner-{i}" for i in range(733)]
        with (
            patch("app.services.app_data.owner_credentials", return_value=object()),
            patch("app.services.app_data.get_group_member_ids", return_value=member_ids),
            patch(
                "app.services.app_data.get_people_batch",
                return_value={mid: {"resourceName": mid} for mid in member_ids},
            ),
            patch("app.services.app_data.recipient_credentials", return_value=object()),
            patch(
                "app.services.push_worker.enqueue_full_group_push",
                return_value=1,
            ) as enqueue,
        ):
            result = app_data_svc.sync_shared_group(OWNER, RESOURCE)

        enqueue.assert_called_once()
        status = result["syncStatus"][R1]
        self.assertIn("Pushing", status)
        recipient = db.get_app_data(R1) or {}
        group = recipient["sharedGroups"][0]
        self.assertEqual(int(group["memberCount"]), 733)
        self.assertTrue(str(group["status"]).startswith("Ready") or "Pushing" in str(group["status"]))


if __name__ == "__main__":
    unittest.main()
