import threading
import unittest
from unittest.mock import MagicMock, patch

from mobile.routes.auth import _resolve_oauth_callback_redirect


class MobileOAuthCallbackDedupTests(unittest.TestCase):
    def _request(self) -> MagicMock:
        request = MagicMock()
        request.session = {}
        return request

    @patch("mobile.routes.auth.finish_callback_exchange")
    @patch("mobile.routes.auth._finish_sign_in")
    @patch("mobile.routes.auth.mobile_user_db_session")
    @patch("mobile.routes.auth.exchange_code")
    @patch("mobile.routes.auth.begin_callback_exchange")
    @patch("mobile.routes.auth.cached_callback_redirect")
    def test_duplicate_callback_reuses_cached_blocked_error(
        self,
        cached_redirect_mock,
        begin_mock,
        exchange_mock,
        db_session_mock,
        finish_sign_in_mock,
        finish_exchange_mock,
    ) -> None:
        blocked_url = "/?error=" + "Alex%20is%20already%20signed%20in"
        cached_redirect_mock.side_effect = [None, blocked_url]
        begin_mock.return_value = (False, None)
        request = self._request()

        redirect_url = _resolve_oauth_callback_redirect(request, "same-code", "signed-state")

        self.assertEqual(redirect_url, blocked_url)
        exchange_mock.assert_not_called()
        finish_sign_in_mock.assert_not_called()
        finish_exchange_mock.assert_not_called()

    @patch("mobile.routes.auth.finish_callback_exchange")
    @patch("mobile.routes.auth._finish_sign_in", return_value="/m/contacts/")
    @patch("mobile.routes.auth.mobile_user_db_session")
    @patch("mobile.routes.auth.exchange_code")
    @patch("mobile.routes.auth.wait_for_callback_redirect")
    @patch("mobile.routes.auth.begin_callback_exchange")
    @patch("mobile.routes.auth.cached_callback_redirect")
    def test_waiting_duplicate_callback_gets_owner_result(
        self,
        cached_redirect_mock,
        begin_mock,
        wait_mock,
        exchange_mock,
        db_session_mock,
        finish_sign_in_mock,
        finish_exchange_mock,
    ) -> None:
        wait_event = threading.Event()
        cached_redirect_mock.side_effect = [None, "/m/contacts/"]
        begin_mock.return_value = (False, wait_event)
        exchange_mock.return_value = {"email": "user@example.com", "oauth_state": {}}
        db_session_mock.return_value.__enter__.return_value = None
        db_session_mock.return_value.__exit__.return_value = None
        request = self._request()

        redirect_url = _resolve_oauth_callback_redirect(request, "same-code", "signed-state")

        self.assertEqual(redirect_url, "/m/contacts/")
        wait_mock.assert_called_once_with("same-code")
        exchange_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
