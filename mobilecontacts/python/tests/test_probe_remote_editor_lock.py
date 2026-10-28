import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.services.google_sync_service import probe_remote_editor_lock


class ProbeRemoteEditorLockTests(unittest.TestCase):
    @patch("app.services.google_sync_service.get_google_sync_summary")
    @patch("app.services.google_sync_service._load_editor_lock")
    @patch("app.services.google_sync_service._ensure_editor_lock_drive_storage")
    def test_probe_reports_blocked_before_settings_import(
        self,
        mock_storage,
        mock_load_lock,
        mock_summary,
    ) -> None:
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=12)).isoformat()
        mock_storage.return_value = {"editor_lock_file_id": "lock-file"}
        mock_load_lock.return_value = {
            "editor_name": "Desktop Editor",
            "session_id": "remote-session",
            "expires_at": expires_at,
        }
        mock_summary.return_value = {"state": {"editor_session_id": "mobile-session"}}

        result = probe_remote_editor_lock("token", pending_editor_name="Mobile", apply_blocked_state=True)

        self.assertTrue(result["blocked"])
        self.assertEqual(result["owner_name"], "Desktop Editor")
        self.assertEqual(result["expires_at"], expires_at)

    @patch("app.services.google_sync_service.get_google_sync_summary")
    @patch("app.services.google_sync_service._load_editor_lock")
    @patch("app.services.google_sync_service._ensure_editor_lock_drive_storage")
    def test_probe_allows_same_session(
        self,
        mock_storage,
        mock_load_lock,
        mock_summary,
    ) -> None:
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=12)).isoformat()
        mock_storage.return_value = {"editor_lock_file_id": "lock-file"}
        mock_load_lock.return_value = {
            "editor_name": "Desktop Editor",
            "session_id": "same-session",
            "expires_at": expires_at,
        }
        mock_summary.return_value = {"state": {"editor_session_id": "same-session"}}

        result = probe_remote_editor_lock("token")

        self.assertFalse(result["blocked"])


if __name__ == "__main__":
    unittest.main()
