import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.services.google_sync_service import _editor_lock_is_own, acquire_editor_lock, probe_remote_editor_lock


class EditorLockOwnSessionTests(unittest.TestCase):
    def test_same_session_id_is_own(self) -> None:
        self.assertTrue(
            _editor_lock_is_own(
                {"session_id": "abc", "editor_name": "Pat", "account_email": "a@example.com"},
                local_session_id="abc",
                editor_name="Other",
                account_email="b@example.com",
            )
        )

    def test_same_name_and_email_is_own_after_session_loss(self) -> None:
        self.assertTrue(
            _editor_lock_is_own(
                {"session_id": "old-session", "editor_name": "Pat Reed", "account_email": "pat@example.com"},
                local_session_id="",
                editor_name="Pat Reed",
                account_email="pat@example.com",
            )
        )

    def test_name_match_is_case_insensitive(self) -> None:
        self.assertTrue(
            _editor_lock_is_own(
                {"editor_name": "Pat Reed", "account_email": "pat@example.com"},
                editor_name="pat reed",
                account_email="PAT@example.com",
            )
        )

    def test_same_name_different_account_is_not_own(self) -> None:
        self.assertFalse(
            _editor_lock_is_own(
                {"editor_name": "Pat Reed", "account_email": "pat@example.com"},
                editor_name="Pat Reed",
                account_email="other@example.com",
            )
        )

    def test_legacy_lock_without_email_reclaims_same_name(self) -> None:
        self.assertTrue(
            _editor_lock_is_own(
                {"editor_name": "Pat Reed"},
                editor_name="Pat Reed",
                account_email="pat@example.com",
            )
        )


class ProbeRemoteEditorLockReclaimTests(unittest.TestCase):
    @patch("app.services.google_sync_service.get_google_sync_summary")
    @patch("app.services.google_sync_service._load_editor_lock")
    @patch("app.services.google_sync_service._ensure_editor_lock_drive_storage")
    def test_probe_allows_same_editor_after_session_loss(
        self,
        mock_storage,
        mock_load_lock,
        mock_summary,
    ) -> None:
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=12)).isoformat()
        mock_storage.return_value = {"editor_lock_file_id": "lock-file"}
        mock_load_lock.return_value = {
            "editor_name": "Pat Reed",
            "account_email": "pat@example.com",
            "session_id": "old-session",
            "expires_at": expires_at,
        }
        mock_summary.return_value = {
            "state": {"editor_session_id": ""},
            "account": {"email": "pat@example.com"},
        }

        result = probe_remote_editor_lock("token", pending_editor_name="Pat Reed")

        self.assertFalse(result["blocked"])

    @patch("app.services.google_sync_service._drive_update_json_file_content")
    @patch("app.services.google_sync_service.get_google_sync_summary")
    @patch("app.services.google_sync_service._load_editor_lock")
    @patch("app.services.google_sync_service._ensure_editor_lock_drive_storage")
    def test_probe_clears_expired_lock_instead_of_blocking(
        self,
        mock_storage,
        mock_load_lock,
        mock_summary,
        mock_update,
    ) -> None:
        expires_at = (datetime.now(timezone.utc) - timedelta(hours=16)).isoformat()
        mock_storage.return_value = {"editor_lock_file_id": "lock-file"}
        mock_load_lock.return_value = {
            "editor_name": "Stacey Seidlitz",
            "account_email": "stacey@example.com",
            "session_id": "old-session",
            "expires_at": expires_at,
        }
        mock_summary.return_value = {
            "state": {"editor_session_id": ""},
            "account": {"email": "cv@example.com"},
        }

        result = probe_remote_editor_lock("token", pending_editor_name="cv")

        self.assertFalse(result["blocked"])
        mock_update.assert_called_once_with("token", "lock-file", {})


class AcquireEditorLockReclaimTests(unittest.TestCase):
    @patch("app.services.google_sync_service._start_signin_sync_job")
    @patch("app.services.google_sync_service._set_signin_sync_state")
    @patch("app.services.google_sync_service.get_connection")
    @patch("app.services.google_sync_service._set_editor_session")
    @patch("app.services.google_sync_service._drive_update_json_file_content")
    @patch("app.services.google_sync_service.get_google_sync_summary")
    @patch("app.services.google_sync_service._load_editor_lock")
    @patch("app.services.google_sync_service._ensure_shared_drive_storage")
    @patch("app.services.google_sync_service._get_valid_access_token")
    def test_acquire_reclaims_own_lock_instead_of_blocking(
        self,
        mock_token,
        mock_storage,
        mock_load_lock,
        mock_summary,
        mock_update,
        mock_set_session,
        mock_connection,
        _mock_set_signin_sync,
        mock_start_sync,
    ) -> None:
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=20)
        mock_token.return_value = ("token", "pat@example.com")
        mock_storage.return_value = {"editor_lock_file_id": "lock-file"}
        mock_load_lock.return_value = {
            "editor_name": "Pat Reed",
            "account_email": "pat@example.com",
            "session_id": "old-session",
            "expires_at": expires_at.isoformat(),
        }
        mock_summary.return_value = {
            "state": {
                "editor_session_id": "",
                "editor_lock_timeout_minutes": 60,
            }
        }
        mock_connection.return_value.__enter__.return_value = mock_connection.return_value
        mock_connection.return_value.__exit__.return_value = False

        result = acquire_editor_lock("Pat Reed", settings_loaded=True, start_signin_sync=True)

        self.assertEqual(result["status"], "acquired")
        mock_update.assert_called_once()
        written = mock_update.call_args.args[2]
        self.assertEqual(written["session_id"], "old-session")
        self.assertEqual(written["editor_name"], "Pat Reed")
        mock_start_sync.assert_called_once()
        mock_set_session.assert_called()
        self.assertNotEqual(mock_set_session.call_args.kwargs.get("mode"), "no_edit")

    @patch("app.services.google_sync_service._set_editor_session")
    @patch("app.services.google_sync_service.get_google_sync_summary")
    @patch("app.services.google_sync_service._load_editor_lock")
    @patch("app.services.google_sync_service._ensure_shared_drive_storage")
    @patch("app.services.google_sync_service._get_valid_access_token")
    def test_acquire_still_blocks_a_different_editor(
        self,
        mock_token,
        mock_storage,
        mock_load_lock,
        mock_summary,
        _mock_set_session,
    ) -> None:
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=20)
        mock_token.return_value = ("token", "sam@example.com")
        mock_storage.return_value = {"editor_lock_file_id": "lock-file"}
        mock_load_lock.return_value = {
            "editor_name": "Pat Reed",
            "account_email": "pat@example.com",
            "session_id": "pat-session",
            "expires_at": expires_at.isoformat(),
        }
        mock_summary.return_value = {"state": {"editor_session_id": ""}}

        result = acquire_editor_lock("Sam Lee", settings_loaded=True, start_signin_sync=False)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["owner_name"], "Pat Reed")

    @patch("app.services.google_sync_service._start_signin_sync_job")
    @patch("app.services.google_sync_service._set_signin_sync_state")
    @patch("app.services.google_sync_service.get_connection")
    @patch("app.services.google_sync_service._set_editor_session")
    @patch("app.services.google_sync_service._drive_update_json_file_content")
    @patch("app.services.google_sync_service.get_google_sync_summary")
    @patch("app.services.google_sync_service._load_editor_lock")
    @patch("app.services.google_sync_service._ensure_shared_drive_storage")
    @patch("app.services.google_sync_service._get_valid_access_token")
    def test_acquire_takes_expired_lock_from_another_editor(
        self,
        mock_token,
        mock_storage,
        mock_load_lock,
        mock_summary,
        mock_update,
        mock_set_session,
        mock_connection,
        _mock_set_signin_sync,
        mock_start_sync,
    ) -> None:
        expires_at = datetime.now(timezone.utc) - timedelta(hours=16)
        mock_token.return_value = ("token", "cv@example.com")
        mock_storage.return_value = {"editor_lock_file_id": "lock-file"}
        mock_load_lock.return_value = {
            "editor_name": "Stacey Seidlitz",
            "account_email": "stacey@example.com",
            "session_id": "stacey-session",
            "expires_at": expires_at.isoformat(),
        }
        mock_summary.return_value = {
            "state": {
                "editor_session_id": "",
                "editor_lock_timeout_minutes": 30,
            }
        }
        mock_connection.return_value.__enter__.return_value = mock_connection.return_value
        mock_connection.return_value.__exit__.return_value = False

        result = acquire_editor_lock("cv", settings_loaded=True, start_signin_sync=False)

        self.assertEqual(result["status"], "acquired")
        self.assertEqual(result["editor_name"], "cv")
        self.assertGreaterEqual(mock_update.call_count, 1)
        written = mock_update.call_args.args[2]
        self.assertEqual(written["editor_name"], "cv")
        mock_start_sync.assert_not_called()
        self.assertEqual(mock_set_session.call_args.kwargs.get("mode"), "edit")

    @patch("app.services.google_sync_service._start_signin_sync_job")
    @patch("app.services.google_sync_service._set_signin_sync_state")
    @patch("app.services.google_sync_service.get_connection")
    @patch("app.services.google_sync_service._set_editor_session")
    @patch("app.services.google_sync_service._drive_update_json_file_content")
    @patch("app.services.google_sync_service.get_google_sync_summary")
    @patch("app.services.google_sync_service._load_editor_lock")
    @patch("app.services.google_sync_service._ensure_editor_lock_drive_storage")
    @patch("app.services.google_sync_service._get_valid_access_token")
    def test_acquire_reclaims_when_same_computer_holds_lock_under_other_initials(
        self,
        mock_token,
        mock_storage,
        mock_load_lock,
        mock_summary,
        mock_update,
        mock_set_session,
        mock_connection,
        _mock_set_signin_sync,
        mock_start_sync,
    ) -> None:
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=19)
        mock_token.return_value = ("token", "pat@example.com")
        mock_storage.return_value = {"editor_lock_file_id": "lock-file"}
        mock_load_lock.return_value = {
            "editor_name": "CV",
            "account_email": "pat@example.com",
            "session_id": "local-session",
            "expires_at": expires_at.isoformat(),
        }
        mock_summary.return_value = {
            "state": {
                "editor_session_id": "local-session",
                "editor_name": "CV",
                "access_name": "FL",
                "editor_lock_timeout_minutes": 60,
            }
        }
        mock_connection.return_value.__enter__.return_value = mock_connection.return_value
        mock_connection.return_value.__exit__.return_value = False

        result = acquire_editor_lock(
            "FL",
            settings_loaded=True,
            minimal_drive_storage=True,
            start_signin_sync=False,
        )

        self.assertEqual(result["status"], "acquired")
        self.assertEqual(result["editor_name"], "CV")
        written = mock_update.call_args.args[2]
        self.assertEqual(written["session_id"], "local-session")
        self.assertEqual(written["editor_name"], "CV")
        mock_start_sync.assert_not_called()
        self.assertNotEqual(mock_set_session.call_args.kwargs.get("mode"), "no_edit")


class ExtendEditorLockOwnSessionTests(unittest.TestCase):
    @patch("app.services.google_sync_service.get_editor_lock_status", return_value={"active": True})
    @patch("app.services.google_sync_service._set_editor_session")
    @patch("app.services.google_sync_service._load_editor_lock")
    @patch("app.services.google_sync_service._ensure_editor_lock_drive_storage")
    @patch("app.services.google_sync_service._get_valid_access_token")
    @patch("app.services.google_sync_service.get_google_sync_summary")
    def test_empty_drive_lock_does_not_drop_local_session(
        self,
        mock_summary,
        mock_token,
        mock_storage,
        mock_load_lock,
        mock_set_session,
        _mock_status,
    ) -> None:
        from app.services.google_sync_service import extend_editor_lock

        mock_summary.return_value = {
            "state": {
                "multi_editor_enabled": 1,
                "editor_mode": "edit",
                "editor_session_id": "local-session",
                "editor_name": "CV",
            }
        }
        mock_token.return_value = ("token", "pat@example.com")
        mock_storage.return_value = {"editor_lock_file_id": "lock-file"}
        mock_load_lock.return_value = {}

        result = extend_editor_lock()

        self.assertEqual(result["status"], "extended_local")
        mock_set_session.assert_not_called()


class EditingAllowedRoleTests(unittest.TestCase):
    @patch("app.services.google_sync_service._has_active_local_editor_session", return_value=True)
    @patch("app.services.google_sync_service._clear_expired_local_editor_session", return_value=False)
    @patch("app.services.google_sync_service.get_google_sync_summary")
    def test_contributor_role_cannot_edit_while_editor_lock_is_held(
        self,
        mock_summary,
        _mock_clear,
        _mock_active,
    ) -> None:
        from app.services.google_sync_service import is_editing_allowed

        mock_summary.return_value = {
            "account": {"status": "connected"},
            "state": {
                "multi_editor_enabled": 1,
                "access_role": "non_editor",
                "editor_mode": "edit",
                "editor_session_id": "local-session",
            },
        }
        self.assertFalse(is_editing_allowed())


if __name__ == "__main__":
    unittest.main()
