import tempfile
import unittest
from pathlib import Path

import fitz

from app.database import fetch_one, initialize_database, set_request_db_path
from app.services.address_book_pdf_service import build_address_book_pdf
from app.services.field_end_list_service import add_field_end_list
from app.services.meeting_v2_service import (
    create_meeting_v2_section,
    get_meeting_v2_section_editor,
    save_meeting_v2_section,
)


_LAYOUT = {
    "trim_width_in": 3.5,
    "trim_height_in": 5.5,
    "margin_left_in": 0.14,
    "margin_right_in": 0.14,
    "margin_top_in": 0.14,
    "margin_bottom_in": 0.14,
    "base_font_size_pt": 7,
    "line_height": 1.2,
    "font_family": "Arial",
}


def _page_texts(pdf: bytes) -> list[str]:
    document = fitz.open(stream=pdf, filetype="pdf")
    return [page.get_text() for page in document]


def _table_row(
    text: str,
    *,
    start: bool = False,
    end: bool = False,
    print_new_page: bool = False,
    no_meeting_association: bool = False,
    group: str = "",
) -> dict:
    return {
        "kind": "cells",
        "is_table_start_row": start,
        "is_table_end_row": end,
        "print_new_page": print_new_page,
        "no_meeting_association": no_meeting_association,
        "keep_together_group": group,
        "font_size_pt": 7.0,
        "column_gap_px": 6,
        "preview_printable_width_px": 220,
        "grid_column_count": 28,
        "cells": [{"html": text, "col_start": 1, "col_span": 28, "align": "auto"}],
    }


def _build_pdf(rows: list[dict], contact_count: int = 6) -> bytes:
    contacts = [{"name": f"Contact {index:02d}"} for index in range(1, contact_count + 1)]
    return build_address_book_pdf(
        fields=[{"name": "NC Eastern Field/Raleigh", "meetings": [{"name": "OXFORD", "contacts": contacts}]}],
        meeting_preview_sections=[{"field": "NC Eastern Field/Raleigh", "meeting": "OXFORD", "rows": rows}],
        settings={"meeting_page_mode": "one_meeting", "include_bible_study_union_info": 1},
        layout=_LAYOUT,
        print_order_mode="default",
        print_order_json="",
    )


class PrintNewPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        set_request_db_path(Path(self._tempdir.name) / "app.sqlite3")
        initialize_database()

    def tearDown(self) -> None:
        set_request_db_path(None)
        self._tempdir.cleanup()

    def test_print_new_page_starts_a_new_page(self) -> None:
        rows = [
            _table_row("FieldListTable", start=True, end=True, print_new_page=True),
        ]
        pages = _page_texts(_build_pdf(rows))
        contact_pages = [index for index, text in enumerate(pages) if "Contact 01" in text]
        table_pages = [index for index, text in enumerate(pages) if "FieldListTable" in text]
        self.assertTrue(contact_pages, pages)
        self.assertTrue(table_pages, pages)
        self.assertGreater(table_pages[0], contact_pages[0])
        self.assertNotIn("FieldListTable", pages[contact_pages[0]])

    def test_print_new_page_keeps_grouped_tables_together(self) -> None:
        group = "block-0-group-with-table-below"
        rows = [
            _table_row("FirstGroupedTable", start=True, end=True, print_new_page=True, group=group),
            _table_row("SecondGroupedTable", start=True, end=True, group=group),
        ]
        pages = _page_texts(_build_pdf(rows))
        first_pages = [index for index, text in enumerate(pages) if "FirstGroupedTable" in text]
        second_pages = [index for index, text in enumerate(pages) if "SecondGroupedTable" in text]
        contact_pages = [index for index, text in enumerate(pages) if "Contact 01" in text]
        self.assertEqual(first_pages, second_pages)
        self.assertTrue(contact_pages)
        self.assertGreater(first_pages[0], contact_pages[0])
        self.assertIn("FirstGroupedTable", pages[first_pages[0]])
        self.assertIn("SecondGroupedTable", pages[first_pages[0]])
        self.assertNotIn("Contact 01", pages[first_pages[0]])

    def test_print_new_page_persists_on_a_table_start_row(self) -> None:
        section_id = create_meeting_v2_section("NC Eastern Field/Raleigh", "OXFORD")
        section = get_meeting_v2_section_editor(section_id)
        row = section["rows"][0]
        save_meeting_v2_section(
            section_id,
            "",
            None,
            None,
            "CV",
            [
                {
                    "id": row["id"],
                    "row_order": 1,
                    "row_kind": ["content", "table_start"],
                    "group_with_table_below": 1,
                    "no_meeting_association": 1,
                    "print_new_page": 1,
                    "format_code": "",
                    "label_text": "",
                    "notes": "",
                }
            ],
            [],
        )
        saved = fetch_one(
            """
            SELECT print_new_page, group_with_table_below, no_meeting_association, row_kind
            FROM meeting_section_rows_v2
            WHERE id = ?
            """,
            (row["id"],),
        )
        reloaded = get_meeting_v2_section_editor(section_id)["rows"][0]
        self.assertEqual(int(saved["print_new_page"]), 0)
        self.assertEqual(int(saved["group_with_table_below"]), 1)
        self.assertEqual(int(saved["no_meeting_association"]), 1)
        self.assertIn("table_start", str(saved["row_kind"]))
        self.assertFalse(reloaded["print_new_page"])
        self.assertTrue(reloaded["group_with_table_below"])
        self.assertTrue(reloaded["no_meeting_association"])
        self.assertFalse(reloaded.get("is_field_end_list"))

    def test_field_list_editor_clears_no_meeting_association(self) -> None:
        saved_list = add_field_end_list("NC Eastern Field/Raleigh", "Workers List")
        section_id = int(saved_list["section_id"])
        section = get_meeting_v2_section_editor(section_id)
        self.assertTrue(section["is_field_end_list"])
        row = section["rows"][0]
        save_meeting_v2_section(
            section_id,
            "",
            None,
            None,
            "CV",
            [
                {
                    "id": row["id"],
                    "row_order": 1,
                    "row_kind": ["content", "table_start"],
                    "group_with_table_below": 1,
                    "no_meeting_association": 1,
                    "print_new_page": 1,
                    "format_code": "",
                    "label_text": "",
                    "notes": "",
                }
            ],
            [],
        )
        saved = fetch_one(
            """
            SELECT print_new_page, group_with_table_below, no_meeting_association
            FROM meeting_section_rows_v2
            WHERE id = ?
            """,
            (row["id"],),
        )
        reloaded = get_meeting_v2_section_editor(section_id)["rows"][0]
        self.assertEqual(int(saved["print_new_page"]), 1)
        self.assertEqual(int(saved["group_with_table_below"]), 1)
        self.assertEqual(int(saved["no_meeting_association"]), 0)
        self.assertFalse(reloaded["no_meeting_association"])

    def test_no_meeting_association_omits_meeting_heading_on_table_page(self) -> None:
        rows = [
            _table_row("ExtraTableBehindMeeting", start=True, end=True, no_meeting_association=True),
        ]
        pages = _page_texts(_build_pdf(rows))
        table_pages = [text for text in pages if "ExtraTableBehindMeeting" in text]
        self.assertTrue(table_pages, pages)
        for text in table_pages:
            self.assertNotIn("OXFORD", text)
            self.assertNotIn("(continued)", text)
        self.assertTrue(any("OXFORD" in text and "Contact 01" in text for text in pages), pages)

    def test_field_end_list_keeps_list_name_with_no_meeting_association(self) -> None:
        rows = [
            _table_row("FieldListTable", start=True, end=True, no_meeting_association=True),
        ]
        pdf = build_address_book_pdf(
            fields=[
                {
                    "name": "NC Eastern Field/Raleigh",
                    "meetings": [
                        {"name": "OXFORD", "contacts": [{"name": "Contact 01"}]},
                        {
                            "name": "Field List 1",
                            "contacts": [],
                            "is_field_end_list": True,
                            "field_end_list_order": 1,
                        },
                    ],
                }
            ],
            meeting_preview_sections=[
                {"field": "NC Eastern Field/Raleigh", "meeting": "Field List 1", "rows": rows},
            ],
            settings={"meeting_page_mode": "one_meeting", "include_bible_study_union_info": 1},
            layout=_LAYOUT,
            print_order_mode="default",
            print_order_json="",
        )
        pages = _page_texts(pdf)
        field_list_pages = [text for text in pages if "FieldListTable" in text]
        self.assertTrue(field_list_pages, pages)
        self.assertTrue(any("Field List 1" in text for text in field_list_pages), pages)


if __name__ == "__main__":
    unittest.main()
