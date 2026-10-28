import unittest
from unittest.mock import patch

from mobile.sync_adapter import refresh_contacts_from_google


class MobileContactRefreshTests(unittest.TestCase):
    @patch("mobile.sync_adapter.import_sign_in_contacts", return_value=42)
    @patch("mobile.sync_adapter._try_import_field_list")
    @patch("mobile.sync_adapter._local_contact_count", return_value=0)
    def test_empty_cache_uses_full_import(self, _count, _field, import_mock) -> None:
        result = refresh_contacts_from_google(include_app_fields=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["changed_count"], 42)
        import_mock.assert_called_once_with(include_app_fields=False)

    @patch("mobile.sync_adapter._try_sync_contact_app_fields", return_value=0)
    @patch(
        "mobile.sync_adapter.sync_google_contact_changes",
        return_value={"changed_count": 1, "created_count": 0, "updated_count": 1, "deleted_count": 0},
    )
    @patch("mobile.sync_adapter._try_import_field_list")
    @patch("mobile.sync_adapter._has_google_contacts_sync_token", return_value=True)
    @patch("mobile.sync_adapter._local_contact_count", return_value=120)
    def test_cached_contacts_with_sync_token_use_incremental(self, _count, _token, _field, sync_mock, *_rest) -> None:
        result = refresh_contacts_from_google(include_app_fields=False)
        self.assertTrue(result["ok"])
        sync_mock.assert_called_once_with(force_full_fetch=False, skip_drive_reconcile=True)

    @patch("mobile.sync_adapter._try_sync_contact_app_fields", return_value=3)
    @patch(
        "mobile.sync_adapter.sync_google_contact_changes",
        return_value={"changed_count": 120, "created_count": 0, "updated_count": 0, "deleted_count": 0},
    )
    @patch("mobile.sync_adapter._try_import_field_list")
    @patch("mobile.sync_adapter._has_google_contacts_sync_token", return_value=False)
    @patch("mobile.sync_adapter._local_contact_count", return_value=120)
    def test_cached_contacts_without_sync_token_establishes_token(self, _count, _token, _field, sync_mock, _fields) -> None:
        result = refresh_contacts_from_google(include_app_fields=True)
        self.assertTrue(result["ok"])
        sync_mock.assert_called_once_with(force_full_fetch=True, skip_drive_reconcile=True)
        self.assertEqual(result["drive_field_updates"], 0)


    @patch(
        "mobile.sync_adapter.sync_google_contact_changes",
        return_value={"changed_count": 0, "created_count": 0, "updated_count": 0, "deleted_count": 0},
    )
    @patch("mobile.sync_adapter._try_import_field_list")
    @patch("mobile.sync_adapter._try_sync_contact_app_fields", return_value=2)
    @patch("mobile.sync_adapter._has_google_contacts_sync_token", return_value=True)
    @patch("mobile.sync_adapter._local_contact_count", return_value=120)
    def test_prefer_cached_skips_bulk_drive_app_fields(
        self,
        _count,
        _token,
        fields_mock,
        _field_list,
        _sync,
    ) -> None:
        result = refresh_contacts_from_google(prefer_cached=True, include_app_fields=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["drive_field_updates"], 0)
        fields_mock.assert_not_called()

    @patch("mobile.sync_adapter._try_sync_contact_app_fields", return_value=0)
    @patch(
        "mobile.sync_adapter.sync_google_contact_changes",
        return_value={"changed_count": 120, "created_count": 0, "updated_count": 120, "deleted_count": 0},
    )
    @patch("mobile.sync_adapter._try_import_field_list")
    @patch("mobile.sync_adapter._has_google_contacts_sync_token", return_value=False)
    @patch("mobile.sync_adapter._local_contact_count", return_value=120)
    def test_prefer_cached_without_sync_token_runs_full_fetch(self, _count, _token, _field, sync_mock, _fields) -> None:
        result = refresh_contacts_from_google(prefer_cached=True, include_app_fields=True)
        self.assertTrue(result["ok"])
        sync_mock.assert_called_once_with(force_full_fetch=True, skip_drive_reconcile=True)
        self.assertEqual(result["changed_count"], 120)
        self.assertEqual(result["drive_field_updates"], 0)


if __name__ == "__main__":
    unittest.main()
