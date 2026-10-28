import unittest

from app.services.google_sync_service import (
    _shared_contact_local_app_fields_should_push,
)


class SharedContactLocalAppFieldsPushTests(unittest.TestCase):
    def test_push_when_local_mtg_set_and_remote_empty(self) -> None:
        local = {"mtg_home_elder_flag": "1", "birthday": ""}
        remote = {"mtg_home_elder_flag": "", "birthday": ""}
        self.assertTrue(_shared_contact_local_app_fields_should_push(local, remote))

    def test_no_push_when_remote_has_value_and_local_empty(self) -> None:
        local = {"mtg_home_elder_flag": "", "birthday": ""}
        remote = {"mtg_home_elder_flag": "1", "birthday": ""}
        self.assertFalse(_shared_contact_local_app_fields_should_push(local, remote))

    def test_no_push_when_values_match(self) -> None:
        local = {"mtg_home_elder_flag": "1", "birthday": "1990-01-02"}
        remote = {"mtg_home_elder_flag": "1", "birthday": "1990-01-02"}
        self.assertFalse(_shared_contact_local_app_fields_should_push(local, remote))

    def test_push_when_local_birthday_set_and_remote_empty(self) -> None:
        local = {"mtg_home_elder_flag": "", "birthday": "03-15"}
        remote = {"mtg_home_elder_flag": "", "birthday": ""}
        self.assertTrue(_shared_contact_local_app_fields_should_push(local, remote))

    def test_push_when_local_meets_in_state_set_and_remote_empty(self) -> None:
        local = {"mtg_home_elder_flag": "", "birthday": "", "meets_in_state": "MS"}
        remote = {"mtg_home_elder_flag": "", "birthday": "", "meets_in_state": ""}
        self.assertTrue(_shared_contact_local_app_fields_should_push(local, remote))


if __name__ == "__main__":
    unittest.main()
