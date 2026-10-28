import unittest
from datetime import datetime, timedelta, timezone

from app.services.google_sync_service import (
    format_editor_lock_blocked_message,
    format_editor_lock_duration,
)


class EditorLockBlockedMessageTests(unittest.TestCase):
    def test_format_editor_lock_duration_minutes(self) -> None:
        self.assertEqual(format_editor_lock_duration(125), "2:05")
        self.assertEqual(format_editor_lock_duration(0), "unknown")

    def test_format_editor_lock_duration_hours(self) -> None:
        self.assertEqual(format_editor_lock_duration(3661), "1:01:01")

    def test_format_editor_lock_blocked_message_uses_owner_and_remaining(self) -> None:
        message = format_editor_lock_blocked_message(owner_name="Alex", remaining_seconds=90)
        self.assertIn("Alex is already signed in", message)
        self.assertIn("NO-EDIT mode", message)
        self.assertIn("Remaining time: 1:30", message)
        self.assertIn("Check back periodically", message)

    def test_format_editor_lock_blocked_message_from_expires_at(self) -> None:
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=4, seconds=15)).isoformat()
        message = format_editor_lock_blocked_message(owner_name="Sam", expires_at=expires_at)
        self.assertIn("Sam is already signed in", message)
        self.assertIn("Remaining time: 4:", message)


if __name__ == "__main__":
    unittest.main()
