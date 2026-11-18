import unittest
from unittest.mock import patch

from app.services import contact_service
from app.services.contact_service import _next_mtg_home_elder_cleared_at


class MtgHomeElderClearMarkerTests(unittest.TestCase):
    def test_setting_a_code_drops_the_marker(self) -> None:
        self.assertEqual(_next_mtg_home_elder_cleared_at(1, "", "1"), "")
        self.assertEqual(_next_mtg_home_elder_cleared_at(1, "1", "2"), "")

    def test_clearing_an_existing_code_stamps_the_marker(self) -> None:
        marker = _next_mtg_home_elder_cleared_at(1, "1", "")
        self.assertTrue(marker)
        self.assertIn("T", marker)

    def test_staying_blank_keeps_whatever_marker_is_stored(self) -> None:
        with patch.object(
            contact_service,
            "fetch_one",
            return_value={"mtg_home_elder_cleared_at": "2026-11-18T10:00:00+00:00"},
        ):
            self.assertEqual(
                _next_mtg_home_elder_cleared_at(1, "", ""),
                "2026-11-18T10:00:00+00:00",
            )
        with patch.object(contact_service, "fetch_one", return_value=None):
            self.assertEqual(_next_mtg_home_elder_cleared_at(1, "", ""), "")


if __name__ == "__main__":
    unittest.main()
