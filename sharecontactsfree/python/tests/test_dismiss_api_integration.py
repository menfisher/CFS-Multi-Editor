"""HTTP integration tests for dismissSharedRecipientGroup RPC."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient

from app import db
from app.main import app


class DismissApiIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        db.DATABASE_PATH = Path(self._tmpdir.name) / "test.db"
        db.init_db()
        self.client = TestClient(app)
        self.recipient = "recipient@example.com"
        self.owner = "al.ms.lacontacts@gmail.com"
        db.save_app_data(
            self.recipient,
            {
                "sharedGroups": [
                    {
                        "owner": "unknown",
                        "name": "AL South - Birmingham (GRAHAM)",
                        "resourceName": "recovered/abc-123",
                        "shareId": "share-uuid-1",
                        "created": "contactGroups/old-google-id",
                        "memberCount": 4,
                        "status": "Shared (recovered)",
                    }
                ],
                "invitedByOwner": {self.owner: "2026-01-01T00:00:00Z"},
            },
        )

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _call(self, method: str, args: list) -> dict:
        from app.main import _dispatch

        return _dispatch(self.recipient, method, args)

    def test_dismiss_rpc_removes_group_from_db_and_response(self) -> None:
        result = self._call(
            "dismissSharedRecipientGroup",
            [
                self.owner,
                "recovered/abc-123",
                "share-uuid-1",
                "",
                "AL South - Birmingham (GRAHAM)",
            ],
        )
        self.assertEqual(result.get("sharedGroups"), [])
        self.assertTrue(result.get("dismissedSharedGroups"))
        stored = db.get_app_data(self.recipient) or {}
        self.assertEqual(stored.get("sharedGroups"), [])
        self.assertTrue(stored.get("dismissedSharedGroups"))

    @patch("app.services.app_data.owner_credentials", return_value=object())
    @patch("app.services.app_data.get_profile", return_value={"email": "recipient@example.com"})
    @patch("app.services.app_data.recipient_credentials", return_value=None)
    def test_load_app_hides_dismissed_group(
        self, _recipient_creds, _profile, _owner_creds
    ) -> None:
        self._call(
            "dismissSharedRecipientGroup",
            [
                self.owner,
                "recovered/abc-123",
                "share-uuid-1",
                "",
                "AL South - Birmingham (GRAHAM)",
            ],
        )
        loaded = self._call("loadApp", ["shared", False])
        self.assertEqual(loaded.get("sharedGroups"), [])
        self.assertTrue(loaded.get("dismissedSharedGroups"))


if __name__ == "__main__":
    unittest.main()
