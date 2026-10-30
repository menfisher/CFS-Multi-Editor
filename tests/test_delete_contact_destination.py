import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from app.database import initialize_database, set_request_db_path
from app.services.contact_service import (
    create_contact,
    delete_contact,
    get_contact_detail,
    is_deleted_contact,
)


class DeleteContactDestinationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        set_request_db_path(Path(self._tempdir.name) / "app.sqlite3")
        initialize_database()

    def tearDown(self) -> None:
        set_request_db_path(None)
        self._tempdir.cleanup()

    def test_delete_moves_contact_to_deleted_field_and_year_meeting(self) -> None:
        contact_id = create_contact(
            {
                "family_name": "Lovelace",
                "given_name": "Ada",
                "fields_text": "AL Central",
                "meetings_text": "Birmingham",
            }
        )
        self.assertTrue(delete_contact(contact_id))
        contact = get_contact_detail(contact_id)
        self.assertEqual(contact["fields_text"], "Deleted")
        self.assertEqual(contact["meetings_text"], str(datetime.now().year))
        self.assertTrue(is_deleted_contact(contact))
        self.assertIn("Deleted - ", contact.get("group_membership") or "")

    def test_legacy_deleted_meeting_still_counts_as_deleted(self) -> None:
        self.assertTrue(
            is_deleted_contact({"fields_text": "2026", "meetings_text": "Deleted"})
        )
        self.assertTrue(
            is_deleted_contact({"fields_text": "Deleted", "meetings_text": "2026"})
        )
        self.assertFalse(
            is_deleted_contact({"fields_text": "AL Central", "meetings_text": "Birmingham"})
        )
