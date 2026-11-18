from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from mobile import main as mobile_main
from mobile.routes.auth import MULTI_EDITOR_SIGN_IN_REQUIRED_MESSAGE


class MobileHomeSignInTests(unittest.TestCase):
    def _home(self, *, multi_editor: bool, error: str = "") -> str:
        with patch.object(mobile_main, "is_multi_editor_enabled", return_value=multi_editor):
            client = TestClient(mobile_main.app)
            response = client.get("/", params={"error": error} if error else None)
        self.assertEqual(response.status_code, 200)
        return response.text

    def test_message_asks_for_initials_not_name(self) -> None:
        self.assertIn("enter your initials", MULTI_EDITOR_SIGN_IN_REQUIRED_MESSAGE)
        self.assertNotIn("enter your name", MULTI_EDITOR_SIGN_IN_REQUIRED_MESSAGE)

    def test_cold_start_shows_single_editor_button(self) -> None:
        page = self._home(multi_editor=False)
        self.assertIn("Single-Editor sign in", page)

    def test_multi_editor_account_error_hides_single_editor_button(self) -> None:
        page = self._home(multi_editor=False, error=MULTI_EDITOR_SIGN_IN_REQUIRED_MESSAGE)
        self.assertNotIn("Single-Editor sign in", page)
        self.assertIn("then enter your initials", page)
        self.assertIn("enter your initials", page)

    def test_missing_fields_are_reported_on_the_page(self) -> None:
        page = self._home(multi_editor=True)
        self.assertIn("novalidate", page)
        self.assertIn('id="mobile-signin-message"', page)
        self.assertIn("Enter the Editor password.", page)
        self.assertIn("Enter your initials.", page)

    def test_multi_editor_mode_asks_for_initials(self) -> None:
        page = self._home(multi_editor=True)
        self.assertNotIn("Single-Editor sign in", page)
        self.assertNotIn("enter your name", page)


if __name__ == "__main__":
    unittest.main()
