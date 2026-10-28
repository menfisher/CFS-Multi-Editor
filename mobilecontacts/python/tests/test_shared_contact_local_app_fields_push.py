import unittest

from app.services.google_sync_service import (
    _shared_contact_local_app_fields_should_push,
)


class SharedContactLocalAppFieldsPushTests(unittest.TestCase):
    def test_push_when_local_mtg_set_and_remote_empty(self) -> None:
        local = {"mtg_home_elder_flag": "1", "birthday": ""}
        remote = {"mtg_home_elder_flag": "", "birthday": ""}
        self.assertTrue(_shared_contact_local_app_fields_should_push(local, remote))


if __name__ == "__main__":
    unittest.main()
