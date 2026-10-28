import unittest

from app.services.google_sync_service import (
    _coalesce_mtg_home_elder_flag,
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

    def test_different_nonblank_remote_mtg_is_stale(self) -> None:
        local_row = {"shared_drive_revision": 5, "mtg_home_elder_flag": "1", "birthday": ""}
        remote_row = {"mtg_home_elder_flag": "2", "birthday": ""}
        self.assertTrue(_shared_contact_app_fields_stale(local_row, remote_row, 5))


class CoalesceMtgHomeElderFlagTests(unittest.TestCase):
    def test_blank_remote_keeps_local(self) -> None:
        self.assertEqual(_coalesce_mtg_home_elder_flag("1", ""), "1")
        self.assertEqual(_coalesce_mtg_home_elder_flag("2", None), "2")

    def test_nonblank_remote_wins(self) -> None:
        self.assertEqual(_coalesce_mtg_home_elder_flag("1", "2"), "2")
        self.assertEqual(_coalesce_mtg_home_elder_flag("", "1"), "1")

    def test_both_blank_stays_blank(self) -> None:
        self.assertEqual(_coalesce_mtg_home_elder_flag("", ""), "")


if __name__ == "__main__":
    unittest.main()
