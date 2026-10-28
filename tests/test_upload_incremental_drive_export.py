import unittest

from app.services.google_sync_service import (
    _can_skip_drive_manifest_read_for_incremental_export,
    _should_queue_local_ahead_shared_app_fields_on_drive_export,
)


class UploadIncrementalDriveExportTests(unittest.TestCase):
    def test_skip_manifest_read_for_single_dirty_contact(self) -> None:
        self.assertTrue(
            _can_skip_drive_manifest_read_for_incremental_export(
                contacts_manifest_needs_drive_export=False,
                pending_contact_export_count=1,
                pending_photo_export_count=0,
                bootstrap_remaining_count=0,
            )
        )

    def test_incremental_upload_skips_library_app_field_scan(self) -> None:
        self.assertFalse(
            _should_queue_local_ahead_shared_app_fields_on_drive_export(
                skip_manifest_read=True,
                bootstrap_needed=False,
                contacts_manifest_needs_drive_export=False,
            )
        )

    def test_bootstrap_still_scans_app_fields(self) -> None:
        self.assertTrue(
            _should_queue_local_ahead_shared_app_fields_on_drive_export(
                skip_manifest_read=False,
                bootstrap_needed=True,
                contacts_manifest_needs_drive_export=False,
            )
        )


if __name__ == "__main__":
    unittest.main()
