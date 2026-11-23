import unittest

from app.services.google_sync_service import _drive_removals_look_unsafe


class DriveRemovalGuardTests(unittest.TestCase):
    def test_a_few_removals_by_another_editor_are_allowed(self) -> None:
        self.assertFalse(_drive_removals_look_unsafe(729, 3))
        self.assertFalse(_drive_removals_look_unsafe(729, 19))

    def test_mass_removal_from_a_different_id_space_is_refused(self) -> None:
        # Nov 22: a phone-app manifest overlapped on 7 ids, so 722 of 729 looked removed.
        self.assertTrue(_drive_removals_look_unsafe(729, 722))
        self.assertTrue(_drive_removals_look_unsafe(729, 200))

    def test_small_books_are_not_blocked(self) -> None:
        self.assertFalse(_drive_removals_look_unsafe(10, 8))


if __name__ == "__main__":
    unittest.main()
