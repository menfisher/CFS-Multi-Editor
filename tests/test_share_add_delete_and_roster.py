import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.database import initialize_database, set_request_db_path
from app.services.contact_service import create_contact, delete_contact, permanently_delete_contact
from app.services.roster_check_service import build_roster_report


class ShareAddDeleteAndRosterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        set_request_db_path(Path(self._tempdir.name) / "app.sqlite3")
        initialize_database()

    def tearDown(self) -> None:
        set_request_db_path(None)
        self._tempdir.cleanup()

    def test_create_contact_notifies_share(self) -> None:
        with patch(
            "app.services.contact_service.create_google_contact_and_notify_share"
        ) as notify:
            contact_id = create_contact(
                {
                    "family_name": "Burns",
                    "given_name": "Chad",
                    "fields_text": "AL Central",
                    "meetings_text": "Scattered Friends",
                }
            )
        notify.assert_called_once_with(contact_id)

    def test_permanent_delete_notifies_share(self) -> None:
        with patch("app.services.contact_service.create_google_contact_and_notify_share"):
            contact_id = create_contact(
                {
                    "family_name": "Paul",
                    "given_name": "Roger",
                    "fields_text": "Deleted",
                    "meetings_text": "2026",
                    "google_contact_id": "people/c7128176463445082155",
                }
            )
        self.assertTrue(delete_contact(contact_id))
        with patch(
            "app.services.contact_service.delete_google_contact_and_notify_share"
        ) as notify:
            self.assertTrue(permanently_delete_contact(contact_id))
        notify.assert_called_once_with("people/c7128176463445082155")

    def test_roster_report_lists_missing_and_extra(self) -> None:
        report = build_roster_report(
            cfs_people=[
                {"id": 1, "google_contact_id": "people/c-chad", "name": "Burns, Chad"},
                {"id": 2, "google_contact_id": "people/c-betty", "name": "Miller, Betty"},
            ],
            google_people=[
                {"id": "people/c-chad", "name": "Burns, Chad"},
                {"id": "people/c-betty", "name": "Miller, Betty"},
            ],
            drive_ids={1, 2},
            drive_labels={1: "Burns, Chad", 2: "Miller, Betty"},
            recipient_maps={
                "menfisher@gmail.com": {"people/c-betty", "people/c-larry"},
            },
        )
        self.assertTrue(report["mismatch"])
        recipient = report["recipients"][0]
        self.assertEqual(recipient["email"], "menfisher@gmail.com")
        self.assertEqual(recipient["missing"], ["Burns, Chad"])
        self.assertEqual(recipient["extra_count"], 1)
        self.assertIn("menfisher@gmail.com", report["message"])
        self.assertIn("Burns, Chad", report["message"])

    def test_matching_roster_is_not_an_alert(self) -> None:
        report = build_roster_report(
            cfs_people=[
                {"id": 1, "google_contact_id": "people/c-chad", "name": "Burns, Chad"},
            ],
            google_people=[{"id": "people/c-chad", "name": "Burns, Chad"}],
            drive_ids={1},
            drive_labels={1: "Burns, Chad"},
            recipient_maps={"menfisher@gmail.com": {"people/c-chad"}},
        )
        self.assertFalse(report["mismatch"])
        self.assertEqual(report["lines"], [])


if __name__ == "__main__":
    unittest.main()
