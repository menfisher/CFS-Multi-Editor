"""Paced card-repair status and enqueue slicing."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db
from app.services import card_repair
from app.services.push_worker import CONTACT_BATCH_SLICE, enqueue_contact_push


class CardRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._prev_path = db._active_database_path
        db._active_database_path = Path(self._tmpdir.name) / "test.db"
        db.init_db()

    def tearDown(self) -> None:
        db._active_database_path = self._prev_path
        self._tmpdir.cleanup()

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


if __name__ == "__main__":
    unittest.main()
