import tempfile
import unittest
from pathlib import Path

from app.database import get_connection, initialize_database, set_request_db_path
from app.services.contact_service import (
    UNASSIGNED_FIELD_NAME,
    UNASSIGNED_MEETING_NAME,
    add_contact_assignment_option,
    get_contact_filter_options,
    list_contacts,
)
from app.services.field_list_service import _field_list_picker_payload


class UnassignedFieldPrintTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        db_path = Path(self._tempdir.name) / "app.sqlite3"
        set_request_db_path(db_path)
        initialize_database()
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO contacts (family_name, given_name, fields_text, meetings_text)
                VALUES (?, ?, ?, ?)
                """,
                ("Adams", "Vera", "Richmond", "Midlothian"),
            )
            conn.execute(
                """
                INSERT INTO contacts (family_name, given_name, fields_text, meetings_text)
                VALUES (?, ?, ?, ?)
                """,
                ("Baker", "Ned", "", ""),
            )
            conn.execute(
                """
                INSERT INTO contact_assignment_options (kind, value)
                VALUES ('field', '__rename_field__')
                """
            )
            conn.commit()

    def tearDown(self) -> None:
        set_request_db_path(None)
        self._tempdir.cleanup()

    def test_all_fields_list_includes_contacts_without_a_field(self) -> None:
        names = [contact["family_name"] for contact in list_contacts()]
        self.assertIn("Adams", names)
        self.assertIn("Baker", names)

    def test_no_field_filter_returns_only_unassigned_contacts(self) -> None:
        names = [contact["family_name"] for contact in list_contacts(field_filter=UNASSIGNED_FIELD_NAME)]
        self.assertEqual(names, ["Baker"])

    def test_filter_options_include_no_field(self) -> None:
        filters = get_contact_filter_options()
        self.assertIn("Richmond", filters["fields"])
        self.assertEqual(filters["fields"][-1], UNASSIGNED_FIELD_NAME)
        self.assertEqual(filters["meetings"][-1], UNASSIGNED_MEETING_NAME)
        self.assertIn(UNASSIGNED_MEETING_NAME, filters["meetings_by_field"][UNASSIGNED_FIELD_NAME])

    def test_filter_options_omit_rename_field_menu_sentinel(self) -> None:
        filters = get_contact_filter_options()
        self.assertNotIn("__rename_field__", filters["fields"])
        with self.assertRaises(ValueError):
            add_contact_assignment_option("field", "__rename_field__")

    def test_print_picker_includes_no_field_contacts(self) -> None:
        payload = _field_list_picker_payload()
        field_names = [field["name"] for field in payload["fields"]]
        self.assertIn("Richmond", field_names)
        self.assertEqual(field_names[-1], UNASSIGNED_FIELD_NAME)
        no_field = next(field for field in payload["fields"] if field["name"] == UNASSIGNED_FIELD_NAME)
        self.assertEqual(no_field["meetings"][-1]["name"], UNASSIGNED_MEETING_NAME)
        contact_names = [contact["family_name"] for contact in no_field["meetings"][-1]["contacts"]]
        self.assertEqual(contact_names, ["Baker"])


if __name__ == "__main__":
    unittest.main()
