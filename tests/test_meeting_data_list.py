import tempfile
import unittest
from pathlib import Path

from app.database import get_connection, initialize_database, set_request_db_path
from app.services.meeting_v2_service import get_meeting_v2_filter_options, list_meeting_v2_sections


class MeetingDataListTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        set_request_db_path(Path(self._tempdir.name) / "app.sqlite3")
        initialize_database()
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO contacts (family_name, given_name, fields_text, meetings_text)
                VALUES (?, ?, ?, ?)
                """,
                ("Adams", "Vera", "Richmond", "Midlothian"),
            )
            conn.commit()

    def tearDown(self) -> None:
        set_request_db_path(None)
        self._tempdir.cleanup()

    def test_contact_field_and_meeting_show_without_typed_cells(self) -> None:
        sections = list_meeting_v2_sections()
        self.assertEqual(
            [(section["field_name"], section["meeting_name"], int(section["cell_count"])) for section in sections],
            [("Richmond", "Midlothian", 0)],
        )
        filters = get_meeting_v2_filter_options()
        self.assertIn("Richmond", filters["fields"])
        self.assertIn("Midlothian", filters["meetings"])
        again = list_meeting_v2_sections()
        self.assertEqual(len(again), 1)
        self.assertEqual(again[0]["id"], sections[0]["id"])
