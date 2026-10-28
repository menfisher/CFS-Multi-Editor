"""Tests for Google People connections incremental sync token recovery."""

from __future__ import annotations

import io
import json
import unittest
from urllib.error import HTTPError

from app.services.google_sync_service import _google_connections_sync_token_invalid


class GoogleConnectionsSyncTokenTests(unittest.TestCase):
    def _error(self, code: int, payload: dict) -> HTTPError:
        body = json.dumps(payload).encode("utf-8")
        return HTTPError(
            url="https://people.googleapis.com/v1/people/me/connections",
            code=code,
            msg="Bad Request" if code == 400 else "Gone",
            hdrs=None,
            fp=io.BytesIO(body),
        )

    def test_400_expired_sync_token_is_recoverable(self) -> None:
        exc = self._error(
            400,
            {
                "error": {
                    "code": 400,
                    "message": "Sync token is expired. Clear local cache and retry call without the sync token.",
                    "status": "FAILED_PRECONDITION",
                }
            },
        )
        self.assertTrue(_google_connections_sync_token_invalid(exc, had_sync_token=True))

    def test_400_without_sync_token_is_not_recoverable(self) -> None:
        exc = self._error(400, {"error": {"message": "Sync token is expired."}})
        self.assertFalse(_google_connections_sync_token_invalid(exc, had_sync_token=False))

    def test_410_with_sync_token_is_recoverable(self) -> None:
        exc = self._error(410, {"error": {"message": "Sync token is no longer valid."}})
        self.assertTrue(_google_connections_sync_token_invalid(exc, had_sync_token=True))


if __name__ == "__main__":
    unittest.main()
