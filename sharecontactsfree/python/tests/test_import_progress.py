"""Contact import must not rewind after a 200-contact batch."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services.recipient_import import (
    _contacts_are_imported,
    _reconcile_import_progress,
    apply_missing_photo_backfill,
)


class ImportProgressTests(unittest.TestCase):
    def test_queued_mid_import_is_not_contacts_done(self) -> None:
        group = {
            "status": "Queued (200/726)",
            "importPhase": "contacts",
            "memberCount": 726,
            "importCursor": 200,
        }
        self.assertFalse(_contacts_are_imported(group))
        self.assertFalse(apply_missing_photo_backfill(group))
        self.assertTrue(str(group["status"]).startswith("Queued"))

    def test_photos_status_is_not_done_when_the_recipient_group_is_empty(self) -> None:
        group = {
            "status": "Photos (100/726)",
            "importPhase": "photos",
            "memberCount": 726,
            "actualCount": 0,
            "importCursor": 726,
        }
        self.assertFalse(_contacts_are_imported(group))

    def test_photos_status_is_not_done_while_contacts_are_still_missing(self) -> None:
        group = {
            "status": "Photos (650/726)",
            "importPhase": "photos",
            "memberCount": 726,
            "actualCount": 200,
            "importCursor": 726,
        }
        self.assertFalse(_contacts_are_imported(group))

    def test_empty_group_drops_stale_mappings_instead_of_skipping_creates(self) -> None:
        group = {
            "importCursor": 726,
            "shareId": "s",
            "owner": "o@x.com",
            "resourceName": "contactGroups/owner",
            "recipientEmail": "r@x.com",
        }
        with (
            patch(
                "app.services.recipient_import.group_member_count",
                return_value=0,
            ),
            patch(
                "app.services.recipient_import._sync_mapping_count",
                side_effect=[726, 726, 0],
            ),
            patch(
                "app.services.recipient_import._forget_mappings_missing_from_group",
                return_value=0,
            ),
            patch(
                "app.services.recipient_import._drop_stale_mappings_for_group"
            ) as drop,
        ):
            def _drop(g):
                g["importCursor"] = 0
                g["importPhase"] = "contacts"

            drop.side_effect = _drop
            start = _reconcile_import_progress(
                object(), group, "contactGroups/g1", 726
            )
        drop.assert_called_once()
        self.assertEqual(start, 0)
        self.assertEqual(int(group["importCursor"]), 0)
        self.assertEqual(int(group["actualCount"]), 0)

    def test_does_not_rewind_cursor_when_google_count_lags(self) -> None:
        # A short Google count that is still > 0 must not rewind a healthy
        # cursor. A true 0 is handled as "group was replaced/emptied".
        group = {"importCursor": 200, "shareId": "s"}
        with (
            patch(
                "app.services.recipient_import.group_member_count",
                return_value=50,
            ),
            patch(
                "app.services.recipient_import._sync_mapping_count",
                return_value=200,
            ),
            patch(
                "app.services.recipient_import._forget_mappings_missing_from_group",
                return_value=0,
            ),
        ):
            start = _reconcile_import_progress(
                object(), group, "contactGroups/g1", 726
            )
        self.assertEqual(start, 200)
        self.assertEqual(int(group["importCursor"]), 200)

    def test_does_not_clear_state_when_member_count_lookup_fails(self) -> None:
        group = {"importCursor": 200, "photoDoneCount": 3}
        with (
            patch(
                "app.services.recipient_import.group_member_count",
                return_value=None,
            ),
            patch(
                "app.services.recipient_import._sync_mapping_count",
                return_value=0,
            ),
        ):
            start = _reconcile_import_progress(
                object(), group, "contactGroups/g1", 726
            )
        self.assertEqual(start, 200)
        self.assertEqual(int(group["importCursor"]), 200)
        self.assertEqual(int(group["photoDoneCount"]), 3)

    def test_uses_mapping_count_when_cursor_was_lost(self) -> None:
        group = {"importCursor": 0}
        with (
            patch(
                "app.services.recipient_import.group_member_count",
                return_value=50,
            ),
            patch(
                "app.services.recipient_import._sync_mapping_count",
                return_value=200,
            ),
            patch(
                "app.services.recipient_import._forget_mappings_missing_from_group",
                return_value=0,
            ),
        ):
            start = _reconcile_import_progress(
                object(), group, "contactGroups/g1", 726
            )
        self.assertEqual(start, 200)
        self.assertEqual(int(group["actualCount"]), 50)


if __name__ == "__main__":
    unittest.main()
