import unittest
from unittest.mock import patch

from app.services import google_sync_service as gss


class BorrowedDriveRootStorageTests(unittest.TestCase):
    def test_borrowed_token_finds_owner_root_without_using_signed_in_account(self) -> None:
        with (
            patch.object(gss, "_get_valid_access_token") as current_token,
            patch.object(gss, "_store_drive_state") as store_state,
            patch.object(gss, "_load_google_account") as load_account,
            patch.object(
                gss,
                "_drive_find_best_root_folder",
                return_value={"id": "owner-root"},
            ) as find_root,
        ):
            storage = gss.ensure_google_drive_root_storage(access_token="owner-token")

        self.assertEqual(storage["root_folder_id"], "owner-root")
        find_root.assert_called_once_with("owner-token")
        current_token.assert_not_called()
        load_account.assert_not_called()
        store_state.assert_not_called()

    def test_shared_storage_uses_borrowed_token_for_root_lookup(self) -> None:
        children = {
            gss.GOOGLE_DRIVE_SHARED_CONTACT_RECORDS_FOLDER_NAME: {
                "id": "records",
                "mimeType": "application/vnd.google-apps.folder",
            },
            gss.GOOGLE_DRIVE_SHARED_CONTACT_PHOTOS_FOLDER_NAME: {
                "id": "photos",
                "mimeType": "application/vnd.google-apps.folder",
            },
            gss.GOOGLE_DRIVE_CONTACTS_CURRENT_FILE_NAME: {
                "id": "contacts",
                "mimeType": "application/json",
            },
            gss.GOOGLE_DRIVE_SHARED_SYNC_STATE_FILE_NAME: {
                "id": "sync",
                "mimeType": "application/json",
            },
            gss.GOOGLE_DRIVE_CHANGES_LIST_CURRENT_FILE_NAME: {
                "id": "changes",
                "mimeType": "application/json",
            },
            gss.GOOGLE_DRIVE_EDITOR_LOCK_FILE_NAME: {
                "id": "lock",
                "mimeType": "application/json",
            },
            gss.GOOGLE_DRIVE_CONTACTS_FULL_FILE_NAME: {
                "id": "full",
                "mimeType": "application/json",
            },
            gss.GOOGLE_DRIVE_MEETINGDATA_CURRENT_FILE_NAME: {
                "id": "meetings",
                "mimeType": "application/json",
            },
            gss.GOOGLE_DRIVE_FIELD_LIST_CURRENT_FILE_NAME: {
                "id": "fields",
                "mimeType": "application/json",
            },
            gss.GOOGLE_DRIVE_SETTINGS_CURRENT_FILE_NAME: {
                "id": "settings",
                "mimeType": "application/json",
            },
            gss.GOOGLE_DRIVE_BOOK_LAYOUTS_CURRENT_FILE_NAME: {
                "id": "layouts",
                "mimeType": "application/json",
            },
        }
        gss._SHARED_DRIVE_STORAGE_CACHE.clear()
        gss._CONTACT_ONLY_SHARED_DRIVE_STORAGE_CACHE.clear()
        with (
            patch.object(
                gss,
                "ensure_google_drive_root_storage",
                return_value={"root_folder_id": "owner-root"},
            ) as ensure_root,
            patch.object(
                gss,
                "_drive_find_child",
                return_value={
                    "id": "shared",
                    "mimeType": "application/vnd.google-apps.folder",
                },
            ),
            patch.object(gss, "_drive_children_by_name", return_value=children),
            patch.object(gss, "_get_valid_access_token") as current_token,
        ):
            result = gss._ensure_shared_drive_storage("owner-token")

        ensure_root.assert_called_with(access_token="owner-token")
        current_token.assert_not_called()
        self.assertEqual(result["changes_list_current_file_id"], "changes")
        self.assertEqual(result["root_folder_id"], "owner-root")


if __name__ == "__main__":
    unittest.main()
