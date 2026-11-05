import unittest
from unittest.mock import MagicMock, patch

from mobile.routes.auth import auth_login_post
from mobile.routes import pending_edits as pending_edits_routes


class PendingEditsSignInTests(unittest.TestCase):
    def test_pending_edits_sign_in_does_not_require_password_or_google(self) -> None:
        request = MagicMock()
        request.session = {}
        with (
            patch("mobile.routes.auth.user_db_exists", return_value=True),
            patch("mobile.routes.auth.user_db_write_lock"),
            patch("mobile.routes.auth.mobile_user_db_session"),
            patch("mobile.routes.auth.use_user_database") as use_db,
            patch(
                "mobile.routes.auth.get_google_sync_summary",
                return_value={"account": {"status": "connected"}},
            ),
            patch("mobile.routes.auth.save_access_session") as save_session,
            patch("mobile.routes.auth.authorization_url") as auth_url,
            patch("mobile.routes.auth.has_cached_contacts", return_value=True),
            patch("mobile.routes.auth.mark_google_contacts_refreshed"),
            patch("mobile.routes.auth._background_non_editor_sign_in_sync"),
        ):
            response = auth_login_post(
                request,
                access_role="non_editor",
                access_name="EN",
                editor_secret="",
                drive_email="al.ms.lacontacts@gmail.com",
            )

        auth_url.assert_not_called()
        use_db.assert_called_once_with("al.ms.lacontacts@gmail.com")
        save_session.assert_called_once_with("non_editor", "EN")
        self.assertEqual(request.session["owner_email"], "al.ms.lacontacts@gmail.com")
        self.assertEqual(request.session["mobile_access_role"], "non_editor")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/m/pending-edits/")

    def test_pending_edits_sign_in_uses_the_entered_drive_account(self) -> None:
        request = MagicMock()
        request.session = {}
        with (
            patch("mobile.routes.auth.user_db_exists", return_value=True),
            patch("mobile.routes.auth.user_db_write_lock"),
            patch("mobile.routes.auth.mobile_user_db_session"),
            patch("mobile.routes.auth.use_user_database") as use_db,
            patch(
                "mobile.routes.auth.get_google_sync_summary",
                return_value={"account": {"status": "connected"}},
            ),
            patch("mobile.routes.auth.save_access_session"),
            patch("mobile.routes.auth.has_cached_contacts", return_value=False),
            patch("mobile.routes.auth.clear_google_contacts_refresh_flag"),
            patch("mobile.routes.auth._background_non_editor_sign_in_sync"),
        ):
            response = auth_login_post(
                request,
                access_role="non_editor",
                access_name="Emmy",
                editor_secret="should-be-ignored",
                drive_email="  Other.Drive@gmail.com ",
            )

        use_db.assert_called_once_with("other.drive@gmail.com")
        self.assertEqual(request.session["owner_email"], "other.drive@gmail.com")
        self.assertEqual(response.status_code, 303)

    def test_pending_edits_sign_in_requires_a_connected_drive_account(self) -> None:
        request = MagicMock()
        request.session = {}
        with patch("mobile.routes.auth.user_db_exists", return_value=False):
            response = auth_login_post(
                request,
                access_role="non_editor",
                access_name="EN",
                drive_email="al.ms.lacontacts@gmail.com",
            )

        self.assertEqual(response.status_code, 303)
        self.assertIn("error=", response.headers["location"])
        self.assertNotIn("owner_email", request.session)


class PendingEditsUploadTests(unittest.TestCase):
    def test_pending_edits_upload_uses_signed_in_drive_account(self) -> None:
        request = MagicMock()
        request.session = {
            "owner_email": "al.ms.lacontacts@gmail.com",
            "mobile_access_role": "non_editor",
            "mobile_access_name": "Emmy",
        }
        with (
            patch.object(pending_edits_routes, "use_user_database") as use_db,
            patch.object(pending_edits_routes, "mobile_is_non_editor", return_value=True),
            patch.object(pending_edits_routes, "can_edit_changes_list", return_value=True),
            patch.object(
                pending_edits_routes,
                "get_google_sync_summary",
                return_value={"account": {"status": "connected"}, "state": {}},
            ),
            patch.object(pending_edits_routes, "export_pending_edits_support_to_google_drive") as export_assets,
            patch.object(pending_edits_routes, "export_changes_list_to_google_drive") as export_list,
            patch.object(pending_edits_routes, "get_connection") as conn_mock,
            patch.object(pending_edits_routes, "_needs_drive_upload", return_value=False),
        ):
            conn = MagicMock()
            conn_mock.return_value.__enter__.return_value = conn
            conn_mock.return_value.__exit__.return_value = None
            response = pending_edits_routes.pending_edits_upload(request)

        self.assertEqual(response.status_code, 200)
        use_db.assert_called_once_with("al.ms.lacontacts@gmail.com")
        export_assets.assert_called_once_with()
        export_list.assert_called_once_with(merge_remote=True)


if __name__ == "__main__":
    unittest.main()
