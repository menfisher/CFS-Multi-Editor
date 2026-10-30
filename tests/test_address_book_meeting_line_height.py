import json
import tempfile
import unittest
from pathlib import Path

import fitz

from app.database import initialize_database, set_request_db_path
from app.services.address_book_pdf_service import build_address_book_pdf
from app.services.field_list_service import (
    get_address_book_settings,
    meeting_line_height_key,
    meeting_line_height_override,
    parse_meeting_line_height_map,
    remap_meeting_line_height_assignments,
    save_address_book_settings,
    serialize_meeting_line_height_map,
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


def _meeting(name: str, contacts: list[str], line_height: float = 0) -> dict:
    payload = {
        "name": name,
        "home_last_name": "",
        "contacts": [_contact(item) for item in contacts],
    }
    if line_height:
        payload["line_height"] = line_height
    return payload


def _fields(alpha_height: float = 0, beta_height: float = 0) -> list[dict]:
    return [
        {
            "name": "AL Central",
            "meetings": [
                _meeting("Alpha (HOME)", ["Ada One", "Ada Two"], alpha_height),
                _meeting("Beta (HOME)", ["Bea One", "Bea Two"], beta_height),
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


def _text_items(pdf: bytes) -> list[tuple[int, str, float]]:
    document = fitz.open(stream=pdf, filetype="pdf")
    items: list[tuple[int, str, float]] = []
    for page_index, page in enumerate(document):
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
                if text:
                    items.append((page_index, text, float(line["bbox"][1])))
    document.close()
    return items


def _first_item(items: list[tuple[int, str, float]], name: str) -> tuple[int, float]:
    for page_index, text, y in items:
        if name in text:
            return page_index, y
    raise AssertionError(f"missing {name!r}")


def _contact_gap(items: list[tuple[int, str, float]], first: str, second: str) -> float:
    page_a, y_a = _first_item(items, first)
    page_b, y_b = _first_item(items, second)
    if page_a != page_b:
        raise AssertionError(f"{first!r} and {second!r} are on different pages")
    return y_b - y_a


class AddressBookMeetingLineHeightTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        set_request_db_path(Path(self._tempdir.name) / "app.sqlite3")
        initialize_database()

    def tearDown(self) -> None:
        set_request_db_path(None)
        self._tempdir.cleanup()

    def test_zero_and_missing_inherit_book(self) -> None:
        self.assertEqual(parse_meeting_line_height_map(""), {})
        self.assertEqual(parse_meeting_line_height_map('{"AL Central||Alpha": 0}'), {})
        self.assertEqual(meeting_line_height_override("", "AL Central", "Alpha"), 0.0)
        self.assertEqual(
            serialize_meeting_line_height_map(parse_meeting_line_height_map('{"AL Central||Alpha": 1.05}')),
            json.dumps({meeting_line_height_key("AL Central", "Alpha"): 1.05}, separators=(",", ":")),
        )

    def test_save_override_leaves_book_line_height(self) -> None:
        save_address_book_settings({"line_height": 1.2})
        save_address_book_settings(
            {
                "meeting_line_height_json": json.dumps(
                    {meeting_line_height_key("AL Central", "Alpha (HOME)"): 1.05}
                )
            }
        )
        settings = get_address_book_settings()
        self.assertAlmostEqual(float(settings["line_height"]), 1.2)
        self.assertAlmostEqual(
            meeting_line_height_override(
                settings["meeting_line_height_json"],
                "AL Central",
                "Alpha (HOME)",
            ),
            1.05,
        )

    def test_remap_keys_on_field_and_meeting_rename(self) -> None:
        save_address_book_settings(
            {
                "meeting_line_height_json": json.dumps(
                    {
                        meeting_line_height_key("AL Central", "Alpha (HOME)"): 1.05,
                        meeting_line_height_key("LA South", "Alpha (HOME)"): 1.1,
                    }
                )
            }
        )
        remap_meeting_line_height_assignments("meeting", "Alpha (HOME)", "Alpha (NEW)")
        settings = get_address_book_settings()
        self.assertAlmostEqual(
            meeting_line_height_override(settings["meeting_line_height_json"], "AL Central", "Alpha (NEW)"),
            1.05,
        )
        self.assertAlmostEqual(
            meeting_line_height_override(settings["meeting_line_height_json"], "LA South", "Alpha (NEW)"),
            1.1,
        )
        self.assertEqual(
            meeting_line_height_override(settings["meeting_line_height_json"], "AL Central", "Alpha (HOME)"),
            0.0,
        )

        remap_meeting_line_height_assignments("field", "AL Central", "AL North")
        settings = get_address_book_settings()
        self.assertAlmostEqual(
            meeting_line_height_override(settings["meeting_line_height_json"], "AL North", "Alpha (NEW)"),
            1.05,
        )
        self.assertEqual(
            meeting_line_height_override(settings["meeting_line_height_json"], "AL Central", "Alpha (NEW)"),
            0.0,
        )

    def test_pdf_inherits_book_line_height(self) -> None:
        items = _text_items(_build(_fields()))
        alpha_gap = _contact_gap(items, "Ada One", "Ada Two")
        beta_gap = _contact_gap(items, "Bea One", "Bea Two")
        self.assertGreater(alpha_gap, 0)
        self.assertAlmostEqual(beta_gap, alpha_gap, delta=0.4)

    def test_pdf_override_tightens_only_that_meeting(self) -> None:
        items = _text_items(_build(_fields(alpha_height=1.05)))
        alpha_gap = _contact_gap(items, "Ada One", "Ada Two")
        beta_gap = _contact_gap(items, "Bea One", "Bea Two")
        self.assertAlmostEqual(alpha_gap / beta_gap, 1.05 / 1.2, delta=0.05)
        self.assertLess(alpha_gap, beta_gap - 0.4)

    def test_complete_meetings_page_keeps_mixed_spacing(self) -> None:
        items = _text_items(
            _build(
                _fields(alpha_height=1.05),
                {"meeting_page_mode": "complete_meetings"},
            )
        )
        alpha_page, _ = _first_item(items, "Ada One")
        beta_page, _ = _first_item(items, "Bea One")
        self.assertEqual(alpha_page, beta_page)
        alpha_gap = _contact_gap(items, "Ada One", "Ada Two")
        beta_gap = _contact_gap(items, "Bea One", "Bea Two")
        self.assertLess(alpha_gap, beta_gap - 0.4)
