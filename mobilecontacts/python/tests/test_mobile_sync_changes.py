import unittest
from unittest.mock import patch

from mobile.sync_adapter import mobile_editor_needs_sync, sync_contact_changes


class MobileEditorNeedsSyncTests(unittest.TestCase):
    @patch(
        "mobile.sync_adapter.get_google_sync_summary",
        return_value={
            "state": {
                "pending_upload_count": 2,
                "pending_contact_drive_export_count": 0,
                "upload_in_progress": 0,
            }
        },
    )
    def test_needs_sync_when_google_queue_pending(self, *_mocks) -> None:
        self.assertTrue(mobile_editor_needs_sync())

    @patch(
        "mobile.sync_adapter.get_google_sync_summary",
        return_value={
            "state": {
                "pending_upload_count": 0,
                "pending_contact_drive_export_count": 1,
                "upload_in_progress": 0,
            }
        },
    )
    def test_needs_sync_when_drive_export_pending(self, *_mocks) -> None:
        self.assertTrue(mobile_editor_needs_sync())

    @patch(
        "mobile.sync_adapter.get_google_sync_summary",
        return_value={
            "state": {
                "pending_upload_count": 0,
                "pending_contact_drive_export_count": 0,
                "upload_in_progress": 0,
            }
        },
    )
    def test_no_sync_attention_when_up_to_date(self, *_mocks) -> None:
        self.assertFalse(mobile_editor_needs_sync())

    @patch(
        "mobile.sync_adapter.get_google_sync_summary",
        return_value={
            "state": {
                "pending_upload_count": 3,
                "pending_contact_drive_export_count": 1,
                "upload_in_progress": 1,
            }
        },
    )
    def test_no_reminder_while_upload_in_progress(self, *_mocks) -> None:
        self.assertFalse(mobile_editor_needs_sync())


class MobileSyncChangesTests(unittest.TestCase):
    @patch(
        "mobile.sync_adapter.sync_mobile_editor_contact_changes",
        return_value={
            "changed_count": 2,
            "created_count": 2,
            "updated_count": 0,
            "deleted_count": 0,
            "skipped_conflict_count": 0,
            "contact_upload_count": 1,
            "upload_processed_count": 1,
            "drive_exported": 1,
            "drive_field_updates": 0,
        },
    )
    def test_sync_uses_two_way_mobile_path(self, sync_mock) -> None:
        result = sync_contact_changes(upload=True)
        sync_mock.assert_called_once_with()
        self.assertEqual(result["contact_upload_count"], 1)
        self.assertEqual(result["drive_exported"], 1)
        self.assertEqual(result["changed_count"], 2)
        self.assertEqual(result["created_count"], 2)


class MobileEditorShareSyncTests(unittest.TestCase):
    @patch(
        "app.services.google_sync_service.sync_google_contact_changes",
        return_value={
            "changed_count": 0,
            "created_count": 0,
            "updated_count": 0,
            "deleted_count": 0,
            "skipped_conflict_count": 0,
            "conflict_names": [],
        },
    )
    @patch("app.services.google_sync_service.record_share_sync_result")
    @patch(
        "app.services.share_sync_service.sync_share_app_contact_changes",
        return_value={"ok": True},
    )
    @patch(
        "app.services.google_sync_service.export_mobile_pending_shared_contacts_to_google_drive",
        return_value=1,
    )
    @patch("app.services.google_sync_service._ensure_shared_drive_storage", return_value={})
    @patch(
        "app.services.google_sync_service._get_valid_access_token",
        return_value=("token", "owner@example.com"),
    )
    @patch("app.services.google_sync_service.upload_google_contacts")
    @patch("app.services.google_sync_service._read_pending_upload_count", return_value=1)
    @patch("app.services.google_sync_service.ensure_google_sync_records")
    @patch("app.services.google_sync_service.get_connection")
    def test_mobile_editor_sync_notifies_share_app_after_upload(
        self,
        get_connection_mock,
        _ensure_records_mock,
        _pending_upload_mock,
        upload_mock,
        _token_mock,
        _storage_mock,
        _drive_export_mock,
        share_sync_mock,
        record_result_mock,
        download_mock,
    ) -> None:
        from app.services.google_sync_service import sync_mobile_editor_contact_changes

        class _Result:
            def __init__(self, row):
                self._row = row

            def fetchone(self):
                return self._row

        class _Conn:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, sql, *_args, **_kwargs):
                text = str(sql)
                if "google_contacts_sync_token" in text:
                    return _Result({"google_contacts_sync_token": "token-1"})
                return _Result({"count": 1})

        get_connection_mock.return_value = _Conn()

        def _upload(*, finish_progress=True, skip_share_sync=False, defer_share_sync_into=None):
            self.assertFalse(skip_share_sync)
            self.assertIsNotNone(defer_share_sync_into)
            defer_share_sync_into["account_email"] = "owner@example.com"
            defer_share_sync_into["person_ids"] = ["people/123"]
            return 1

        upload_mock.side_effect = _upload

        result = sync_mobile_editor_contact_changes()

        self.assertEqual(result["contact_upload_count"], 1)
        share_sync_mock.assert_called_once_with("owner@example.com", ["people/123"])
        record_result_mock.assert_called_once()
        download_mock.assert_called_once_with(force_full_fetch=False, skip_drive_reconcile=True)

    @patch(
        "app.services.google_sync_service.sync_google_contact_changes",
        return_value={
            "changed_count": 3,
            "created_count": 3,
            "updated_count": 0,
            "deleted_count": 0,
            "skipped_conflict_count": 0,
            "conflict_names": [],
        },
    )
    @patch("app.services.google_sync_service.upload_google_contacts")
    @patch("app.services.google_sync_service._read_pending_upload_count", return_value=0)
    @patch("app.services.google_sync_service.ensure_google_sync_records")
    @patch("app.services.google_sync_service.get_connection")
    def test_mobile_editor_sync_downloads_even_when_nothing_to_upload(
        self,
        get_connection_mock,
        _ensure_records_mock,
        _pending_upload_mock,
        upload_mock,
        download_mock,
    ) -> None:
        from app.services.google_sync_service import sync_mobile_editor_contact_changes

        class _Result:
            def __init__(self, row):
                self._row = row

            def fetchone(self):
                return self._row

        class _Conn:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, sql, *_args, **_kwargs):
                text = str(sql)
                if "google_contacts_sync_token" in text:
                    return _Result({"google_contacts_sync_token": ""})
                return _Result({"count": 0})

        get_connection_mock.return_value = _Conn()

        result = sync_mobile_editor_contact_changes()

        upload_mock.assert_not_called()
        download_mock.assert_called_once_with(force_full_fetch=True, skip_drive_reconcile=True)
        self.assertEqual(result["contact_upload_count"], 0)
        self.assertEqual(result["changed_count"], 3)
        self.assertEqual(result["created_count"], 3)


if __name__ == "__main__":
    unittest.main()
