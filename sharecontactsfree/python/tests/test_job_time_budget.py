"""A poll-driven import pass must stay inside the caller's slice.

Cloud Run kills any request that runs past 300s, and import work runs inside
poll/kick requests. A pass that ignores the caller's budget shows up in the UI
as a frozen counter.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db
from app.config import RECIPIENT_IMPORT_TIME_BUDGET_MS
from app.services import push_worker
from app.services.recipient_import import resolve_import_time_budget_ms

OWNER = "owner@example.com"
RECIPIENT = "friend@example.com"
RESOURCE = "contactGroups/g1"


class ImportTimeBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._prev = db._active_database_path
        db._active_database_path = Path(self._tmpdir.name) / "test.db"
        db.init_db()

    def tearDown(self) -> None:
        db._active_database_path = self._prev
        self._tmpdir.cleanup()

    def test_pass_budget_never_exceeds_default(self) -> None:
        self.assertEqual(resolve_import_time_budget_ms(5_000), 5_000)
        self.assertEqual(resolve_import_time_budget_ms(0), 1_000)
        self.assertGreaterEqual(RECIPIENT_IMPORT_TIME_BUDGET_MS, 180_000)
        self.assertEqual(
            resolve_import_time_budget_ms(10_000_000), RECIPIENT_IMPORT_TIME_BUDGET_MS
        )
        self.assertEqual(resolve_import_time_budget_ms(None), RECIPIENT_IMPORT_TIME_BUDGET_MS)

    def test_hard_timeout_stays_under_cloud_run_request_limit(self) -> None:
        self.assertLess(push_worker.MAX_JOB_HARD_SECONDS, 300)

    def test_watchdog_does_not_shorten_poll_slices(self) -> None:
        self.assertEqual(
            push_worker._watchdog_join_seconds(25),
            float(push_worker.MAX_JOB_HARD_SECONDS),
        )
        self.assertEqual(
            push_worker._watchdog_join_seconds(None),
            float(push_worker.MAX_JOB_HARD_SECONDS),
        )

    def test_priority_email_is_claimed_before_older_jobs(self) -> None:
        charlie = db.enqueue_push_job(
            owner=OWNER,
            resource_name=RESOURCE,
            recipient_email="charliekerr.inbox@gmail.com",
            job_type="full_group",
            payload={},
        )
        emmy = db.enqueue_push_job(
            owner=OWNER,
            resource_name=RESOURCE,
            recipient_email="emmynelson1970@gmail.com",
            job_type="full_group",
            payload={},
        )
        with patch.dict(
            "os.environ",
            {"SHARE_JOB_PRIORITY_EMAIL": "emmynelson1970@gmail.com"},
        ):
            claimed = db.claim_push_jobs(1)
        self.assertEqual(len(claimed), 1)
        self.assertEqual(int(claimed[0]["id"]), emmy)
        self.assertNotEqual(int(claimed[0]["id"]), charlie)

    def test_newest_import_is_claimed_before_older_leftover(self) -> None:
        leftover = db.enqueue_push_job(
            owner=OWNER,
            resource_name=RESOURCE,
            recipient_email="emmynelson1970@gmail.com",
            job_type="full_group",
            payload={"resume": True},
        )
        newest = db.enqueue_push_job(
            owner=OWNER,
            resource_name=RESOURCE,
            recipient_email="joanna.bishop901@gmail.com",
            job_type="full_group",
            payload={},
        )
        claimed = db.claim_push_jobs(1)
        self.assertEqual(len(claimed), 1)
        self.assertEqual(int(claimed[0]["id"]), newest)
        self.assertNotEqual(int(claimed[0]["id"]), leftover)

    def test_shared_stale_photo_counter_is_not_resumed(self) -> None:
        db.save_app_data(
            RECIPIENT,
            {
                "sharedGroups": [
                    {
                        "owner": OWNER,
                        "resourceName": RESOURCE,
                        "recipientEmail": RECIPIENT,
                        "shareId": "share-stale-photos",
                        "status": "Shared",
                        "importPhase": "done",
                        "memberCount": 727,
                        "actualCount": 727,
                        "photoDoneCount": 111,
                        "photoTargetCount": 725,
                    }
                ]
            },
        )
        self.assertEqual(
            push_worker.resume_stalled_imports_for_recipient(RECIPIENT), 0
        )

    def _finished_share_with_stale_photos_status(self) -> str:
        share_id = "share-stale-photos-row"
        for idx in range(2):
            db.upsert_sync_mapping(
                share_id=share_id,
                owner=OWNER,
                group_resource_name=RESOURCE,
                recipient_email=RECIPIENT,
                owner_person_id=f"people/{idx}",
                recipient_person_id=f"people/r{idx}",
            )
        db.save_app_data(
            RECIPIENT,
            {
                "sharedGroups": [
                    {
                        "owner": OWNER,
                        "resourceName": RESOURCE,
                        "recipientEmail": RECIPIENT,
                        "shareId": share_id,
                        "status": "Photos (180/462)",
                        "importPhase": "photos",
                        "memberCount": 2,
                        "actualCount": 2,
                        "importCursor": 2,
                        "photoDoneCount": 180,
                        "photoTargetCount": 462,
                        "photoPendingIndexes": [],
                        "photoRetryIndexes": [],
                    }
                ]
            },
        )
        job_id = db.enqueue_push_job(
            owner=OWNER,
            resource_name=RESOURCE,
            recipient_email=RECIPIENT,
            job_type="full_group",
            payload={},
        )
        db.update_push_job(job_id, status="done")
        return share_id

    def test_stale_photos_row_after_done_job_is_not_resumed(self) -> None:
        self._finished_share_with_stale_photos_status()
        group = (db.get_app_data(RECIPIENT) or {})["sharedGroups"][0]
        self.assertTrue(
            push_worker.recipient_finished_import_is_stale_photo_row(
                group, RECIPIENT
            )
        )
        self.assertFalse(push_worker._group_needs_import_job(group, RECIPIENT))
        self.assertEqual(
            push_worker.resume_stalled_imports_for_recipient(RECIPIENT), 0
        )
        saved = (db.get_app_data(RECIPIENT) or {})["sharedGroups"][0]
        self.assertTrue(str(saved.get("status") or "").startswith("Shared"))
        self.assertEqual(saved.get("importPhase"), "done")
        self.assertEqual(
            db.count_queued_push_jobs(),
            0,
        )

    def test_cancelled_leftover_job_does_not_reopen_finished_import(self) -> None:
        self._finished_share_with_stale_photos_status()
        leftover = db.enqueue_push_job(
            owner=OWNER,
            resource_name=RESOURCE,
            recipient_email=RECIPIENT,
            job_type="full_group",
            payload={},
        )
        db.update_push_job(
            leftover, status="error", last_error="cancelled: unlabeled duplicate cleanup"
        )
        self.assertEqual(
            push_worker.resume_stalled_imports_for_recipient(RECIPIENT), 0
        )
        saved = (db.get_app_data(RECIPIENT) or {})["sharedGroups"][0]
        self.assertTrue(str(saved.get("status") or "").startswith("Shared"))

    def test_open_photo_queue_is_still_resumed(self) -> None:
        share_id = "share-open-photos"
        db.upsert_sync_mapping(
            share_id=share_id,
            owner=OWNER,
            group_resource_name=RESOURCE,
            recipient_email=RECIPIENT,
            owner_person_id="people/1",
            recipient_person_id="people/r1",
        )
        db.save_app_data(
            RECIPIENT,
            {
                "sharedGroups": [
                    {
                        "owner": OWNER,
                        "resourceName": RESOURCE,
                        "recipientEmail": RECIPIENT,
                        "shareId": share_id,
                        "status": "Photos (1/2)",
                        "importPhase": "photos",
                        "memberCount": 1,
                        "actualCount": 1,
                        "importCursor": 1,
                        "photoDoneCount": 1,
                        "photoTargetCount": 2,
                        "photoPendingIndexes": [1],
                    }
                ]
            },
        )
        self.assertEqual(
            push_worker.resume_stalled_imports_for_recipient(RECIPIENT), 1
        )

    def test_progressing_job_is_not_stalled(self) -> None:
        job_id = db.enqueue_push_job(
            owner=OWNER,
            resource_name=RESOURCE,
            recipient_email=RECIPIENT,
            job_type="full_group",
            payload={},
        )
        db.update_push_job(job_id, touch=True)
        self.assertFalse(push_worker._job_is_stalled(job_id))

    def test_idle_photo_scan_is_not_resumed(self) -> None:
        db.save_app_data(
            RECIPIENT,
            {
                "sharedGroups": [
                    {
                        "owner": OWNER,
                        "resourceName": RESOURCE,
                        "recipientEmail": RECIPIENT,
                        "status": "Photos (0/0)",
                        "importPhase": "photos",
                        "photoDoneCount": 0,
                        "photoTargetCount": 0,
                        "photoNeedsRecipientScan": True,
                    }
                ]
            },
        )
        self.assertFalse(push_worker._group_needs_import_job(
            (db.get_app_data(RECIPIENT) or {})["sharedGroups"][0],
            RECIPIENT,
        ))
        self.assertEqual(
            push_worker.resume_stalled_imports_for_recipient(RECIPIENT), 0
        )

    def test_yielding_a_time_slice_does_not_burn_retry_attempts(self) -> None:
        job_id = db.enqueue_push_job(
            owner=OWNER,
            resource_name=RESOURCE,
            recipient_email=RECIPIENT,
            job_type="full_group",
            payload={},
        )
        for _ in range(30):
            db.claim_push_jobs(1)
            db.update_push_job(
                job_id, requeue=True, last_error="yielding", reset_attempts=True
            )
        row = db.list_push_jobs(OWNER, RESOURCE, limit=5)[0]
        self.assertEqual(row["status"], "queued")
        self.assertLessEqual(int(row["attempts"]), 1)

        db.reclaim_stale_push_jobs()
        row = db.list_push_jobs(OWNER, RESOURCE, limit=5)[0]
        self.assertNotEqual(row["status"], "error")

    def test_poll_resumes_an_import_whose_job_was_parked(self) -> None:
        db.save_app_data(
            RECIPIENT,
            {
                "sharedGroups": [
                    {
                        "owner": OWNER,
                        "resourceName": RESOURCE,
                        "recipientEmail": RECIPIENT,
                        "status": "Photos (0/726)",
                        "importPhase": "photos",
                        "memberCount": 726,
                    }
                ]
            },
        )
        self.assertEqual(
            push_worker.resume_stalled_imports_for_recipient(RECIPIENT), 1
        )
        self.assertTrue(db.has_active_push_job(OWNER, RESOURCE, RECIPIENT))
        # Already queued: do not pile on duplicates.
        self.assertEqual(
            push_worker.resume_stalled_imports_for_recipient(RECIPIENT), 0
        )

    def test_poll_repairs_a_finished_group_holding_extra_contacts(self) -> None:
        # 1169 contacts in Google against 726 records means duplicates.
        db.save_app_data(
            RECIPIENT,
            {
                "sharedGroups": [
                    {
                        "owner": OWNER,
                        "resourceName": RESOURCE,
                        "shareId": "share-dupe-repair",
                        "status": "Shared",
                        "importPhase": "done",
                        "actualCount": 1169,
                    }
                ]
            },
        )
        self.assertEqual(
            push_worker.resume_stalled_imports_for_recipient(RECIPIENT), 1
        )
        jobs = db.list_push_jobs(OWNER, RESOURCE, limit=20)
        for row in jobs:
            if row.get("status") in ("queued", "running"):
                db.update_push_job(int(row["id"]), status="done")
        db.save_recipient_token(RECIPIENT, {"token": "x", "refresh_token": "y"})
        # Scheduler kick (not just the recipient page) must queue the same repair.
        push_worker._resume_orphaned_imports()
        self.assertTrue(
            db.has_active_push_job(OWNER, RESOURCE, RECIPIENT)
        )
        self.assertFalse(
            push_worker._group_is_complete(RECIPIENT, OWNER, RESOURCE)
        )
        self.assertTrue(
            push_worker._group_is_pending(RECIPIENT, OWNER, RESOURCE)
        )

    def test_repair_runs_once_per_observed_count(self) -> None:
        db.save_app_data(
            RECIPIENT,
            {
                "sharedGroups": [
                    {
                        "owner": OWNER,
                        "resourceName": RESOURCE,
                        "shareId": "share-dupe-repair",
                        "status": "Shared",
                        "importPhase": "done",
                        "actualCount": 1169,
                        "repairCheckedActual": 1169,
                    }
                ]
            },
        )
        self.assertEqual(
            push_worker.resume_stalled_imports_for_recipient(RECIPIENT), 0
        )

    def test_shared_group_with_retry_indexes_is_not_treated_as_complete(self) -> None:
        db.save_app_data(
            RECIPIENT,
            {
                "sharedGroups": [
                    {
                        "owner": OWNER,
                        "resourceName": RESOURCE,
                        "shareId": "share-dupe-repair",
                        "status": "Shared",
                        "importPhase": "done",
                        "actualCount": 1169,
                        "repairCheckedActual": 1169,
                        "retryIndexes": [1, 2, 3],
                    }
                ]
            },
        )
        self.assertFalse(
            push_worker._group_is_complete(RECIPIENT, OWNER, RESOURCE)
        )
        self.assertEqual(
            push_worker.resume_stalled_imports_for_recipient(RECIPIENT), 1
        )

    def test_shared_group_with_too_few_contacts_is_resumed(self) -> None:
        db.save_app_data(
            RECIPIENT,
            {
                "sharedGroups": [
                    {
                        "owner": OWNER,
                        "resourceName": RESOURCE,
                        "shareId": "share-short",
                        "status": "Shared",
                        "importPhase": "done",
                        "memberCount": 726,
                        "actualCount": 200,
                    }
                ]
            },
        )
        self.assertFalse(
            push_worker._group_is_complete(RECIPIENT, OWNER, RESOURCE)
        )
        self.assertEqual(
            push_worker.resume_stalled_imports_for_recipient(RECIPIENT), 1
        )

    def test_poll_does_not_resume_a_finished_group(self) -> None:
        db.save_app_data(
            RECIPIENT,
            {
                "sharedGroups": [
                    {
                        "owner": OWNER,
                        "resourceName": RESOURCE,
                        "recipientEmail": RECIPIENT,
                        "shareId": "share-finished",
                        "status": "Shared",
                        "importPhase": "done",
                        "memberCount": 726,
                        "actualCount": 726,
                    }
                ]
            },
        )
        self.assertEqual(
            push_worker.resume_stalled_imports_for_recipient(RECIPIENT), 0
        )

    def test_poll_slice_is_passed_into_the_import_pass(self) -> None:
        job_id = db.enqueue_push_job(
            owner=OWNER,
            resource_name=RESOURCE,
            recipient_email=RECIPIENT,
            job_type="full_group",
            payload={},
        )
        seen: dict[str, int] = {}

        def _fake_import(recipient, owner, resource, max_ops=200, time_budget_ms=None):
            seen["budget_ms"] = int(time_budget_ms or 0)
            return {"busy": False}

        with (
            patch.object(push_worker, "start_push_worker"),
            patch.object(push_worker, "recipient_credentials", return_value=object()),
            patch.object(push_worker, "_mark_recipient_push_running"),
            patch.object(
                push_worker, "maybe_refresh_staged_shared_contacts_before_import"
            ),
            patch.object(push_worker, "_refresh_staged_then_backfill_photos"),
            patch.object(push_worker, "_group_is_complete", return_value=False),
            patch.object(push_worker, "_group_is_pending", return_value=False),
            patch.object(push_worker, "_sync_recipient_push_status_from_group"),
            patch.object(
                push_worker, "process_shared_import_for_group", _fake_import
            ),
        ):
            push_worker.pump_push_queue(max_seconds=5)

        self.assertLessEqual(seen.get("budget_ms", 0), 5_000)
        self.assertGreater(seen.get("budget_ms", 0), 0)
        row = db.list_push_jobs(OWNER, RESOURCE, limit=5)[0]
        self.assertEqual(int(row["id"]), job_id)

    def _owner_group_404(self, resource: str) -> str:
        return (
            "<HttpError 404 when requesting https://people.googleapis.com/v1/"
            f"{resource}?maxMembers=2000&alt=json returned "
            '"Requested entity was not found.". Details: '
            '"Requested entity was not found.">'
        )

    def _pushing_leftover_row(self, resource: str, share_id: str, name: str) -> dict:
        return {
            "owner": OWNER,
            "resourceName": resource,
            "recipientEmail": RECIPIENT,
            "shareId": share_id,
            "name": name,
            "status": "Pushing (0/377)",
            "pushStatus": "Running",
            "memberCount": 377,
            "actualCount": 0,
            "importCursor": 0,
            "created": "",
        }

    def test_refresh_does_not_requeue_deleted_owner_group(self) -> None:
        old = "contactGroups/old-almsla"
        new = "contactGroups/almsla-shared"
        db.save_app_data(
            RECIPIENT,
            {
                "sharedGroups": [
                    self._pushing_leftover_row(old, "share-old", "AlMsLa"),
                    {
                        "owner": OWNER,
                        "resourceName": new,
                        "recipientEmail": RECIPIENT,
                        "shareId": "share-new",
                        "name": "AlMsLa (Shared)",
                        "status": "Shared",
                        "importPhase": "done",
                        "memberCount": 730,
                        "actualCount": 730,
                        "importCursor": 730,
                    },
                ]
            },
        )
        gone = db.enqueue_push_job(
            owner=OWNER,
            resource_name=old,
            recipient_email=RECIPIENT,
            job_type="full_group",
            payload={"resume": True},
        )
        db.update_push_job(
            gone, status="error", last_error=self._owner_group_404(old)
        )
        queued = db.enqueue_push_job(
            owner=OWNER,
            resource_name=old,
            recipient_email=RECIPIENT,
            job_type="full_group",
            payload={"resume": True},
        )
        self.assertEqual(
            push_worker.resume_stalled_imports_for_recipient(RECIPIENT), 0
        )
        saved = db.get_app_data(RECIPIENT) or {}
        groups = saved.get("sharedGroups") or []
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].get("resourceName"), new)
        self.assertEqual(groups[0].get("status"), "Shared")
        dismissed = set(saved.get("dismissedSharedGroups") or [])
        self.assertIn(f"group:{OWNER}|{old}", dismissed)
        self.assertIn("share-id:share-old", dismissed)
        self.assertFalse(any(key.startswith("name:") for key in dismissed))
        self.assertEqual(
            db.get_push_job(queued).get("status"),
            "cancelled",
        )
        self.assertEqual(db.count_queued_push_jobs(), 0)

    def test_owner_group_404_is_not_retried(self) -> None:
        job_id = db.enqueue_push_job(
            owner=OWNER,
            resource_name=RESOURCE,
            recipient_email=RECIPIENT,
            job_type="full_group",
            payload={},
        )
        db.save_app_data(
            RECIPIENT,
            {"sharedGroups": [self._pushing_leftover_row(RESOURCE, "share-gone", "AlMsLa")]},
        )
        outcome = push_worker._handle_job_exception(
            {
                "id": job_id,
                "owner": OWNER,
                "resource_name": RESOURCE,
                "recipient_email": RECIPIENT,
                "attempts": 0,
            },
            Exception(self._owner_group_404(RESOURCE)),
        )
        self.assertEqual(outcome.get("reason"), "owner_group_gone")
        self.assertEqual(db.get_push_job(job_id).get("status"), "error")
        self.assertEqual((db.get_app_data(RECIPIENT) or {}).get("sharedGroups"), [])

    def test_contact_404_does_not_retire_owner_group(self) -> None:
        db.save_app_data(
            RECIPIENT,
            {
                "sharedGroups": [
                    self._pushing_leftover_row(RESOURCE, "share-live", "Friends")
                ]
            },
        )
        job_id = db.enqueue_push_job(
            owner=OWNER,
            resource_name=RESOURCE,
            recipient_email=RECIPIENT,
            job_type="full_group",
            payload={},
        )
        db.update_push_job(
            job_id,
            status="error",
            last_error=(
                '<HttpError 404 when requesting https://people.googleapis.com/v1/'
                'people/xyz?alt=json returned "Requested entity was not found.">'
            ),
        )
        group = (db.get_app_data(RECIPIENT) or {})["sharedGroups"][0]
        self.assertFalse(
            push_worker.owner_group_is_gone_error(
                str(db.get_push_job(job_id).get("last_error") or ""),
                RESOURCE,
            )
        )
        self.assertTrue(push_worker._group_needs_import_job(group, RECIPIENT))

    def test_retry_does_not_requeue_deleted_owner_group(self) -> None:
        from app.services import app_data as app_data_svc

        old = "contactGroups/old-almsla"
        db.save_app_data(
            RECIPIENT,
            {"sharedGroups": [self._pushing_leftover_row(old, "share-old", "AlMsLa")]},
        )
        gone = db.enqueue_push_job(
            owner=OWNER,
            resource_name=old,
            recipient_email=RECIPIENT,
            job_type="full_group",
            payload={},
        )
        db.update_push_job(
            gone, status="error", last_error=self._owner_group_404(old)
        )
        with (
            patch("app.services.app_data.recipient_credentials", return_value=object()),
            patch(
                "app.services.push_worker.enqueue_full_group_push",
                return_value=99,
            ) as enqueue,
            patch("app.db.reclaim_stale_push_jobs"),
        ):
            result = app_data_svc.retry_share_push(RECIPIENT, OWNER, old)
        enqueue.assert_not_called()
        self.assertEqual(result.get("status"), "owner_group_gone")
        self.assertEqual((db.get_app_data(RECIPIENT) or {}).get("sharedGroups"), [])

    def test_repair_from_staged_skips_dismissed_vanished_share(self) -> None:
        from app.services.recipient_recovery import repair_recipient_app_data_from_staged

        old = "contactGroups/old-almsla"
        share_id = "share-old"
        db.write_shared_contacts(
            share_id,
            OWNER,
            "AlMsLa",
            old,
            [{"resourceName": "people/1", "names": [{"displayName": "A"}]}],
        )
        db.upsert_sync_mapping(
            share_id=share_id,
            owner=OWNER,
            group_resource_name=old,
            recipient_email=RECIPIENT,
            owner_person_id="people/1",
            recipient_person_id="people/r1",
        )
        db.save_app_data(
            RECIPIENT,
            {
                "sharedGroups": [],
                "dismissedSharedGroups": [
                    f"group:{OWNER}|{old}",
                    f"share-id:{share_id}",
                ],
            },
        )
        self.assertFalse(repair_recipient_app_data_from_staged(RECIPIENT))
        self.assertEqual((db.get_app_data(RECIPIENT) or {}).get("sharedGroups"), [])


if __name__ == "__main__":
    unittest.main()
