import unittest
from unittest.mock import patch

from app.services.google_sync_service import (
    _find_shared_contact_manifest_item,
    _shared_contact_app_fields_stale,
)


class SharedContactAppFieldsStaleTests(unittest.TestCase):
    def test_missing_local_mtg_with_remote_value_is_stale(self) -> None:
        local_row = {"shared_drive_revision": 5, "mtg_home_elder_flag": "", "birthday": ""}
        remote_row = {"mtg_home_elder_flag": "1", "birthday": ""}
        self.assertTrue(_shared_contact_app_fields_stale(local_row, remote_row, 5))

    def test_matching_fields_and_revision_are_fresh(self) -> None:
        local_row = {
            "shared_drive_revision": 5,
            "mtg_home_elder_flag": "1",
            "birthday": "1990-01-01",
            "photo": "",
            "photo_drive_file_id": "",
            "photo_sync_revision": 0,
        }
        remote_row = {
            "mtg_home_elder_flag": "1",
            "birthday": "1990-01-01",
            "photo": "",
            "photo_drive_file_id": "",
            "photo_sync_revision": 0,
        }
        self.assertFalse(_shared_contact_app_fields_stale(local_row, remote_row, 5))

    def test_local_mtg_is_not_stale_when_remote_is_blank(self) -> None:
        local_row = {
            "shared_drive_revision": 5,
            "mtg_home_elder_flag": "1",
            "birthday": "",
            "photo": "",
            "photo_drive_file_id": "",
            "photo_sync_revision": 0,
        }
        remote_row = {
            "mtg_home_elder_flag": "",
            "birthday": "",
            "photo": "",
            "photo_drive_file_id": "",
            "photo_sync_revision": 0,
        }
        self.assertFalse(_shared_contact_app_fields_stale(local_row, remote_row, 5))


class FindSharedContactManifestItemTests(unittest.TestCase):
    def test_matches_manifest_entry_by_contact_id_first(self) -> None:
        local_row = {"id": 42, "family_name": "Anderson", "given_name": "Jason"}
        manifest_entries = [
            {"id": 99, "family_name": "Other", "given_name": "Person"},
            {"id": 42, "family_name": "Anderson", "given_name": "Jason"},
        ]
        with patch("app.services.google_sync_service._load_shared_contact_row_from_drive") as load_row:
            matched = _find_shared_contact_manifest_item(
                "token",
                {"contact_records_folder_id": "folder"},
                local_row,
                manifest_entries,
            )
        self.assertEqual(int(matched["id"]), 42)
        load_row.assert_not_called()


if __name__ == "__main__":
    unittest.main()
