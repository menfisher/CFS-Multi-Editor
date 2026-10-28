import unittest
from unittest.mock import MagicMock, patch

from mobile.routes.auth import auth_logout


class MobileLogoutTests(unittest.TestCase):
    @patch("mobile.routes.auth.threading.Thread")
    def test_logout_clears_session_and_starts_background_cleanup(self, thread_mock) -> None:
        request = MagicMock()
        request.session = {"owner_email": "user@example.com", "other": "keep"}

        response = auth_logout(request)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/")
        self.assertNotIn("owner_email", request.session)
        thread_mock.assert_called_once()
        started = thread_mock.return_value.start
        started.assert_called_once()

    @patch("mobile.routes.auth.threading.Thread")
    def test_logout_without_email_skips_background_cleanup(self, thread_mock) -> None:
        request = MagicMock()
        request.session = {}

        response = auth_logout(request)

        self.assertEqual(response.status_code, 303)
        thread_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
