import tempfile
import unittest
from pathlib import Path

import fitz

from app.database import initialize_database, set_request_db_path
from app.services.address_book_pdf_service import build_address_book_pdf


_LAYOUT = {
    "book_title": "Test Book",
    "trim_width_in": 3.5,
    "trim_height_in": 5.5,
    "margin_left_in": 0.14,
    "margin_right_in": 0.14,
    "margin_top_in": 0.14,
    "margin_bottom_in": 0.14,
    "base_font_size_pt": 7,
    "title_font_size_pt": 12,
    "toc_font_size_pt": 9,
    "line_height": 1.2,
    "font_family": "Arial",
}


def _uris(pdf: bytes) -> list[str]:
    document = fitz.open(stream=pdf, filetype="pdf")
    found: list[str] = []
    for page in document:
        for link in page.get_links():
            uri = str(link.get("uri") or "")
            if uri:
                found.append(uri)
    return found


class AddressBookPhoneLinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        set_request_db_path(Path(self._tempdir.name) / "app.sqlite3")
        initialize_database()

    def tearDown(self) -> None:
        set_request_db_path(None)
        self._tempdir.cleanup()

    def test_home_phone_keeps_call_link_without_texting_link(self) -> None:
        pdf = build_address_book_pdf(
            fields=[
                {
                    "name": "AL Central",
                    "meetings": [
                        {
                            "name": "Birmingham (GRAHAM)",
                            "home_last_name": "GRAHAM",
                            "contacts": [
                                {
                                    "name": "GRAHAM, Mark",
                                    "given_name": "Mark",
                                    "family_name": "GRAHAM",
                                    "phones": [
                                        {"value": "205-555-0000", "type": "home"},
                                        {"value": "205-555-1212", "type": "mobile"},
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
            meeting_preview_sections=[],
            settings={"meeting_page_mode": "one_meeting", "include_bible_study_union_info": 0},
            layout=_LAYOUT,
            print_order_mode="alphabetical",
            print_order_json="",
        )
        uris = _uris(pdf)
        self.assertIn("tel://2055550000", uris)
        self.assertNotIn("sms://2055550000", uris)
        self.assertIn("tel://2055551212", uris)
        self.assertIn("sms://2055551212", uris)
