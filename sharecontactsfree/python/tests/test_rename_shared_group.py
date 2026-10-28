"""In-place rename of one shared group label across connected recipients."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db
from app.services import app_data as app_data_svc


OWNER = "owner@example.com"
R1 = "r1@example.com"
R2 = "r2@example.com"
RESOURCE = "contactGroups/abc123"
CREATED_1 = "contactGroups/r1-group"
CREATED_2 = "contactGroups/r2-group"


class RenameSharedGroupTests(unittest.TestCase):
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
                        "shareId": "share-abc",
                        "name": "TriState Separates (Shared)",
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
                        "shareId": "share-abc",
                        "name": "TriState Separates (Shared)",
                        "created": CREATED_1,
                    }
                ]
            },
        )
        db.save_app_data(
            R2,
            {
                "sharedGroups": [
                    {
                        "owner": OWNER,
                        "resourceName": RESOURCE,
                        "shareId": "share-abc",
                        "name": "TriState Separates (Shared)",
                        "created": CREATED_2,
                    }
                ]
            },
        )

    def tearDown(self) -> None:
        db._active_database_path = self._prev_path
        self._tmpdir.cleanup()

    def test_rename_updates_stored_names_and_recipient_google_groups(self) -> None:
        renamed: list[tuple[str, str]] = []

        def fake_update(_creds, resource_name, group_name):
            renamed.append((resource_name, group_name))
            return resource_name

        with patch("app.services.app_data.recipient_credentials", return_value=object()), patch(
            "app.people_recipient.update_contact_group_name",
            side_effect=fake_update,
        ):
            summary = app_data_svc.rename_shared_group_rpc(
                OWNER,
                RESOURCE,
                "TriState Separates (Shared)",
                "AlMsLa (Shared)",
            )

        self.assertTrue(summary["ok"])
        self.assertEqual(summary["recipients_updated"], 2)
        self.assertEqual(summary["google_renamed"], 2)
        self.assertEqual(
            renamed,
            [(CREATED_1, "AlMsLa (Shared)"), (CREATED_2, "AlMsLa (Shared)")],
        )
        owner_data = db.get_app_data(OWNER) or {}
        self.assertEqual(owner_data["groups"][RESOURCE]["name"], "AlMsLa (Shared)")
        self.assertEqual(owner_data["sharedContactsGroupName"], "AlMsLa (Shared)")
        self.assertEqual(owner_data["sharedContactsGroupResourceName"], RESOURCE)
        self.assertEqual(
            (db.get_app_data(R1) or {})["sharedGroups"][0]["name"],
            "AlMsLa (Shared)",
        )
        self.assertEqual(
            (db.get_app_data(R2) or {})["sharedGroups"][0]["name"],
            "AlMsLa (Shared)",
        )

    def test_rename_does_not_update_unconnected_recipient_google_group(self) -> None:
        with patch("app.services.app_data.recipient_credentials", return_value=None), patch(
            "app.people_recipient.update_contact_group_name"
        ) as update_name:
            summary = app_data_svc.rename_shared_group_rpc(
                OWNER,
                RESOURCE,
                "TriState Separates (Shared)",
                "AlMsLa (Shared)",
            )

        update_name.assert_not_called()
        self.assertEqual(summary["skipped"], 2)
        self.assertEqual(
            (db.get_app_data(R1) or {})["sharedGroups"][0]["name"],
            "TriState Separates (Shared)",
        )

    def test_same_name_saves_display_without_renaming_recipients(self) -> None:
        with patch("app.services.app_data.recipient_credentials", return_value=object()), patch(
            "app.people_recipient.update_contact_group_name"
        ) as update_name:
            summary = app_data_svc.rename_shared_group_rpc(
                OWNER,
                RESOURCE,
                "AlMsLa (Shared)",
                "AlMsLa (Shared)",
            )

        update_name.assert_not_called()
        self.assertTrue(summary["ok"])
        self.assertTrue(summary.get("display_name_saved"))
        self.assertEqual(summary["recipients_updated"], 0)
        owner_data = db.get_app_data(OWNER) or {}
        self.assertEqual(owner_data["sharedContactsGroupName"], "AlMsLa (Shared)")
        self.assertEqual(
            (db.get_app_data(R1) or {})["sharedGroups"][0]["name"],
            "TriState Separates (Shared)",
        )


if __name__ == "__main__":
    unittest.main()
