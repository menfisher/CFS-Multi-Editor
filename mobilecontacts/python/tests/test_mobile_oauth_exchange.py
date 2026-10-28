import unittest
from unittest.mock import MagicMock, patch

from mobile.google_auth import (
    _exchange_authorization_code,
    _oauth_redirect_uri_for_exchange,
    exchange_code,
)


class MobileOAuthExchangeTests(unittest.TestCase):
    def _request(self) -> MagicMock:
        request = MagicMock()
        request.session = {}
        return request

    def test_oauth_redirect_uri_prefers_signed_state(self) -> None:
        request = self._request()
        state_payload = {"redirect_uri": "https://mobile.contactsfreeshare.org/auth/callback"}
        self.assertEqual(
            _oauth_redirect_uri_for_exchange(request, state_payload),
            "https://mobile.contactsfreeshare.org/auth/callback",
        )

    @patch("mobile.google_auth.httpx.post")
    def test_exchange_uses_single_redirect_uri(self, post_mock) -> None:
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"access_token": "token", "expires_in": 3600, "scope": "email"}
        post_mock.return_value = response
        request = self._request()
        payload = _exchange_authorization_code(
            request,
            "abc",
            "https://mobile.contactsfreeshare.org/auth/callback",
        )
        self.assertEqual(payload["access_token"], "token")
        self.assertEqual(post_mock.call_count, 1)
        sent_redirect_uri = post_mock.call_args.kwargs["data"]["redirect_uri"]
        self.assertEqual(sent_redirect_uri, "https://mobile.contactsfreeshare.org/auth/callback")

    @patch("mobile.google_auth.httpx.get")
    @patch("mobile.google_auth.httpx.post")
    @patch("mobile.google_auth._validate_oauth_state")
    def test_exchange_code_is_cached_for_duplicate_callback(
        self,
        validate_mock,
        post_mock,
        get_mock,
    ) -> None:
        validate_mock.return_value = {
            "nonce": "n1",
            "redirect_uri": "https://mobile.contactsfreeshare.org/auth/callback",
        }
        token_response = MagicMock()
        token_response.status_code = 200
        token_response.json.return_value = {
            "access_token": "token",
            "expires_in": 3600,
            "scope": "email",
            "refresh_token": "refresh",
        }
        post_mock.return_value = token_response
        userinfo_response = MagicMock()
        userinfo_response.json.return_value = {"email": "user@example.com", "sub": "123"}
        get_mock.return_value = userinfo_response

        request = self._request()
        first = exchange_code(request, "same-code", "signed-state")
        second = exchange_code(request, "same-code", "signed-state")
        self.assertEqual(first["email"], "user@example.com")
        self.assertEqual(second["email"], "user@example.com")
        self.assertEqual(post_mock.call_count, 1)
        self.assertEqual(get_mock.call_count, 1)


if __name__ == "__main__":
    unittest.main()
