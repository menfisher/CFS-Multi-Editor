import signal
import tempfile
import unittest
from pathlib import Path

from app.database import fetch_all, fetch_one, get_connection, initialize_database, set_request_db_path
from app.services.field_end_list_service import add_field_end_list, delete_field_end_list, rename_field_end_list, reorder_field_end_lists
from app.services.field_list_service import _field_list_picker_payload
from app.services.meeting_v2_service import (
    delete_empty_orphan_meeting_v2_sections,
    list_meeting_v2_sections,
    save_meeting_v2_section,
)


class FieldEndListServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        set_request_db_path(Path(self._tempdir.name) / "app.sqlite3")
        initialize_database()

    def tearDown(self) -> None:
        set_request_db_path(None)
        self._tempdir.cleanup()

    def test_add_and_reorder_field_lists(self) -> None:
        add_field_end_list("NC Western Field", "Workers")
        add_field_end_list("NC Western Field", "Friends")
        add_field_end_list("NC Western Field", "Contacts")
        lists = reorder_field_end_lists(
            "NC Western Field",
            ["Friends", "Contacts", "Workers"],
        )
        self.assertEqual(
            [item["list_name"] for item in lists],
            ["Friends", "Contacts", "Workers"],
        )

    def test_add_field_list_opens_a_meetingdata_section(self) -> None:
        saved = add_field_end_list("NC Western Field", "Workers")
        section_id = int(saved.get("section_id") or 0)
        self.assertGreater(section_id, 0)
        self.assertEqual(saved.get("editor_url"), f"/meetings/v2/sections/{section_id}/editor#row-1")
        section = fetch_one(
            """
            SELECT field_name, meeting_name
            FROM meeting_sections_v2
            WHERE id = ?
            """,
            (section_id,),
        )
        self.assertIsNotNone(section)
        self.assertEqual(section["field_name"], "NC Western Field")
        self.assertEqual(section["meeting_name"], "Workers")
        listed = list_meeting_v2_sections("", "NC Western Field", "")
        self.assertIn("Workers", [item["meeting_name"] for item in listed])
        self.assertEqual(
            fetch_all(
                """
                SELECT value
                FROM contact_assignment_options
                WHERE kind = 'meeting' AND value = ?
                """,
                ("Workers",),
            ),
            [],
        )

    def test_orphaned_field_list_gets_a_meetingdata_section(self) -> None:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO field_end_lists (field_name, list_name, sort_order)
                VALUES ('NC Eastern Field/Raleigh', 'Field list 1', 1)
                """
            )
            conn.commit()
        saved = add_field_end_list("NC Eastern Field/Raleigh", "Field List 1")
        section_id = int(saved.get("section_id") or 0)
        self.assertGreater(section_id, 0)
        self.assertEqual(saved.get("list_name"), "Field list 1")
        section = fetch_one(
            """
            SELECT field_name, meeting_name
            FROM meeting_sections_v2
            WHERE id = ?
            """,
            (section_id,),
        )
        self.assertEqual(section["field_name"], "NC Eastern Field/Raleigh")
        self.assertEqual(section["meeting_name"], "Field list 1")
        listed = list_meeting_v2_sections("", "NC Eastern Field/Raleigh", "")
        self.assertIn("Field list 1", [item["meeting_name"] for item in listed])

    def test_rename_and_delete_field_list_updates_meetingdata(self) -> None:
        add_field_end_list("NC Eastern Field/Raleigh", "Temp Organize List")
        renamed = rename_field_end_list(
            "NC Eastern Field/Raleigh",
            "Temp Organize List",
            "Temp Organize Renamed",
        )
        self.assertEqual(renamed["list_name"], "Temp Organize Renamed")
        self.assertEqual(renamed["old_list_name"], "Temp Organize List")
        listed = list_meeting_v2_sections("", "NC Eastern Field/Raleigh", "")
        names = [item["meeting_name"] for item in listed]
        self.assertIn("Temp Organize Renamed", names)
        self.assertNotIn("Temp Organize List", names)
        section = fetch_one(
            """
            SELECT meeting_name
            FROM meeting_sections_v2
            WHERE id = ?
            """,
            (int(renamed["section_id"]),),
        )
        self.assertEqual(section["meeting_name"], "Temp Organize Renamed")
        removed = delete_field_end_list("NC Eastern Field/Raleigh", "Temp Organize Renamed")
        self.assertEqual(removed["value"], "Temp Organize Renamed")
        listed_after = list_meeting_v2_sections("", "NC Eastern Field/Raleigh", "")
        self.assertNotIn("Temp Organize Renamed", [item["meeting_name"] for item in listed_after])
        self.assertIsNone(
            fetch_one(
                """
                SELECT id
                FROM meeting_sections_v2
                WHERE field_name = ? AND meeting_name = ?
                """,
                ("NC Eastern Field/Raleigh", "Temp Organize Renamed"),
            )
        )

    def test_picker_prints_field_lists_after_meetings(self) -> None:
        add_field_end_list("NC Western Field", "Workers")
        add_field_end_list("NC Western Field", "Friends")
        reorder_field_end_lists("NC Western Field", ["Friends", "Workers"])
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO contacts (family_name, given_name, fields_text, meetings_text)
                VALUES
                  ('Avery', 'Ann', 'NC Western Field', 'ASHEVILLE'),
                  ('Baker', 'Ben', 'NC Western Field', 'GASTONIA'),
                  ('Cole', 'Cam', 'NC Western Field', 'Workers'),
                  ('Dale', 'Dee', 'NC Western Field', 'Friends')
                """
            )
            conn.commit()
        field = _field_list_picker_payload()["fields"][0]
        self.assertEqual(
            [meeting["name"] for meeting in field["meetings"]],
            ["ASHEVILLE", "GASTONIA", "Friends", "Workers"],
        )
        self.assertEqual(
            [bool(meeting.get("is_field_end_list")) for meeting in field["meetings"]],
            [False, False, True, True],
        )

    def test_picker_includes_field_lists_without_contacts(self) -> None:
        add_field_end_list("NC Western Field", "Workers")
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO contacts (family_name, given_name, fields_text, meetings_text)
                VALUES ('Avery', 'Ann', 'NC Western Field', 'ASHEVILLE')
                """
            )
            conn.commit()
        field = _field_list_picker_payload()["fields"][0]
        self.assertEqual(
            [meeting["name"] for meeting in field["meetings"]],
            ["ASHEVILLE", "Workers"],
        )
        workers = field["meetings"][1]
        self.assertTrue(workers.get("is_field_end_list"))
        self.assertEqual(workers.get("contacts"), [])

    def test_save_layout_does_not_deadlock_when_pruning_old_edit_logs(self) -> None:
        saved = add_field_end_list("AL Central", "Test Field list 1")
        section_id = int(saved["section_id"])
        row = fetch_one(
            """
            SELECT id
            FROM meeting_section_rows_v2
            WHERE section_id = ?
            ORDER BY row_order, id
            LIMIT 1
            """,
            (section_id,),
        )
        cell = fetch_one(
            """
            SELECT id
            FROM meeting_row_cells_v2
            WHERE row_id = ?
            ORDER BY col_start, id
            LIMIT 1
            """,
            (int(row["id"]),),
        )
        self.assertIsNotNone(row)
        self.assertIsNotNone(cell)
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO meeting_edit_log_entries (
                  batch_id,
                  batch_created_at,
                  batch_row_index,
                  editor_initials,
                  section_id,
                  field_name,
                  meeting_name,
                  row_id,
                  row_number,
                  original_text,
                  edited_text
                )
                VALUES (?, ?, 1, 'XX', ?, 'AL Central', 'Old Meeting', ?, 1, 'old', 'older')
                """,
                ("deadlock-batch", "2000-01-01T00:00:00.000000", section_id, int(row["id"])),
            )
            conn.commit()

        def _timeout_handler(signum, frame):
            raise TimeoutError("Save Layout deadlocked on a nested SQLite write")

        previous = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(8)
        try:
            save_meeting_v2_section(
                section_id=section_id,
                section_notes="",
                book_layout_preset_id=None,
                base_font_size_pt="8.5",
                editor_initials="CV",
                row_updates=[
                    {
                        "id": int(row["id"]),
                        "row_order": 1,
                        "row_kind": ["content"],
                        "group_with_table_below": 0,
                        "no_meeting_association": 0,
                        "format_code": "",
                        "label_text": "",
                        "notes": "",
                    }
                ],
                cell_updates=[
                    {
                        "id": int(cell["id"]),
                        "col_start": 1,
                        "col_span": 28,
                        "text_value": "Hello<br>this is the 2nd line<br>and this is the third",
                        "text_align": "center",
                    }
                ],
            )
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)

        stored = fetch_one(
            """
            SELECT text_value
            FROM meeting_row_cells_v2
            WHERE id = ?
            """,
            (int(cell["id"]),),
        )
        self.assertIn("Hello", str(stored["text_value"] or ""))
        self.assertIsNone(
            fetch_one(
                """
                SELECT id
                FROM meeting_edit_log_entries
                WHERE batch_id = 'deadlock-batch'
                """
            )
        )

    def test_empty_field_list_survives_orphan_cleanup(self) -> None:
        saved = add_field_end_list("AL Central", "Keep This List")
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO meeting_sections_v2 (field_name, meeting_name, sort_order)
                VALUES ('AL Central', 'Orphan One', 90), ('AL Central', 'Orphan Two', 91)
                """
            )
            conn.commit()

        def _timeout_handler(signum, frame):
            raise TimeoutError("Orphan cleanup deadlocked on a nested Field-list lookup")

        previous = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(8)
        try:
            delete_empty_orphan_meeting_v2_sections()
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)

        remaining = {
            str(item["meeting_name"])
            for item in list_meeting_v2_sections("", "AL Central", "")
        }
        self.assertIn("Keep This List", remaining)
        self.assertNotIn("Orphan One", remaining)
        self.assertNotIn("Orphan Two", remaining)
        self.assertIsNotNone(
            fetch_one(
                """
                SELECT id
                FROM meeting_sections_v2
                WHERE id = ?
                """,
                (int(saved["section_id"]),),
            )
        )


if __name__ == "__main__":
    unittest.main()
