"""Paced card-repair status and enqueue slicing."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db
from app.services import card_repair
from app.services.contact_hash import compute_contact_hash
from app.services.push_worker import CONTACT_BATCH_SLICE, enqueue_contact_push


OWNER_PERSON = {
    "resourceName": "people/p1",
    "etag": "owner-etag",
    "organizations": [{"title": "Deleted", "name": "2026", "metadata": {"primary": True}}],
}
RECIPIENT_PERSON = {
    "resourceName": "people/r1",
    "etag": "recipient-etag",
    "memberships": [
        {
            "contactGroupMembership": {
                "contactGroupResourceName": "contactGroups/deleted"
            }
        }
    ],
}
REPAIR_CONTEXT = {
    "ids": {"Deleted - 2026 (Shared)": "contactGroups/deleted"},
    "names": {"contactGroups/deleted": "Deleted - 2026 (Shared)"},
    "protected": {"contactGroups/import"},
}


class CardRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._prev_path = db._active_database_path
        db._active_database_path = Path(self._tmpdir.name) / "test.db"
        db.init_db()

    def tearDown(self) -> None:
        db._active_database_path = self._prev_path
        self._tmpdir.cleanup()

    def _seed_assignment_mapping(
        self,
        *,
        owner_person_id: str = "people/p1",
        recipient_person_id: str = "people/r1",
        last_assignment_label: str = "",
        title: str = "Deleted",
        meeting: str = "2026",
    ) -> None:
        db.save_app_data("o@example.com", {"groups": {}})
        db.write_shared_contacts(
            "s1",
            "o@example.com",
            "AlMsLa",
            "contactGroups/g1",
            [
                {
                    "resourceName": owner_person_id,
                    "organizations": [
                        {"title": title, "name": meeting, "metadata": {"primary": True}}
                    ],
                }
            ],
        )
        db.upsert_sync_mapping(
            share_id="s1",
            owner="o@example.com",
            group_resource_name="contactGroups/g1",
            recipient_email="r@example.com",
            owner_person_id=owner_person_id,
            recipient_person_id=recipient_person_id,
            last_assignment_label=last_assignment_label,
        )

    def test_status_starts_idle(self) -> None:
        status = card_repair.get_card_repair_status()
        self.assertEqual(status["state"], "idle")
        self.assertEqual(status["current_recipient"], "")
        self.assertEqual(status["recipients_done"], 0)

    def test_start_with_no_mappings_is_done(self) -> None:
        status = card_repair.start_card_repair()
        self.assertEqual(status["state"], "done")
        self.assertIn("No mapped recipient contacts", status["last_error"])

    def test_summary_line_for_running(self) -> None:
        line = card_repair.card_repair_summary_line(
            {
                "state": "running",
                "recipients_done": 3,
                "recipients_total": 10,
                "current_recipient": "r1@example.com",
            }
        )
        self.assertEqual(line, "Card repair: running 3/10 — r1@example.com")

    def test_stalled_when_heartbeat_is_old(self) -> None:
        db.save_app_data(
            card_repair.STATUS_EMAIL,
            {
                **card_repair._empty_status(),
                "state": "running",
                "recipients_total": 10,
                "updated_at": "2000-01-01T00:00:00+00:00",
            },
        )
        status = card_repair.get_card_repair_status()
        self.assertEqual(status["state"], "stalled")

    def test_enqueue_contact_push_slices_ids(self) -> None:
        ids = [f"people/c{index}" for index in range(CONTACT_BATCH_SLICE + 2)]
        with patch("app.services.push_worker.kick_push_queue"):
            enqueue_contact_push("owner@example.com", ids)
        jobs = db.list_push_jobs("owner@example.com")
        self.assertEqual(len(jobs), 2)
        self.assertTrue(all(job["job_type"] == "contact_batch" for job in jobs))
        sizes = sorted(len(job["payload"]["owner_person_ids"]) for job in jobs)
        self.assertEqual(sizes, [2, CONTACT_BATCH_SLICE])

    def test_skips_full_update_when_card_already_matches(self) -> None:
        mapping = {
            "share_id": "s1",
            "owner": "o@example.com",
            "group_resource_name": "contactGroups/g1",
            "recipient_email": "r@example.com",
            "owner_person_id": "people/p1",
            "recipient_person_id": "people/r1",
            "last_hash": compute_contact_hash(OWNER_PERSON),
            "last_photo_hash": "",
        }
        with (
            patch("app.services.card_repair.update_contact") as update_mock,
            patch("app.services.card_repair.ensure_assignment_contact_label") as ensure_mock,
            patch("app.services.card_repair.remove_outdated_assignment_labels") as stale_mock,
        ):
            result = card_repair._repair_mapping(
                mapping,
                {"people/p1": OWNER_PERSON},
                object(),
                REPAIR_CONTEXT,
                {"people/r1": RECIPIENT_PERSON},
            )
        self.assertEqual(result, "ok")
        update_mock.assert_not_called()
        ensure_mock.assert_not_called()
        stale_mock.assert_called_once()
        self.assertEqual(
            stale_mock.call_args.kwargs.get("memberships"),
            RECIPIENT_PERSON["memberships"],
        )

    def test_updates_when_hash_changed(self) -> None:
        mapping = {
            "share_id": "s1",
            "owner": "o@example.com",
            "group_resource_name": "contactGroups/g1",
            "recipient_email": "r@example.com",
            "owner_person_id": "people/p1",
            "recipient_person_id": "people/r1",
            "last_hash": "stale-hash",
            "last_photo_hash": "",
        }
        with (
            patch("app.services.card_repair.update_contact", return_value=True) as update_mock,
            patch("app.services.card_repair.ensure_assignment_contact_label"),
            patch("app.services.card_repair.remove_outdated_assignment_labels"),
        ):
            result = card_repair._repair_mapping(
                mapping,
                {"people/p1": OWNER_PERSON},
                object(),
                REPAIR_CONTEXT,
                {"people/r1": RECIPIENT_PERSON},
            )
        self.assertEqual(result, "ok")
        update_mock.assert_called_once()

    def test_skips_contacts_whose_assignment_already_matches(self) -> None:
        self._seed_assignment_mapping(
            last_assignment_label="Deleted - 2026 (Shared)",
        )
        self.assertEqual(card_repair._repair_items_for_recipient("r@example.com"), [])

    def test_repairs_only_assignment_mismatches(self) -> None:
        self._seed_assignment_mapping(
            last_assignment_label="Contacts - LA North (Shared)",
        )
        items = card_repair._repair_items_for_recipient("r@example.com")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["owner_person_id"], "people/p1")

    def test_does_not_scan_when_stored_assignment_is_unknown(self) -> None:
        self._seed_assignment_mapping(last_assignment_label="")
        self.assertEqual(card_repair._repair_items_for_recipient("r@example.com"), [])

    def test_pending_person_is_repaired_even_when_labels_match(self) -> None:
        self._seed_assignment_mapping(
            last_assignment_label="Deleted - 2026 (Shared)",
        )
        card_repair.queue_person_for_label_repair("people/p1")
        items = card_repair._repair_items_for_recipient("r@example.com")
        self.assertEqual(len(items), 1)

    def test_same_owner_cards_are_repaired_on_every_recipient(self) -> None:
        self._seed_assignment_mapping(
            last_assignment_label="Contacts - LA North (Shared)",
        )
        db.upsert_sync_mapping(
            share_id="s1",
            owner="o@example.com",
            group_resource_name="contactGroups/g1",
            recipient_email="r2@example.com",
            owner_person_id="people/p1",
            recipient_person_id="people/r2",
            last_assignment_label="Deleted - 2026 (Shared)",
        )
        ids = card_repair._collect_repair_person_ids()
        self.assertEqual(ids, ["people/p1"])
        card_repair.save_card_repair_status(
            {**card_repair._empty_status(), "repair_person_ids": ids}
        )
        self.assertEqual(
            [item["recipient_person_id"] for item in card_repair._repair_items_for_recipient("r@example.com")],
            ["people/r1"],
        )
        self.assertEqual(
            [item["recipient_person_id"] for item in card_repair._repair_items_for_recipient("r2@example.com")],
            ["people/r2"],
        )

    def test_repairs_unlabeled_recipients_when_one_recipient_already_recorded(self) -> None:
        self._seed_assignment_mapping(
            last_assignment_label="Deleted - 2026 (Shared)",
        )
        db.upsert_sync_mapping(
            share_id="s1",
            owner="o@example.com",
            group_resource_name="contactGroups/g1",
            recipient_email="r2@example.com",
            owner_person_id="people/p1",
            recipient_person_id="people/r2",
            last_assignment_label="",
        )
        ids = card_repair._collect_repair_person_ids()
        self.assertEqual(ids, ["people/p1"])


if __name__ == "__main__":
    unittest.main()
