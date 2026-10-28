import time
import unittest
from unittest.mock import MagicMock

from mobile.google_auth import (
    OAUTH_STATE_MAX_AGE_SECONDS,
    _encode_oauth_state,
    _validate_oauth_state,
)


class MobileOAuthStateTests(unittest.TestCase):
    def _request(self, session: dict | None = None):
        request = MagicMock()
        request.session = session or {}
        return request

    def test_accepts_signed_state_without_session_cookie(self) -> None:
        payload = {"nonce": "abc123", "iat": int(time.time()), "access_role": "non_editor"}
        state = _encode_oauth_state(payload)
        result = _validate_oauth_state(self._request({}), state)
        self.assertEqual(result["nonce"], "abc123")

    def test_accepts_signed_state_when_session_nonce_is_stale(self) -> None:
        payload = {"nonce": "abc123", "iat": int(time.time()), "access_role": "editor"}
        state = _encode_oauth_state(payload)
        result = _validate_oauth_state(self._request({"mobile_oauth_state": "different"}), state)
        self.assertEqual(result["access_role"], "editor")
        self.assertEqual(result["nonce"], "abc123")

    def test_rejects_expired_state(self) -> None:
        payload = {"nonce": "abc123", "iat": int(time.time()) - OAUTH_STATE_MAX_AGE_SECONDS - 5}
        state = _encode_oauth_state(payload)
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            _validate_oauth_state(self._request({}), state)


if __name__ == "__main__":
    unittest.main()
