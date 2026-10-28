"""Photo import should use large batches and stop after the first full-res fetch."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.config import RECIPIENT_IMPORT_PHOTO_BATCH_SIZE
from app.people_recipient import fetch_photo_bytes
from app.services.recipient_import import _photo_import_chunk_size


class PhotoImportSpeedTests(unittest.TestCase):
    def test_photo_batches_stay_large_through_the_tail(self) -> None:
        self.assertGreaterEqual(RECIPIENT_IMPORT_PHOTO_BATCH_SIZE, 20)
        self.assertEqual(_photo_import_chunk_size(30, 200), RECIPIENT_IMPORT_PHOTO_BATCH_SIZE)
        self.assertEqual(_photo_import_chunk_size(8, 200), 8)
        self.assertEqual(_photo_import_chunk_size(726, 12), 12)

    def test_fetch_stops_at_first_working_full_resolution_url(self) -> None:
        calls: list[str] = []

        def _fake_fetch(url: str, token: str, *, client=None) -> bytes | None:
            calls.append(url)
            if "2048" in url or "=s2048" in url:
                return b"full-res-photo-bytes"
            return b"thumbnail"

        with patch("app.people_recipient._fetch_url_bytes", side_effect=_fake_fetch):
            payload = fetch_photo_bytes(
                "https://lh3.googleusercontent.com/photo=s100", "token"
            )

        self.assertTrue(payload)
        self.assertEqual(len(calls), 1)
        self.assertTrue("2048" in calls[0] or "=s2048" in calls[0])


if __name__ == "__main__":
    unittest.main()
