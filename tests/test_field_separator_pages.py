import base64
import re
import tempfile
import unittest
from pathlib import Path

import fitz

from app.database import initialize_database, set_request_db_path
from app.services.address_book_pdf_service import (
    _PAGE_FRAME_SIDE_INSET_PT,
    build_address_book_pdf,
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


def _page_texts(pdf: bytes) -> list[str]:
    document = fitz.open(stream=pdf, filetype="pdf")
    return [page.get_text() for page in document]


def _tiny_cover_jpeg() -> tuple[str, int, int]:
    document = fitz.open()
    page = document.new_page(width=32, height=32)
    image = page.get_pixmap()
    payload = base64.b64encode(image.tobytes("jpeg")).decode("ascii")
    document.close()
    return f"data:image/jpeg;base64,{payload}", 32, 32


def _fields() -> list[dict]:
    return [
        {
            "name": "AL Central",
            "meetings": [
                {
                    "name": "Birmingham (GRAHAM)",
                    "home_last_name": "GRAHAM",
                    "contacts": [
                        {"name": "Ada Lovelace"},
                        {
                            "name": "GRAHAM, Mark & Stephanie",
                            "given_name": "Mark",
                            "family_name": "GRAHAM",
                            "mtg_home_elder_flag": "2",
                            "phones": [
                                {"value": "205-555-0000", "type": "home"},
                                {"value": "205-555-1212", "type": "mobile", "owner_given_name": "Mark"},
                                {"value": "205-555-9999", "type": "mobile", "owner_given_name": "Stephanie"},
                            ],
                        },
                    ],
                },
                {
                    "name": "MS S & AL Coast, New Orleans (GOODWIN)",
                    "home_last_name": "GOODWIN",
                    "contacts": [
                        {
                            "name": "GOODWIN, Jane",
                            "given_name": "Jane",
                            "family_name": "GOODWIN",
                            "mtg_home_elder_flag": "2",
                            "phones": [{"value": "228-555-7777", "type": "mobile"}],
                        },
                    ],
                },
                {
                    "name": "Opelika (MASK)",
                    "home_last_name": "MASK",
                    "contacts": [
                        {"name": "Alan Turing"},
                        {
                            "name": "MASK, John & Mary",
                            "given_name": "John",
                            "family_name": "MASK",
                            "mtg_home_elder_flag": "2",
                            "phones": [{"value": "334-555-3434", "type": "home"}],
                        },
                    ],
                },
            ],
        },
        {
            "name": "LA South",
            "meetings": [
                {
                    "name": "Lake Charles (SAVOY)",
                    "home_last_name": "SAVOY",
                    "contacts": [
                        {"name": "Grace Hopper"},
                        {
                            "name": "SAVOY, James",
                            "given_name": "James",
                            "family_name": "Savoy",
                            "mtg_home_elder_flag": "2",
                            "phones": [
                                {"value": "337-555-1111", "type": "work"},
                                {"value": "337-555-5656", "type": "cell"},
                            ],
                        },
                    ],
                },
            ],
        },
    ]


def _build(settings: dict, **kwargs) -> bytes:
    defaults = {"meeting_page_mode": "one_meeting", "include_bible_study_union_info": 0}
    defaults.update(settings)
    return build_address_book_pdf(
        fields=_fields(),
        meeting_preview_sections=[],
        settings=defaults,
        layout=_LAYOUT,
        print_order_mode="alphabetical",
        print_order_json="",
        **kwargs,
    )


def _numbered_footers(texts: list[str]) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    pattern = re.compile(r"^(Table of Contents|.+?) - Page (\d+)\s*$", re.MULTILINE)
    for text in texts:
        matches = pattern.findall(text)
        if not matches:
            continue
        label, number = matches[-1]
        found.append((label.strip(), int(number)))
    return found


class FieldSeparatorPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        set_request_db_path(Path(self._tempdir.name) / "app.sqlite3")
        initialize_database()

    def tearDown(self) -> None:
        set_request_db_path(None)
        self._tempdir.cleanup()

    def test_content_page_numbers_follow_toc_without_skipping(self) -> None:
        footers = _numbered_footers(_page_texts(_build({})))
        toc_count = sum(1 for label, _ in footers if label == "Table of Contents")
        self.assertGreaterEqual(toc_count, 1)
        content = [(label, number) for label, number in footers if label != "Table of Contents"]
        self.assertTrue(content)
        self.assertEqual(content[0][1], toc_count + 1)
        numbers = [number for _, number in footers]
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)))

    def test_cover_does_not_skip_a_numbered_page(self) -> None:
        data, width, height = _tiny_cover_jpeg()
        footers = _numbered_footers(
            _page_texts(
                _build(
                    {},
                    cover_image_data=data,
                    cover_image_width=width,
                    cover_image_height=height,
                )
            )
        )
        toc_count = sum(1 for label, _ in footers if label == "Table of Contents")
        content = [(label, number) for label, number in footers if label != "Table of Contents"]
        self.assertEqual(content[0][1], toc_count + 1)
        numbers = [number for _, number in footers]
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)))

    def test_separator_page_lists_field_and_meetings_and_is_numbered(self) -> None:
        texts = _page_texts(_build({"field_separator_pages": 1}))
        footers = _numbered_footers(texts)
        toc_count = sum(1 for label, _ in footers if label == "Table of Contents")
        separator_pages = [
            text
            for text in texts
            if "Table of Contents" not in text
            and "Ada Lovelace" not in text
            and "Grace Hopper" not in text
            and "Index - Page" not in text
        ]
        self.assertTrue(separator_pages)
        first_separator = separator_pages[0]
        self.assertIn("AL Central", first_separator)
        self.assertIn("Birmingham", first_separator)
        self.assertIn("Opelika", first_separator)
        self.assertIn("MS S & AL Coast, New Orleans", first_separator)
        self.assertIn("Mark Graham", first_separator)
        self.assertIn("John Mask", first_separator)
        self.assertIn("Jane Goodwin", first_separator)
        self.assertNotIn("Stephanie", first_separator)
        self.assertNotIn("GRAHAM", first_separator)
        self.assertIn("205-555-1212", first_separator)
        self.assertNotIn("205-555-0000", first_separator)
        self.assertNotIn("205-555-9999", first_separator)
        self.assertIn("334-555-3434", first_separator)
        self.assertNotIn("Ada Lovelace", first_separator)
        self.assertRegex(first_separator, r"AL Central - Page \d+")
        content = [(label, number) for label, number in footers if label != "Table of Contents"]
        self.assertTrue(content)
        self.assertEqual(content[0][1], toc_count + 1)
        first_meeting = next(index for index, text in enumerate(texts) if "Ada Lovelace" in text)
        separator_index = next(index for index, text in enumerate(texts) if text == first_separator)
        self.assertGreater(first_meeting, separator_index)
        numbers = [number for _, number in footers]
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)))
        second = next(
            text
            for text in separator_pages
            if "LA South" in text and "Lake Charles" in text
        )
        self.assertIn("James Savoy", second)
        self.assertIn("337-555-5656", second)
        self.assertNotIn("337-555-1111", second)
        self.assertNotIn("Grace Hopper", second)
        self.assertRegex(second, r"LA South - Page \d+")

    def test_separator_page_aligns_meetings_elders_and_phones(self) -> None:
        document = fitz.open(stream=_build({"field_separator_pages": 1}), filetype="pdf")
        page = next(item for item in document if "Mark Graham" in item.get_text() and "Ada Lovelace" not in item.get_text())
        words = page.get_text("words")
        left_margin = (_LAYOUT["margin_left_in"] * 72) + _PAGE_FRAME_SIDE_INSET_PT
        right_margin = page.rect.width - (_LAYOUT["margin_right_in"] * 72) - _PAGE_FRAME_SIDE_INSET_PT
        title_top = min(word[1] for word in words if word[4] == "AL" or word[4] == "Central")
        self.assertAlmostEqual(title_top, 0.375 * 72, delta=14)
        birmingham = next(word for word in words if word[4] == "Birmingham")
        opelika = next(word for word in words if word[4] == "Opelika")
        self.assertAlmostEqual(birmingham[0], left_margin, delta=2)
        self.assertAlmostEqual(opelika[0], left_margin, delta=2)
        graham = next(word for word in words if word[4] == "Mark")
        mask = next(word for word in words if word[4] == "John")
        self.assertAlmostEqual(graham[0], mask[0], delta=2)
        self.assertGreater(graham[0], birmingham[0] + 4)
        self.assertGreater(graham[1], birmingham[1])
        graham_phone = next(word for word in words if "205-555-1212" in word[4])
        mask_phone = next(word for word in words if "334-555-3434" in word[4])
        self.assertAlmostEqual(graham_phone[2], right_margin, delta=3)
        self.assertAlmostEqual(mask_phone[2], right_margin, delta=3)
        self.assertAlmostEqual(graham_phone[1], graham[1], delta=2)
        digit_marks = [
            word
            for word in words
            if word[4].isdigit() and len(word[4]) <= 2 and word[1] < birmingham[1] + 80
        ]
        self.assertFalse(digit_marks)
        graham_dots = [
            word
            for word in words
            if set(word[4]) <= {"."} and len(word[4]) > 1 and abs(word[1] - graham_phone[1]) < 2
        ]
        self.assertTrue(graham_dots)
        self.assertLess(graham_phone[0] - max(word[2] for word in graham_dots), 6)
        sizes = [
            span["size"]
            for block in page.get_text("dict")["blocks"]
            if block.get("type") == 0
            for line in block.get("lines", [])
            for span in line.get("spans", [])
            if "Birmingham" in span.get("text", "") or "Mark Graham" in span.get("text", "")
        ]
        self.assertTrue(sizes)
        self.assertTrue(all(abs(size - _LAYOUT["toc_font_size_pt"]) < 0.6 for size in sizes))

    def test_meeting_pages_have_no_corner_page_marks(self) -> None:
        document = fitz.open(stream=_build({"field_separator_pages": 1}), filetype="pdf")
        meeting_page = next(item for item in document if "Ada Lovelace" in item.get_text())
        words = meeting_page.get_text("words")
        top_digits = [
            word
            for word in words
            if word[4].isdigit() and len(word[4]) <= 2 and word[1] < 40
        ]
        self.assertFalse(top_digits)

    def test_separator_pages_draw_a_margin_frame_around_the_text(self) -> None:
        document = fitz.open(stream=_build({"field_separator_pages": 1}), filetype="pdf")
        separator = next(
            item for item in document if "Mark Graham" in item.get_text() and "Ada Lovelace" not in item.get_text()
        )
        meeting_page = next(item for item in document if "Ada Lovelace" in item.get_text())
        left_edge = _LAYOUT["margin_left_in"] * 72
        right_edge = separator.rect.width - (_LAYOUT["margin_right_in"] * 72)
        top_edge = _LAYOUT["margin_top_in"] * 72

        def tall_verticals(page) -> list[float]:
            xs: list[float] = []
            for drawing in page.get_drawings():
                for item in drawing.get("items") or []:
                    if item[0] != "l":
                        continue
                    start, end = item[1], item[2]
                    if abs(start.x - end.x) < 0.6 and abs(start.y - end.y) > 80:
                        xs.append(start.x)
            return xs

        separator_xs = tall_verticals(separator)
        self.assertTrue(any(abs(value - left_edge) < 1.5 for value in separator_xs))
        self.assertTrue(any(abs(value - right_edge) < 1.5 for value in separator_xs))
        separator_ys = [
            drawing["rect"].y0
            for drawing in separator.get_drawings()
            if drawing.get("rect") is not None
        ]
        self.assertTrue(any(abs(value - top_edge) < 1.5 for value in separator_ys))
        birmingham = next(word for word in separator.get_text("words") if word[4] == "Birmingham")
        self.assertGreater(birmingham[0], left_edge + 3)
        self.assertLess(birmingham[2], right_edge - 3)
        self.assertFalse(tall_verticals(meeting_page))
