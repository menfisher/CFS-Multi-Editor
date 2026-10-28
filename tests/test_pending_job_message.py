import unittest

from app.services.google_sync_service import describe_pending_jobs


class PendingJobMessageTests(unittest.TestCase):
    def test_empty_queue_has_no_message(self) -> None:
        self.assertEqual(describe_pending_jobs([], {}), "")

    def test_job_title_cleanup_message(self) -> None:
        queue = [{"action_label": "Job Title cleanup"}] * 730
        self.assertEqual(
            describe_pending_jobs(queue, {}),
            "Waiting to run: owner Job Title cleanup for 730 contacts. Click Upload to send this job.",
        )

    def test_drive_only_message(self) -> None:
        self.assertEqual(
            describe_pending_jobs([], {"meetingdata_needs_drive_export": True}),
            "Waiting to run: save MeetingData to Drive. Click Upload to send this job.",
        )

    def test_mixed_jobs_message(self) -> None:
        queue = [{"action_label": "Update"}, {"action_label": "Delete"}]
        message = describe_pending_jobs(queue, {"settings_needs_drive_export": True})
        self.assertEqual(
            message,
            "Waiting to run: 1 contact update, 1 contact deletion, and save Settings to Drive. Click Upload to send these jobs.",
        )


if __name__ == "__main__":
    unittest.main()
