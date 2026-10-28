"""Retry must keep uploading photos after a group was already marked Shared."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db
from app.services import app_data as app_data_svc
from app.services.recipient_import import (
    _apply_photo_progress_fields,
    _ensure_photo_work_queue,
    _photo_count_cap,
    _photos_import_complete,
    _raise_member_count_to_known_total,
    apply_missing_photo_backfill,
    group_needs_photo_backfill,
)


OWNER = "owner@example.com"
RECIPIENT = "friend@example.com"
RESOURCE = "contactGroups/g1"
SHARE_ID = "share-photos"


def _photo_member(idx: int) -> dict:
    return {
        "resourceName": f"people/{idx}",
        "names": [{"displayName": f"c{idx}"}],
        "photos": [{"url": f"https://example.com/p{idx}.jpg", "default": False}],
    }


class PhotoBackfillTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._prev = db._active_database_path
        db._active_database_path = Path(self._tmpdir.name) / "test.db"
        db.init_db()

    def tearDown(self) -> None:
        db._active_database_path = self._prev
        self._tmpdir.cleanup()

    def _map(self, idx: int, photo_hash: str = "") -> None:
        db.upsert_sync_mapping(
            share_id=SHARE_ID,
            owner=OWNER,
            group_resource_name=RESOURCE,
            recipient_email=RECIPIENT,
            owner_person_id=f"people/{idx}",
            recipient_person_id=f"people/r{idx}",
            last_hash="h",
            last_photo_hash=photo_hash,
            owner_etag="",
        )

    def _shared_group(self, *, done: int, target: int, members: int) -> dict:
        for idx in range(members):
            self._map(idx)
        db.write_shared_contacts(
            SHARE_ID,
            OWNER,
            "Friends",
            RESOURCE,
            [_photo_member(i) for i in range(members)],
        )
        return {
            "shareId": SHARE_ID,
            "owner": OWNER,
            "resourceName": RESOURCE,
            "recipientEmail": RECIPIENT,
            "status": "Shared",
            "importPhase": "done",
            "memberCount": members,
            "importCursor": members,
            "actualCount": members,
            "photoDoneCount": done,
            "photoTargetCount": target,
            "photoPendingIndexes": [],
            "photoRetryIndexes": [],
        }

    def test_photos_import_complete_does_not_drop_pending_queue(self) -> None:
        pending = [4, 5]
        retry: list[int] = []
        self.assertFalse(_photos_import_complete(3, 3, pending, retry))
        self.assertEqual(pending, [4, 5])

    def test_shared_group_with_unfinished_photos_needs_backfill(self) -> None:
        group = self._shared_group(done=2, target=2, members=5)
        self.assertTrue(group_needs_photo_backfill(group))
        self.assertFalse(app_data_svc._recipient_is_fully_shared(group))
        self.assertFalse(
            app_data_svc._recipient_skip_owner_push(
                group,
                share_id=SHARE_ID,
                owner=OWNER,
                resource_name=RESOURCE,
                recipient_email=RECIPIENT,
                reset=False,
                owner_member_count=5,
            )
        )

    def test_retry_reopens_photo_pass_after_shared(self) -> None:
        group = self._shared_group(done=2, target=2, members=5)
        self.assertTrue(apply_missing_photo_backfill(group))
        self.assertTrue(str(group["status"]).startswith("Photos ("))
        self.assertEqual(group["importPhase"], "photos")
        self.assertFalse(group.get("photoNeedsRecipientScan"))
        self.assertEqual(len(group.get("photoPendingIndexes") or []), 5)
        self.assertEqual(int(group["photoTargetCount"]), 5)

    def test_retry_share_push_reopens_photos_instead_of_rewinding(self) -> None:
        group = self._shared_group(done=2, target=2, members=5)
        db.save_app_data(RECIPIENT, {"sharedGroups": [group]})
        with (
            patch("app.services.app_data.recipient_credentials", return_value=object()),
            patch("app.services.push_worker.enqueue_full_group_push", return_value=9),
            patch("app.db.has_active_push_job", return_value=False),
            patch("app.db.reclaim_stale_push_jobs"),
        ):
            app_data_svc.retry_share_push(RECIPIENT, OWNER, RESOURCE)
        saved = (db.get_app_data(RECIPIENT) or {})["sharedGroups"][0]
        self.assertTrue(str(saved["status"]).startswith("Photos ("))
        self.assertEqual(saved["importPhase"], "photos")
        self.assertFalse(str(saved["status"]).startswith("Pushing"))
        self.assertFalse(saved.get("photoNeedsRecipientScan"))
        self.assertEqual(len(saved.get("photoPendingIndexes") or []), 5)

    def test_retry_share_push_backfills_even_if_job_already_running(self) -> None:
        group = self._shared_group(done=2, target=2, members=5)
        db.save_app_data(RECIPIENT, {"sharedGroups": [group]})
        with (
            patch("app.services.app_data.recipient_credentials", return_value=object()),
            patch("app.services.push_worker.enqueue_full_group_push", return_value=9),
            patch("app.db.has_active_push_job", return_value=True),
            patch("app.db.reclaim_stale_push_jobs"),
        ):
            result = app_data_svc.retry_share_push(RECIPIENT, OWNER, RESOURCE)
        self.assertEqual(result["status"], "already_running")
        saved = (db.get_app_data(RECIPIENT) or {})["sharedGroups"][0]
        self.assertTrue(str(saved["status"]).startswith("Photos ("))
        self.assertFalse(saved.get("photoNeedsRecipientScan"))
        self.assertEqual(len(saved.get("photoPendingIndexes") or []), 5)

    def test_retry_queues_only_missing_recipient_photos(self) -> None:
        group = self._shared_group(done=5, target=5, members=5)
        with patch(
            "app.services.recipient_import._missing_recipient_photo_indexes",
            return_value=([2, 4], 3),
        ):
            self.assertTrue(
                apply_missing_photo_backfill(group, force=True, creds=object())
            )
        self.assertEqual(sorted(group.get("photoPendingIndexes") or []), [2, 4])
        self.assertEqual(int(group["photoDoneCount"]), 3)
        self.assertFalse(group.get("photoNeedsRecipientScan"))
        self.assertTrue(str(group["status"]).startswith("Photos (3/"))

    def test_retry_does_not_requeue_stale_photos_after_done_job(self) -> None:
        group = self._shared_group(done=1, target=2, members=2)
        group["status"] = "Photos (180/462)"
        group["importPhase"] = "photos"
        db.save_app_data(RECIPIENT, {"sharedGroups": [group]})
        job_id = db.enqueue_push_job(
            owner=OWNER,
            resource_name=RESOURCE,
            recipient_email=RECIPIENT,
            job_type="full_group",
            payload={},
        )
        db.update_push_job(job_id, status="done")
        with (
            patch("app.services.app_data.recipient_credentials", return_value=object()),
            patch(
                "app.services.push_worker.enqueue_full_group_push",
                return_value=99,
            ) as enqueue,
            patch("app.db.has_active_push_job", return_value=False),
            patch("app.db.reclaim_stale_push_jobs"),
            patch(
                "app.services.recipient_import._missing_recipient_photo_indexes",
                return_value=([], 2),
            ),
        ):
            result = app_data_svc.retry_share_push(RECIPIENT, OWNER, RESOURCE)
        enqueue.assert_not_called()
        self.assertEqual(result["status"], "already_complete")
        saved = (db.get_app_data(RECIPIENT) or {})["sharedGroups"][0]
        self.assertTrue(str(saved.get("status") or "").startswith("Shared"))

    def test_retry_share_push_requeues_when_done_count_already_matches(self) -> None:
        group = self._shared_group(done=5, target=5, members=5)
        db.save_app_data(RECIPIENT, {"sharedGroups": [group]})
        with (
            patch("app.services.app_data.recipient_credentials", return_value=object()),
            patch("app.services.push_worker.enqueue_full_group_push", return_value=9),
            patch("app.db.has_active_push_job", return_value=False),
            patch("app.db.reclaim_stale_push_jobs"),
        ):
            app_data_svc.retry_share_push(RECIPIENT, OWNER, RESOURCE)
        saved = (db.get_app_data(RECIPIENT) or {})["sharedGroups"][0]
        self.assertTrue(str(saved["status"]).startswith("Photos ("))
        self.assertEqual(int(saved["photoDoneCount"]), 5)
        self.assertTrue(saved.get("photoNeedsRecipientScan"))
        self.assertEqual(len(saved.get("photoPendingIndexes") or []), 0)

    def test_force_without_creds_refills_queue_from_mapped_photos(self) -> None:
        group = self._shared_group(done=4, target=5, members=5)
        self.assertTrue(apply_missing_photo_backfill(group, force=True))
        self.assertFalse(group.get("photoNeedsRecipientScan"))
        self.assertEqual(len(group.get("photoPendingIndexes") or []), 5)
        self.assertEqual(int(group["photoDoneCount"]), 4)

    def test_empty_queue_after_first_photo_batch_refills_remaining(self) -> None:
        group = self._shared_group(done=50, target=50, members=80)
        pending: list[int] = []
        retry: list[int] = []
        members = [_photo_member(i) for i in range(80)]
        targets = {str(i): f"people/r{i}" for i in range(80)}
        stuck = _ensure_photo_work_queue(
            members, targets, pending, retry, 50, 50
        )
        self.assertFalse(stuck)
        self.assertEqual(len(pending), 80)
        self.assertTrue(apply_missing_photo_backfill(group))
        self.assertEqual(len(group.get("photoPendingIndexes") or []), 80)
        self.assertEqual(group["importPhase"], "photos")
        self.assertFalse(str(group["status"]).startswith("Shared"))

    def test_photo_progress_fields_never_decrease_done_or_target(self) -> None:
        group = {
            "photoDoneCount": 12,
            "photoTargetCount": 20,
        }
        target = _apply_photo_progress_fields(
            group,
            photo_pending=[13, 14],
            photo_retry=[],
            photo_retry_attempts={},
            photo_targets={"0": "people/r0", "1": "people/r1"},
            photo_done=4,
        )
        self.assertEqual(int(group["photoDoneCount"]), 12)
        self.assertEqual(int(group["photoTargetCount"]), 20)
        self.assertEqual(target, 20)

    def test_photo_counts_cannot_exceed_member_count(self) -> None:
        group = {
            "memberCount": 726,
            "photoDoneCount": 762,
            "photoTargetCount": 762,
        }
        target = _apply_photo_progress_fields(
            group,
            photo_pending=[],
            photo_retry=[],
            photo_retry_attempts={},
            photo_targets={str(i): f"people/r{i}" for i in range(5)},
            photo_done=800,
        )
        self.assertEqual(int(group["photoDoneCount"]), 726)
        self.assertEqual(int(group["photoTargetCount"]), 726)
        self.assertEqual(target, 726)

    def test_stale_member_count_does_not_freeze_photo_progress(self) -> None:
        group = {
            "memberCount": 725,
            "actualCount": 727,
            "importCursor": 726,
            "photoDoneCount": 725,
            "photoPendingIndexes": list(range(354)),
        }
        self.assertEqual(_raise_member_count_to_known_total(group), 727)
        self.assertEqual(int(group["memberCount"]), 727)
        self.assertGreater(_photo_count_cap(group, 726), 725)
        self.assertFalse(_photos_import_complete(725, 725, [0], []))

    def test_force_backfill_keeps_higher_stored_done(self) -> None:
        group = self._shared_group(done=4, target=5, members=5)
        with patch(
            "app.services.recipient_import._missing_recipient_photo_indexes",
            return_value=([3, 4], 3),
        ) as lookup:
            self.assertTrue(
                apply_missing_photo_backfill(group, force=True, creds=object())
            )
            lookup.assert_not_called()
        self.assertEqual(int(group["photoDoneCount"]), 4)
        self.assertEqual(len(group.get("photoPendingIndexes") or []), 5)

    def test_finished_photos_do_not_backfill(self) -> None:
        for idx in range(3):
            self._map(idx, photo_hash="uploaded")
        db.write_shared_contacts(
            SHARE_ID,
            OWNER,
            "Friends",
            RESOURCE,
            [_photo_member(i) for i in range(3)],
        )
        group = {
            "shareId": SHARE_ID,
            "owner": OWNER,
            "resourceName": RESOURCE,
            "recipientEmail": RECIPIENT,
            "status": "Shared",
            "importPhase": "done",
            "memberCount": 3,
            "importCursor": 3,
            "actualCount": 3,
            "photoDoneCount": 3,
            "photoTargetCount": 3,
            "photoPendingIndexes": [],
            "photoRetryIndexes": [],
        }
        self.assertFalse(group_needs_photo_backfill(group))
        self.assertFalse(apply_missing_photo_backfill(group))
        self.assertTrue(str(group["status"]).startswith("Shared"))
        self.assertTrue(
            app_data_svc._recipient_skip_owner_push(
                group,
                share_id=SHARE_ID,
                owner=OWNER,
                resource_name=RESOURCE,
                recipient_email=RECIPIENT,
                reset=False,
                owner_member_count=3,
            )
        )


class CustomPhotoDetectionTests(unittest.TestCase):
    def test_default_user_url_is_not_a_custom_photo(self) -> None:
        from app.people_recipient import has_custom_contact_photo

        person = {
            "photos": [
                {
                    "url": "https://lh3.googleusercontent.com/a/default-user=s100",
                    "metadata": {
                        "primary": True,
                        "source": {"type": "CONTACT", "id": "x"},
                    },
                }
            ]
        }
        self.assertFalse(has_custom_contact_photo(person))

    def test_profile_picture_does_not_count_when_contact_photo_is_placeholder(self) -> None:
        from app.people_recipient import has_custom_contact_photo

        person = {
            "photos": [
                {
                    "url": "https://lh3.googleusercontent.com/a/default-user=s100",
                    "metadata": {
                        "primary": True,
                        "source": {"type": "CONTACT", "id": "x"},
                    },
                },
                {
                    "url": "https://lh3.googleusercontent.com/a-/ALV-UjVrealphoto=s100",
                    "metadata": {
                        "source": {"type": "PROFILE", "id": "y"},
                    },
                },
            ]
        }
        self.assertFalse(has_custom_contact_photo(person))

    def test_uploaded_contact_photo_counts(self) -> None:
        from app.people_recipient import has_custom_contact_photo

        person = {
            "photos": [
                {
                    "url": "https://lh3.googleusercontent.com/contacts/AG6tpzEreal=s100",
                    "metadata": {
                        "primary": True,
                        "source": {"type": "CONTACT", "id": "x"},
                    },
                }
            ]
        }
        self.assertTrue(has_custom_contact_photo(person))


if __name__ == "__main__":
    unittest.main()
