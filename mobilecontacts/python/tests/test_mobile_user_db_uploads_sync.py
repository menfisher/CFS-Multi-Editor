from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mobile.user_db import _sync_uploads_tree, _uploads_tree_signature


class MobileUserDbUploadsSyncTests(unittest.TestCase):
    def test_sync_uploads_copies_newer_and_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "cloud" / "uploads"
            dest = root / "local" / "uploads"
            photo_dir = source / "contact_photos"
            photo_dir.mkdir(parents=True)
            source_file = photo_dir / "joe.jpg"
            source_file.write_bytes(b"photo-bytes")

            _sync_uploads_tree(source, dest)

            copied = dest / "contact_photos" / "joe.jpg"
            self.assertTrue(copied.is_file())
            self.assertEqual(copied.read_bytes(), b"photo-bytes")

            source_file.write_bytes(b"updated-photo")
            _sync_uploads_tree(source, dest)
            self.assertEqual(copied.read_bytes(), b"updated-photo")

    def test_uploads_signature_changes_when_file_added(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            uploads = Path(temp_dir) / "uploads" / "contact_photos"
            uploads.mkdir(parents=True)
            before = _uploads_tree_signature(uploads.parent)
            (uploads / "a.jpg").write_bytes(b"a")
            after = _uploads_tree_signature(uploads.parent)
            self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
