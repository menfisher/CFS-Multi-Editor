from __future__ import annotations

import unittest
from unittest.mock import patch

from mobile import sync_adapter


class MobileSharedGroupLabelTests(unittest.TestCase):
    def test_label_loaded_from_drive_before_sync_upload(self) -> None:
        calls: list[str] = []
        with patch.object(
            sync_adapter, "import_book_layouts_from_google_drive", side_effect=lambda **_: calls.append("labels") or True
        ), patch.object(
            sync_adapter, "sync_mobile_editor_contact_changes", side_effect=lambda: calls.append("upload") or {"ok": True}
        ):
            sync_adapter.sync_contact_changes(upload=True)
        self.assertEqual(calls, ["labels", "upload"])

    def test_label_loaded_from_drive_before_upload_only(self) -> None:
        calls: list[str] = []
        with patch.object(
            sync_adapter, "import_book_layouts_from_google_drive", side_effect=lambda **_: calls.append("labels") or True
        ), patch.object(
            sync_adapter, "upload_google_contacts_only", side_effect=lambda: calls.append("upload") or {"ok": True}
        ):
            sync_adapter.upload_contacts_only()
        self.assertEqual(calls, ["labels", "upload"])

    def test_upload_refused_when_drive_unreadable_and_label_is_old_default(self) -> None:
        with patch.object(
            sync_adapter, "import_book_layouts_from_google_drive", side_effect=RuntimeError("drive down")
        ), patch(
            "app.services.preset_service.get_shared_contacts_group_name",
            return_value=sync_adapter.LEGACY_SHARED_GROUP_NAME,
        ), patch.object(sync_adapter, "sync_mobile_editor_contact_changes") as upload:
            with self.assertRaises(RuntimeError):
                sync_adapter.sync_contact_changes(upload=True)
        upload.assert_not_called()

    def test_upload_allowed_when_drive_unreadable_but_label_already_loaded(self) -> None:
        with patch.object(
            sync_adapter, "import_book_layouts_from_google_drive", side_effect=RuntimeError("drive down")
        ), patch(
            "app.services.preset_service.get_shared_contacts_group_name", return_value="AlMsLa (Shared)"
        ), patch.object(sync_adapter, "sync_mobile_editor_contact_changes", return_value={"ok": True}) as upload:
            sync_adapter.sync_contact_changes(upload=True)
        upload.assert_called_once()

    def test_download_only_sync_does_not_need_the_label(self) -> None:
        with patch.object(sync_adapter, "import_book_layouts_from_google_drive") as labels, patch.object(
            sync_adapter, "sync_google_contact_changes", return_value={"changed_count": 0}
        ), patch.object(sync_adapter, "_has_google_contacts_sync_token", return_value=True):
            sync_adapter.sync_contact_changes(upload=False)
        labels.assert_not_called()


if __name__ == "__main__":
    unittest.main()
