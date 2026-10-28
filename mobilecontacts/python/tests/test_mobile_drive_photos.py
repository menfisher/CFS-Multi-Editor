"""Tests for mobile Drive cropped photo sync helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.database import get_connection, initialize_database, set_request_db_path, set_request_uploads_dir
from app.services.google_sync_service import (
    _apply_shared_contact_app_fields,
    _contact_has_usable_local_photo,
    _contact_needs_photo_sync,
    get_contact_photo_storage_dir,
)


class MobileDrivePhotoSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        root = Path(self._tmpdir.name)
        db_path = root / "app.sqlite3"
        uploads_dir = root / "uploads"
        set_request_db_path(db_path)
        set_request_uploads_dir(uploads_dir)
        initialize_database()
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO contacts (
                  id, family_name, given_name, photo, photo_drive_file_id, photo_sync_revision
                ) VALUES (5613, 'Anderson', 'Jason', '', '', 0)
                """
            )
            conn.commit()

    def tearDown(self) -> None:
        set_request_db_path(None)
        set_request_uploads_dir(None)
        self._tmpdir.cleanup()

    def test_apply_shared_contact_downloads_custom_photo(self) -> None:
        photo_path = "/static/uploads/contact_photos/contact_5613_photo.jpg"
        remote_row = {
            "google_contact_id": "people/c123",
            "mtg_home_elder_flag": "1",
            "birthday": "1990-01-01",
            "photo": photo_path,
            "photo_drive_file_id": "drive-file-abc",
            "photo_sync_revision": 3,
        }
        local_row = {
            "id": 5613,
            "google_contact_id": "people/c123",
            "photo": "https://lh3.googleusercontent.com/contacts/example=s100",
            "photo_drive_file_id": "",
            "photo_sync_revision": 0,
        }
        fake_bytes = b"\xff\xd8\xff fake jpeg bytes for test"

        with patch(
            "app.services.google_sync_service._drive_load_file_content",
            return_value=fake_bytes,
        ):
            applied = _apply_shared_contact_app_fields(
                local_row,
                remote_row,
                remote_revision=5,
                access_token="token",
            )

        self.assertTrue(applied)
        with get_connection() as conn:
            row = conn.execute(
                "SELECT photo, photo_drive_file_id, photo_sync_revision, mtg_home_elder_flag FROM contacts WHERE id = 5613"
            ).fetchone()
        self.assertEqual(row["photo"], photo_path)
        self.assertEqual(row["photo_drive_file_id"], "drive-file-abc")
        self.assertEqual(int(row["photo_sync_revision"]), 3)
        self.assertEqual(row["mtg_home_elder_flag"], "1")
        local_file = get_contact_photo_storage_dir() / "contact_5613_photo.jpg"
        self.assertTrue(local_file.is_file())
        self.assertEqual(local_file.read_bytes(), fake_bytes)
        self.assertTrue(_contact_has_usable_local_photo(dict(row)))
        self.assertFalse(_contact_needs_photo_sync(dict(row), remote_row))

    def test_needs_photo_sync_when_local_file_missing(self) -> None:
        local_row = {
            "photo": "/static/uploads/contact_photos/contact_5613_photo.jpg",
            "photo_drive_file_id": "drive-file-abc",
            "photo_sync_revision": 3,
        }
        remote_row = {
            "photo": "/static/uploads/contact_photos/contact_5613_photo.jpg",
            "photo_drive_file_id": "drive-file-abc",
            "photo_sync_revision": 3,
        }
        self.assertTrue(_contact_needs_photo_sync(local_row, remote_row))


if __name__ == "__main__":
    unittest.main()
