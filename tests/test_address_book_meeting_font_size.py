import json
import tempfile
import unittest
from pathlib import Path

import fitz

from app.database import initialize_database, set_request_db_path
from app.services.address_book_pdf_service import build_address_book_pdf
from app.services.field_list_service import (
    get_address_book_settings,
    meeting_font_size_override,
    meeting_line_height_key,
    parse_meeting_font_size_map,
    remap_meeting_font_size_assignments,
    save_address_book_settings,
    serialize_meeting_font_size_map,
)


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


def _contact(name: str) -> dict:
    return {"name": name}


def _meeting(name: str, contacts: list[str], font_size_pt: float = 0) -> dict:
    payload = {
        "name": name,
        "home_last_name": "",
        "contacts": [_contact(item) for item in contacts],
    }
    if font_size_pt:
        payload["font_size_pt"] = font_size_pt
    return payload


def _fields(alpha_size: float = 0, beta_size: float = 0) -> list[dict]:
    return [
        {
            "name": "AL Central",
            "meetings": [
                _meeting("Alpha (HOME)", ["Ada One", "Ada Two"], alpha_size),
                _meeting("Beta (HOME)", ["Bea One", "Bea Two"], beta_size),
            ],
        }
    ]


def _build(fields: list[dict], settings: dict | None = None) -> bytes:
    defaults = {"meeting_page_mode": "one_meeting", "include_bible_study_union_info": 0}
    defaults.update(settings or {})
    return build_address_book_pdf(
        fields=fields,
        meeting_preview_sections=[],
        settings=defaults,
        layout=_LAYOUT,
        print_order_mode="alphabetical",
        print_order_json="",
    )


def _text_sizes(pdf: bytes) -> list[tuple[int, str, float]]:
    document = fitz.open(stream=pdf, filetype="pdf")
    items: list[tuple[int, str, float]] = []
    for page_index, page in enumerate(document):
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
                sizes = [float(span.get("size") or 0) for span in line.get("spans", [])]
                if text and sizes:
                    items.append((page_index, text, max(sizes)))
    document.close()
    return items


def _first_item(items: list[tuple[int, str, float]], name: str) -> tuple[int, float]:
    for page_index, text, size in items:
        if name in text:
            return page_index, size
    raise AssertionError(f"missing {name!r}")


class AddressBookMeetingFontSizeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        set_request_db_path(Path(self._tempdir.name) / "app.sqlite3")
        initialize_database()

    def tearDown(self) -> None:
        set_request_db_path(None)
        self._tempdir.cleanup()

    def test_zero_and_missing_inherit_book(self) -> None:
        self.assertEqual(parse_meeting_font_size_map(""), {})
        self.assertEqual(parse_meeting_font_size_map('{"AL Central||Alpha": 0}'), {})
        self.assertEqual(meeting_font_size_override("", "AL Central", "Alpha"), 0.0)
        self.assertEqual(
            serialize_meeting_font_size_map(parse_meeting_font_size_map('{"AL Central||Alpha": 9}')),
            json.dumps({meeting_line_height_key("AL Central", "Alpha"): 9.0}, separators=(",", ":")),
        )

    def test_save_override_leaves_book_font_size(self) -> None:
        save_address_book_settings({"base_font_size_pt": 10})
        save_address_book_settings(
            {
                "meeting_font_size_json": json.dumps(
                    {meeting_line_height_key("AL Central", "Alpha (HOME)"): 9}
                )
            }
        )
        settings = get_address_book_settings()
        self.assertAlmostEqual(float(settings["base_font_size_pt"]), 10)
        self.assertAlmostEqual(
            meeting_font_size_override(
                settings["meeting_font_size_json"],
                "AL Central",
                "Alpha (HOME)",
            ),
            9,
        )

    def test_remap_keys_on_field_and_meeting_rename(self) -> None:
        save_address_book_settings(
            {
                "meeting_font_size_json": json.dumps(
                    {
                        meeting_line_height_key("AL Central", "Alpha (HOME)"): 9,
                        meeting_line_height_key("LA South", "Alpha (HOME)"): 8,
                    }
                )
            }
        )
        remap_meeting_font_size_assignments("meeting", "Alpha (HOME)", "Alpha (NEW)")
        settings = get_address_book_settings()
        self.assertAlmostEqual(
            meeting_font_size_override(settings["meeting_font_size_json"], "AL Central", "Alpha (NEW)"),
            9,
        )
        self.assertAlmostEqual(
            meeting_font_size_override(settings["meeting_font_size_json"], "LA South", "Alpha (NEW)"),
            8,
        )
        self.assertEqual(
            meeting_font_size_override(settings["meeting_font_size_json"], "AL Central", "Alpha (HOME)"),
            0.0,
        )

        remap_meeting_font_size_assignments("field", "AL Central", "AL North")
        settings = get_address_book_settings()
        self.assertAlmostEqual(
            meeting_font_size_override(settings["meeting_font_size_json"], "AL North", "Alpha (NEW)"),
            9,
        )
        self.assertEqual(
            meeting_font_size_override(settings["meeting_font_size_json"], "AL Central", "Alpha (NEW)"),
            0.0,
        )

    def test_pdf_inherits_book_font_size(self) -> None:
        items = _text_sizes(_build(_fields()))
        _, alpha_size = _first_item(items, "Ada One")
        _, beta_size = _first_item(items, "Bea One")
        self.assertAlmostEqual(alpha_size, 7, delta=0.15)
        self.assertAlmostEqual(beta_size, alpha_size, delta=0.15)

    def test_pdf_override_changes_only_that_meeting(self) -> None:
        items = _text_sizes(_build(_fields(alpha_size=9)))
        _, alpha_size = _first_item(items, "Ada One")
        _, beta_size = _first_item(items, "Bea One")
        self.assertAlmostEqual(alpha_size, 9, delta=0.15)
        self.assertAlmostEqual(beta_size, 7, delta=0.15)

    def test_complete_meetings_page_keeps_mixed_sizes(self) -> None:
        items = _text_sizes(
            _build(
                _fields(alpha_size=9),
                {"meeting_page_mode": "complete_meetings"},
            )
        )
        alpha_page, alpha_size = _first_item(items, "Ada One")
        beta_page, beta_size = _first_item(items, "Bea One")
        self.assertEqual(alpha_page, beta_page)
        self.assertAlmostEqual(alpha_size, 9, delta=0.15)
        self.assertAlmostEqual(beta_size, 7, delta=0.15)
