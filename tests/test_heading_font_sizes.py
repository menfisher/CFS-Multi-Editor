import unittest

from app.services.address_book_pdf_service import (
    _IndexEntry,
    _TocEntry,
    _build_index_pages,
    _build_toc_pages,
    _heading_font_sizes,
    _text_width,
)


class HeadingFontSizeTests(unittest.TestCase):
    def test_explicit_sizes_win_over_compact_fallback(self) -> None:
        title_size, toc_size = _heading_font_sizes(6.0, True, 10.0, 8.5)
        self.assertEqual(title_size, 10.0)
        self.assertEqual(toc_size, 8.5)

    def test_missing_sizes_use_compact_base(self) -> None:
        title_size, toc_size = _heading_font_sizes(6.0, True, None, None)
        self.assertEqual(title_size, 6.0)
        self.assertEqual(toc_size, 6.0)

    def test_toc_page_draws_title_and_items_at_requested_sizes(self) -> None:
        pages = _build_toc_pages(
            entries=[_TocEntry("Field A", "Meeting 1", 1)],
            book_title="Address Book",
            page_width=250,
            page_height=360,
            margin_left=10,
            margin_right=10,
            margin_top=10,
            margin_bottom=10,
            base_font_size=6.0,
            page_offset=0,
            compact=True,
            font_family="Arial",
            title_font_size=10.0,
            toc_font_size=8.5,
        )
        self.assertTrue(pages)
        title_lines = [line for line in pages[0].lines if line.bold]
        item_lines = [line for line in pages[0].lines if "Meeting 1" in line.text]
        self.assertTrue(title_lines)
        self.assertEqual(title_lines[0].size, 10.0)
        self.assertTrue(item_lines)
        self.assertEqual(item_lines[0].size, 8.5)

    def test_index_page_uses_index_font_size(self) -> None:
        pages = _build_index_pages(
            entries=[_IndexEntry(name="Abashian, Marianna", page=8, target_y=100.0)],
            page_width=250,
            page_height=360,
            margin_left=10,
            margin_right=10,
            margin_top=10,
            margin_bottom=10,
            base_font_size=6.0,
            start_page_number=20,
            compact=True,
            font_family="Arial",
            title_font_size=12.0,
            toc_font_size=9.0,
            index_font_size=6.5,
        )
        self.assertTrue(pages)
        title_lines = [line for line in pages[0].lines if line.text == "Index"]
        name_lines = [line for line in pages[0].lines if "Abashian" in line.text]
        self.assertTrue(title_lines)
        self.assertEqual(title_lines[0].size, 12.0)
        self.assertTrue(name_lines)
        self.assertEqual(name_lines[0].size, 6.5)

    def test_index_leader_dots_reach_the_page_number(self) -> None:
        pages = _build_index_pages(
            entries=[_IndexEntry(name="Abashian, Marianna", page=8, target_y=100.0)],
            page_width=250,
            page_height=360,
            margin_left=10,
            margin_right=10,
            margin_top=10,
            margin_bottom=10,
            base_font_size=6.0,
            start_page_number=20,
            compact=True,
            font_family="Arial",
            title_font_size=12.0,
            toc_font_size=9.0,
            index_font_size=6.5,
        )
        dots = next(line for line in pages[0].lines if line.text and set(line.text) == {"."})
        number = next(line for line in pages[0].lines if line.text == "8")
        dots_end = dots.x + _text_width(dots.text, dots.size, "Arial")
        self.assertLessEqual(dots_end, number.x + 0.25)
        self.assertLess(number.x - dots_end, dots.size)
