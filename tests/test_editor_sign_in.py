import unittest
from unittest.mock import patch

from app.routes import google_sync as google_sync_routes
from app.services import google_sync_service as gss


class EditorSignInSpeedTests(unittest.TestCase):
    def test_sign_in_takes_lock_without_blocking_on_shared_data(self) -> None:
        summary = {
            "account": {"status": "connected", "drive_scope_ready": 1},
            "state": {},
        }
        with (
            patch.object(google_sync_routes, "verify_editor_secret", return_value=True),
            patch.object(google_sync_routes, "set_pending_editor_name"),
            patch.object(google_sync_routes, "get_google_sync_summary", return_value=summary),
            patch.object(
                google_sync_routes,
                "acquire_editor_lock",
                return_value={"status": "acquired"},
            ) as acquire,
            patch.object(google_sync_routes, "save_access_session") as save_session,
            patch.object(google_sync_routes, "start_google_signin_sync") as start_sync,
        ):
            response = google_sync_routes.google_editor_sign_in(
                editor_name="CV",
                editor_secret="secret",
            )

        acquire.assert_called_once_with(
            "CV",
            settings_loaded=True,
            minimal_drive_storage=True,
            start_signin_sync=True,
        )
        start_sync.assert_not_called()
        save_session.assert_called_once_with("editor", "CV")
        self.assertEqual(response.status_code, 303)
        self.assertIn("notice=editor_signed_in", str(response.headers.get("location") or ""))

    def test_signed_in_redirect_does_not_check_app_updates(self) -> None:
        with patch("app.services.app_update_service.get_app_update_status") as update_status:
            url = google_sync_routes._editor_signed_in_redirect_url()
        update_status.assert_not_called()
        self.assertIn("notice=editor_signed_in", url)
        self.assertNotIn("update_available", url)

    def test_blocked_sign_in_keeps_google_connected(self) -> None:
        summary = {
            "account": {"status": "connected", "drive_scope_ready": 1},
            "state": {},
        }
        with (
            patch.object(google_sync_routes, "verify_editor_secret", return_value=True),
            patch.object(google_sync_routes, "set_pending_editor_name"),
            patch.object(google_sync_routes, "get_google_sync_summary", return_value=summary),
            patch.object(
                google_sync_routes,
                "acquire_editor_lock",
                return_value={"status": "blocked", "owner_name": "Stacey Seidlitz"},
            ),
            patch.object(google_sync_routes, "disconnect_google_account") as disconnect,
        ):
            response = google_sync_routes.google_editor_sign_in(
                editor_name="CV",
                editor_secret="secret",
            )

        disconnect.assert_not_called()
        self.assertEqual(response.status_code, 303)
        self.assertIn("notice=editor_lock_blocked", str(response.headers.get("location") or ""))


class EditorSharedDriveBackupTests(unittest.TestCase):
    def test_auto_backup_exports_live_shared_contacts_without_revision_bump(self) -> None:
        with (
            patch.object(gss, "export_shared_contacts_to_google_drive") as export_shared,
            patch.object(gss, "_get_valid_access_token", return_value=("token", "a@b.com")),
            patch.object(
                gss,
                "_ensure_shared_drive_storage",
                return_value={"contacts_full_file_id": "full"},
            ),
            patch.object(gss, "_write_contacts_full_snapshot_to_google_drive") as write_full,
            patch.object(gss, "_store_drive_state") as store_state,
            patch.object(gss, "_next_dataset_sync_revision") as next_rev,
            patch.object(gss, "_write_shared_sync_state") as write_state,
            patch.object(gss, "_store_local_sync_revisions") as store_rev,
        ):
            gss._auto_backup_contacts_snapshot_to_google_drive()

        export_shared.assert_called_once()
        write_full.assert_called_once()
        store_state.assert_called_once()
        next_rev.assert_not_called()
        write_state.assert_not_called()
        store_rev.assert_not_called()
