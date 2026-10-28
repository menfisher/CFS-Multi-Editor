"""Tests for reset/relink re-imports using the fast batchCreateContacts path.

_queue_unmapped_member_retries must only queue already-passed gaps (below importCursor),
not not-yet-reached contacts, so a fresh re-import flows through batchCreateContacts
instead of the slow one-at-a-time retry queue.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import db
from app.services.recipient_import import _queue_unmapped_member_retries

OWNER = "owner@example.com"
RECIPIENT = "recip@example.com"
RESOURCE = "contactGroups/g1"
SHARE_ID = "share-xyz"


def _members(n: int) -> list[dict]:
    return [{"resourceName": f"people/{i}", "names": [{"displayName": f"c{i}"}]} for i in range(n)]


def _group(cursor: int) -> dict:
    return {
        "shareId": SHARE_ID,
        "owner": OWNER,
        "resourceName": RESOURCE,
        "recipientEmail": RECIPIENT,
        "importCursor": cursor,
    }


class QueueUnmappedRetriesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._prev = db._active_database_path
        db._active_database_path = Path(self._tmpdir.name) / "test.db"
        db.init_db()

    def tearDown(self) -> None:
        db._active_database_path = self._prev
        self._tmpdir.cleanup()

    def _map(self, idx: int) -> None:
        db.upsert_sync_mapping(
            share_id=SHARE_ID,
            owner=OWNER,
            group_resource_name=RESOURCE,
            recipient_email=RECIPIENT,
            owner_person_id=f"people/{idx}",
            recipient_person_id=f"people/r{idx}",
            last_hash="h",
            last_photo_hash="",
            owner_etag="",
        )

    def test_fresh_reset_queues_nothing(self) -> None:
        """importCursor=0 (fresh reset) must not force any contact into single-create retry."""
        retry: list[int] = []
        added = _queue_unmapped_member_retries(_group(0), _members(722), retry)
        self.assertEqual(added, 0)
        self.assertEqual(retry, [])

    def test_only_passed_gaps_are_queued(self) -> None:
        # Cursor moved past 5 contacts; index 2 never got a mapping (a real gap).
        for idx in (0, 1, 3, 4):
            self._map(idx)
        retry: list[int] = []
        added = _queue_unmapped_member_retries(_group(5), _members(10), retry)
        self.assertEqual(added, 1)
        self.assertEqual(retry, [2])

    def test_not_yet_reached_contacts_left_for_batch(self) -> None:
        # Cursor at 3, all below mapped; indexes >= 3 are unmapped but must NOT be queued.
        for idx in (0, 1, 2):
            self._map(idx)
        retry: list[int] = []
        added = _queue_unmapped_member_retries(_group(3), _members(10), retry)
        self.assertEqual(added, 0)
        self.assertEqual(retry, [])


if __name__ == "__main__":
    unittest.main()
