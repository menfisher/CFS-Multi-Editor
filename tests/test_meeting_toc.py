import tempfile
import unittest
from pathlib import Path

from app.database import initialize_database, set_request_db_path
from app.services.address_book_pdf_service import build_address_book_pdf
from app.services.field_list_service import export_field_list_payload, import_field_list_payload
from app.services.meeting_toc_service import (
    meeting_is_unlisted_in_toc,
    rename_unlisted_toc_assignment,
    set_meetings_unlisted_in_toc,
)


def _sample_fields() -> list[dict]:
    return [
        {
            "name": "NC Eastern",
            "meetings": [
                {
                    "name": "Hillsborough",
                    "contacts": [{"name": "Ada Lovelace", "phones": [], "addresses": []}],
                }
            ],
        }
    ]


class MeetingTocTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        set_request_db_path(Path(self._tempdir.name) / "app.sqlite3")
        initialize_database()

    def tearDown(self) -> None:
        set_request_db_path(None)
        self._tempdir.cleanup()

    def test_unlisted_meeting_can_be_checked_and_cleared(self) -> None:
        set_meetings_unlisted_in_toc([{"field": "NC Eastern", "meeting": "Hillsborough"}], True)
        self.assertTrue(meeting_is_unlisted_in_toc("nc eastern", "hillsborough"))
        self.assertFalse(meeting_is_unlisted_in_toc("NC Eastern", "Raleigh"))
        set_meetings_unlisted_in_toc([{"field": "NC Eastern", "meeting": "Hillsborough"}], False)
        self.assertFalse(meeting_is_unlisted_in_toc("NC Eastern", "Hillsborough"))

    def test_blank_field_is_stored_as_no_field(self) -> None:
        set_meetings_unlisted_in_toc([{"field": "", "meeting": "Hillsborough"}], True)
        self.assertTrue(meeting_is_unlisted_in_toc("No Field", "Hillsborough"))

    def test_rename_keeps_the_unlisted_meeting(self) -> None:
        set_meetings_unlisted_in_toc([{"field": "NC Eastern", "meeting": "Hillsborough"}], True)
        rename_unlisted_toc_assignment("meeting", "Hillsborough", "Chapel Hill")
        self.assertFalse(meeting_is_unlisted_in_toc("NC Eastern", "Hillsborough"))
        self.assertTrue(meeting_is_unlisted_in_toc("NC Eastern", "Chapel Hill"))

    def test_field_list_sync_replaces_the_list_only_when_present(self) -> None:
        set_meetings_unlisted_in_toc([{"field": "NC Eastern", "meeting": "Hillsborough"}], True)
        payload = export_field_list_payload()
        self.assertEqual(payload["unlisted_toc_meetings"][0]["meeting_name"], "Hillsborough")
        set_meetings_unlisted_in_toc([{"field": "VA Workers", "meeting": "Fredericksburg"}], True)
        older_payload = dict(payload)
        older_payload.pop("unlisted_toc_meetings")
        self.assertTrue(import_field_list_payload(older_payload))
        self.assertTrue(meeting_is_unlisted_in_toc("VA Workers", "Fredericksburg"))
        self.assertTrue(import_field_list_payload(payload))
        self.assertTrue(meeting_is_unlisted_in_toc("NC Eastern", "Hillsborough"))
        self.assertFalse(meeting_is_unlisted_in_toc("VA Workers", "Fredericksburg"))

    def test_unlisted_meeting_is_omitted_from_the_pdf_toc(self) -> None:
        listed = build_address_book_pdf(
            fields=_sample_fields(),
            meeting_preview_sections=[],
            settings={"meeting_page_mode": "one_meeting", "include_bible_study_union_info": 0},
            layout={"trim_width_in": 8.5, "trim_height_in": 11, "base_font_size_pt": 10},
            print_order_mode="default",
            print_order_json="",
        )
        set_meetings_unlisted_in_toc([{"field": "NC Eastern", "meeting": "Hillsborough"}], True)
        unlisted = build_address_book_pdf(
            fields=_sample_fields(),
            meeting_preview_sections=[],
            settings={"meeting_page_mode": "one_meeting", "include_bible_study_union_info": 0},
            layout={"trim_width_in": 8.5, "trim_height_in": 11, "base_font_size_pt": 10},
            print_order_mode="default",
            print_order_json="",
        )
        self.assertGreater(listed.count(b"Hillsborough"), unlisted.count(b"Hillsborough"))
        self.assertIn(b"Hillsborough", unlisted)
        self.assertIn(b"Ada Lovelace", unlisted)
