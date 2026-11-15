from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import db
from app.services import app_data


OWNER = "owner@example.com"
R1 = "menfisher@gmail.com"
R2 = "invited-not-connected@gmail.com"
RESOURCE = "contactGroups/abc123"
SHARE_ID = "share-abc"


class RosterStatusTests(unittest.TestCase):
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
                        "shared": [R1, R2],
                        "shareId": SHARE_ID,
                        "name": "Group A",
                    }
                }
            },
        )
        db.save_recipient_token(R1, {"token": "x", "refresh_token": "y"})
        db.upsert_sync_mapping(
            share_id=SHARE_ID,
            owner=OWNER,
            group_resource_name=RESOURCE,
            recipient_email=R1,
            owner_person_id="people/c-betty",
            recipient_person_id="people/r-betty",
        )

    def tearDown(self) -> None:
        db._active_database_path = self._prev_path
        self._tmpdir.cleanup()

    def test_roster_status_lists_connected_recipient_mappings(self) -> None:
        result = app_data.roster_status_rpc(OWNER)
        by_email = {item["email"]: item for item in result["recipients"]}
        self.assertIn(R1, by_email)
        self.assertNotIn(R2, by_email)
        self.assertEqual(by_email[R1]["mapped_count"], 1)
        self.assertEqual(by_email[R1]["owner_person_ids"], ["people/c-betty"])


if __name__ == "__main__":
    unittest.main()
